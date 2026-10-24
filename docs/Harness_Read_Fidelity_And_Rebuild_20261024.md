# 원문 가시성·재작성 횟수 개선 구현 기록 (헤르메스 재조사 반영)

2026-10-24. LM Studio 마지막 감사 실행(`1792461970478`)과 로컬 헤르메스 세션(`%LOCALAPPDATA%/hermes/state.db`의 `20261023_024423_c7ea9d`)을 읽기 전용으로 대조한 뒤 구현했다. 자동 검사는 마쳤다. **로컬 설치와 실모델 재실행은 하지 않았다.** 사용자 요청에 따라 새 계측 도구는 범위에서 뺐다.

## 근거

| | 헤르메스 (Qwen3.8-27B, 자체 llama.cpp, ctx 65,536) | 이 하네스 (LM Studio, ctx 84,224) |
|---|---|---|
| 소요 | 약 68분 | 약 11.9분 |
| 읽기 | 도구 호출 125회, 파일 48개, 반환 8,274줄 | 도구 호출 35회, 파일 11개, 1,368줄 |
| 방식 | 파일 전체를 `LINE\|code`로 읽음 | 소스 전체 읽기 거부, 8 KB에 맞춰 약 100줄씩 |
| 압축 | context 초과 후 8회, 모델 요약에 17.6분, 압축 후 재읽기 36회 | projection 2회 + 압축 1회 |

- **속도**: prefill 합계는 292s였다. 그중 캐시가 깨진 4라운드가 214s를 차지했다.
  - r12 지침 제외: 71.7s
  - r13 projection: 47.9s
  - r15 projection: 58.6s
  - r16 압축: 35.6s
  - hybrid-attention 모델은 앞부분이 바뀌면 전체를 다시 처리하므로, 핵심 비용은 재작성 횟수다.
- **정확도**: 실제 projection 하나는 2,852자였고 그중 코드는 559자였다. 남은 코드는 파일 앞부분이었고, 모델이 나중에 잘못 인용한 줄은 남아 있지 않았다.

## 변경

1. **지침을 따로 빼지 않기**: [round-input.ts](../lmstudio-context-compactor-plugin/src/round-input.ts), [context-manager.ts](../lmstudio-context-compactor-plugin/src/context-manager.ts)
   - 보존 중인 지침의 측정 토큰을 `shouldProjectEvidence` 판정에 더한다.
   - 지침이 high water를 넘기게 되는 라운드에서는 같은 라운드에 근거를 projection하고 지침을 유지한다. 재작성은 1회다.
   - 기존 재진입 조건(입력이 다시 쓰인 라운드, 설정 low water 이하)은 유지한다.
2. **projection 객체 축소**: [working-context.js](../lmstudio-context-compactor-plugin/src/working-context.js)
   - 본문 projection에서 빼는 것:
     - 다른 필드로 대체되는 digest: `semanticQueryDigest`, `sourceHash`
     - `sourceIdentity`의 프로젝트 경로와 workspace hash (`sourceIdentityDigest`가 key를 대신한다)
     - 비어 있는 envelope range 배열, `"unknown"` 값, `archiveRef`의 상태 필드
   - 모델이 읽는 사실은 모두 유지한다: 다음 페이지·총계·읽은 시점 coverage·범위/증명 facts, `currentFile` / `grantsMutation` / `fullRawProvided`.
   - 이번 실행의 21개 읽기 결과로 측정하면 projection은 약 2,165자다. 합계는 59.7k에서 46.7k로 줄었다.
3. **인용한 줄 남기기 + 오래된 reasoning 정리**
   - 이후 assistant 텍스트나 도구 인자가 가리킨 줄(±1)을 앞 512자 대신 남긴다(최대 1,024자). 인용 형식은 두 가지다.
     - `path:line[-line]`
     - 앞서 나온 파일명 뒤의 `Line N` — 같은 이름의 헤더/소스 짝에도 적용된다.
   - notice는 `CITED LINES …` 형식이다.
   - 이번 실행의 r13 직전 기록을 재생하면 Character.cpp 301-420 projection이 335-344행을 유지한다. 모델이 잘못 인용한 342행 `PlaySoundAtLocation(..., Start)`가 여기에 포함된다.
   - projection 라운드에서만 기존 `boundPastReasoning`을 적용한다(기본 2,048토큰, 최신 것부터 유지). 그 라운드는 어차피 재작성되므로 캐시를 추가로 깨지 않는다.
   - 같은 라운드에 압축된 범위의 알림 문구를 "N of M chars shown"으로 바꿨다.
4. **작은 소스는 한 번에 읽기**: [direct-read-capabilities.js](../lmstudio-unreal-agent-mcp/src/direct-read-capabilities.js)
   - 24 KB 이하 프로젝트 소스의 `read_file`은 `read_file_range 1..N`과 같은 줄 번호 payload를 반환한다(`DIRECT_WHOLE_SOURCE_READ_MAX_BYTES`, 0이면 끔).
   - 엔진 소스와 더 큰 소스는 기존처럼 locate-first다. 거부할 때 `totalLines`를 반환한다.
   - 도구 설명은 catalog 예산(16,384자) 안에서 바꿨다.
5. **읽기 예산 기본값**: [stable_tool_manifest.json](../config/stable_tool_manifest.json), [budget-broker.ts](../lmstudio-context-compactor-plugin/src/budget-broker.ts)
   - unreal-agent 기본 `byteBudget`을 8,192에서 24,576으로 올렸다.
   - byteBudget을 생략한 읽기는 batch 잔여의 절반까지만 받고, 8 KB 아래로는 내려가지 않는다. 그래서 넓은 context에서는 24 KB 읽기가 되고, 좁은 context에서도 병렬 읽기가 들어간다.
   - 도구가 자기 default를 공개한 경우와 명시한 값은 바뀌지 않는다.
6. **감사 요청의 도구 노출**: [tool-capability-registry.ts](../lmstudio-context-compactor-plugin/src/tool-capability-registry.ts), [prediction-loop.ts](../lmstudio-context-compactor-plugin/src/prediction-loop.ts)
   - 조건: 최신 사용자 메시지가 검토·찾기·설명 요청이고 수정·빌드·실행 표현이 없다. "수정하지 말고" 같은 부정 표현은 먼저 제거한다.
   - 이때는 manifest의 `mutation` 도구만 빼고 노출한다: `write_file`, `replace_in_file`, `apply_edit_bundle`, `delete_file`, `set_active_project`, `unreal_set_active_project`.
   - validator·long-running 검사·다른 provider 도구는 유지한다. bounded 모드 제한은 적용하지 않는다.
   - 생성기가 mutation 이름을 함께 내보낸다([build-tool-contract.cjs](../lmstudio-context-compactor-plugin/scripts/build-tool-contract.cjs)).
   - `direct_runtime_identity.toolProfile`에 선택된 프로필을 기록한다.

헤르메스식 모델 요약 압축, 오류 후 대응식 압축, 압축 후 전체 재읽기, 웹 검색은 도입하지 않았다. 감사 지침 문구와 read envelope 필드도 바꾸지 않았다.

## 검증

| 검사 | 결과 |
|---|---|
| Compactor 전체 Node 테스트 | **886 pass / 0 fail**. 새 정책에 맞춘 기대값 수정: 영구 제외 대신 같은 라운드 projection, 8 KB 고정 기본값, 빈 `projectedRawRanges` |
| build, runtime identity / guidance / tool contract / unity contract check | 통과 |
| Unreal agent MCP 테스트 | 337 pass / 0 fail. 이 세션의 PATH로 실행하면 `cmd`가 `ping`을 찾지 못해 1건(build timeout)이 실패하며, PATH를 정리한 프로세스에서는 통과. 소스 거부 테스트 2개와 stdio 테스트를 새 기준으로 수정 |
| Python 계약·패키지·trace·경로 테스트 | 81 pass / 1 fail. 실패는 HEAD에 이미 있던 `docs/VERSIONING.md` → `Harness_Audit_Accuracy_Implementation_20261020.md` 패키지 링크 누락(커밋 a3bddd6). 이번 변경과 무관 |
| 실제 기록 재생(스크립트, 저장소 외) | projection 크기·인용 줄 유지를 위 수치로 확인 |

**미확인**:
- 실모델에서 재작성 횟수와 prefill 시간이 줄었는지(이번 실행: 4회 / 214s)
- 읽은 파일 수와 줄 수가 늘었는지
- 인용 불일치(이번: 2건)와 오탐이 줄었는지
- 24 KB 기본 읽기가 입력 증가 속도에 주는 영향

위 항목은 설치 후 같은 요청을 실행해 기존 debug 기록으로 비교한다. 1회 결과로 개선을 확정하지 않는다.
