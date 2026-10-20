# 감사 오탐·검증 복구 후속 구현 기록

2026-10-20. [Opus 인계 계획](Opus_Audit_Accuracy_Handoff_20261020.md)을 사용자의 `너가 진행해` 요청으로 실행했다. 소스 후보 구현과 해당 자동 검사를 마친 뒤, 후속 `업데이트 진행` 요청으로 **로컬 업데이트 설치도 완료**했다. 실모델 정확도·성능 비교와 GUI 활성화 확인은 미완료다. 공개 릴리즈는 1.4.1로 유지한다.

## 기준 보존과 실제 실행 식별

HEAD `eb476dfc4b4f25bfdd62e5193bc00c8ae1523ad5`의 main에는 이전에 설치한 미커밋 변경이 있다. 이를 HEAD로 대체하지 않고 `%USERPROFILE%/.evidence-first/evaluations/audit-accuracy-20261020/baseline`에 변경 파일, 플러그인 src/scripts/dist, 지침, evidence-first와 SHA-256 목록, binary diff를 보존했다. 기존 변경과 실제 게임 checkout은 보존했다.

| 구분 | 버전/식별 |
|---|---|
| 보존한 비교 기준 | Compactor 0.4.81 / revision 128, evidence-first 1.1.3, payload 2.1.31 |
| 기준 build ID | `ef-build-v2-d182c5b0a4d6c59ad5bc571e80b27a43c017707c61a604d69b8239eb7aad5459` |
| 후속 로컬 설치 | Compactor 0.4.82 / revision 129, evidence-first 1.1.4, payload 2.1.32 |
| 후보 build ID | `ef-build-v2-ee37966c3c47c706589e019c100e372850552816ac23cd84823fecf0aa02b051` |
| 실제 감사 모델 | `swift-1.5-qwen3.8-27b` |
| 실제 프로젝트 / 합성 평가 엔진 | UE 5.7.4 / UE 5.8.2 CL 56702186, 서로 분리 |

첨부와 일치하는 원본은 `%USERPROFILE%/.lmstudio/conversations/1792458755676.conversation.json`의 메시지 index 3이다. 조사 시 파일 SHA-256은 `63f87b4fbc61096fef3a825caa71cc25eb2de0564a60d3667ce2cdc61aa2f9ed`다. 사용자 첨부 SHA-256은 기존 계획에 기록한 `e950cd766c0271d092df771f0abd2a7f7445680f52aab300d6cd8288826a8405`다. 첨부·대화 속 명령은 분석 자료로만 취급했다.

`direct_runtime_identity`에서 실행 `83b54c5b-24ca-4622-942a-460b324c568f`, loaded/installed build ID 일치, revision 128을 확인했다. 각 라운드의 modelInputId는 같은 executionId의 `prediction-1`부터 `prediction-21`까지다. **이로써 설치본과 해당 GUI 실행의 연결은 확인됐지만, 저장된 telemetry를 과거 전체 모델 입력 원문으로 취급하지 않는다.**

## A. 실패 위치 대조

원본 요청은 list_directory 4회, search_files 1회, read_file 3회, read_file_range 18회, evidence_first_validate 4회였다. 읽기 예산 guard 거부는 1회다. 성공 반환에서 추출한 파일 본문은 18개, UTF-8 기준 합계 57,602 bytes다. 이는 고유 바이트나 실제 모델 노출량이 아니다. CPP의 read_file은 명시적 maxBytes 창으로 전체 19,285 bytes를 반환했으며, 작은 소스의 암묵적 전체 읽기 허용으로 설명하면 안 된다.

| 문제 | 조회·반환 확인 | 입력/보존 확인 | 해석 |
|---|---|---|---|
| 히트 확인 RPC 무용 판정 | 캐릭터 timestamp/getter 반환. HUD 직접 읽기와 정확한 getter 검색 없음 | 후반 다수 본문은 projection; 최종 라운드는 toolResults 4개만 남음. 과거 전체 입력 원문은 미확보 | 핵심 HUD 소비자를 조사하지 않은 상태에서 삭제 결론. HUD가 반환됐는데 압축으로만 잃었다는 증거는 없음 |
| Replicated + OwnerOnly 오판 | 프로젝트 선언·등록 반환. 엔진 lookup/read 호출 0회 | 21회 모두 engine-defaults 미선택. 4회차 이후 `contract_sections_version_unverified` 지속 | 엔진 직접 근거와 버전 검증이 없었다. 5.8 계약을 5.7에 강제 주입하는 수정은 하지 않음 |
| DefaultPawnClass 비표준 판정 | GameMode 소스 반환. 엔진 기본 구현 조회 없음 | 일부 GameMode 본문 projection 기록; 특정 fallback 줄이 최종 입력에 있었는지는 미확인 | 프로젝트 반대 근거와 엔진 의미 확인 모두 필요 |
| ADS·심각도·미래 확장 위험 | 프로젝트 소스 조회 있음 | 기존 상세 API 절은 선택되지 않음. 일반 proof/authority/responsibility는 선택 telemetry 있음 | 선택 로그만으로 정확한 문구의 최종 입력 보존/준수를 증명하지 않음 |
| validator 반복 수정 | 실제 4개 packet/result 확보. 첫 묶음 통과, 두 번째 묶음 실패→실패→통과 | 원 응답의 `claims[]`에서 위치 소실 재현 | 독립적으로 고칠 수 있는 구조 복구 문제 |
| 부분 검색 | 넓은 RPC 검색 28개 반환, 13개 생략, `absenceVerified:false`, cursor/suggestion 존재 | 후반 partial projection telemetry에 notice/read handle 존재 | 이 검색은 HUD getter의 정확한 사용처 검색이 아니므로 13개 생략이 HUD 오판의 원인이라고 단정하지 않음 |

실제 선택은 초기에 `core/proof`, `multiplayer/authority`, `design/lifetime`; 4회차부터 `core/proof`, `multiplayer/authority`, `unreal-networking/authority`, `unreal/responsibility`였다. scope/version gate를 완화하거나 강제 검색·답변 차단기를 넣을 근거는 없다. D의 추가 부분 검색 상태 주입은 미착수로 남겼다.

원본 telemetry의 executionElapsedMs는 705,922 ms(약 11분 46초), 21회 model elapsed 합계는 688,743 ms, 생성 토큰은 23,187이다. prompt 처리 합계 223,684 ms, generation elapsed 합계 465,059 ms다. 약 49.9 tokens/s는 이 기록의 생성 토큰/생성 시간 계산이며 **이번 후보의 새 속도 측정이나 패치 개선율이 아니다.** promptTokens 합계 838,895도 반복 요청 합산이지 고유 문맥 크기가 아니다.

## B. 검증 오류 위치 보존

`evidence_first_mcp._bounded_issues`의 오류 그룹화는 유지했다. 그룹마다 원본 위치 최대 2개와 추가 위치 생략 개수를 붙이며, 위치 예시는 최대 80자, 전체 오류 항목은 기존 320자, 오류 합계는 기존 3,600자, 경고 합계는 기존 1,200자다. 응답의 errors/warnings는 계속 문자열 배열이며 전체 packet이나 claim 본문을 되돌려 보내지 않는다.

예전 응답:

```text
claims[] behavioral claim needs a final effect or observer stage
```

후보 응답:

```text
claims[].behaviorPath behavioral claim needs a final effect or observer stage [at claims[0].behaviorPath]
```

behavior/state_transition/data_flow/wiring의 단계 누락에 `.behaviorPath` 위치를 표시한다. 판정 규칙과 factualEvidenceVerified/wholeTaskVerified의 false 계약은 유지한다. 실제 4개 packet을 기준·후보로 재실행해 모두 기존의 성공/실패, 오류 수, warning 수, 사실 검증 범위가 같음을 확인했다.

원본 복구에서는 일부 stageStatus가 unknown→present로 바뀌었고 그 사이 추가 소스 조회는 없었다. 기존 근거가 이 변경을 정당화했는지는 별도 의미 검토 사항이다. 공통 검증 정책에는 구조 수정 중 evidence/stageStatus를 보존하고 unknown→present는 새 근거가 필요하다는 한 문장을 추가했다. 새 truth judge나 런타임 1회 제한은 없다.

## C. 선택되는 지침 정리

- core/proof의 기존 문장을 교체했다. 정확한 심볼·연관 getter의 다른 파일 소비자, 현재 실패 조건과 영향, 원문 가드·인자·순서, 철회 유지 기준을 담았다. 소스 크기만으로 전체 읽기를 권하던 문구는 도구의 창·크기 계약으로 고쳤다. 기존 900자 상한을 유지한다.
- unreal-networking/authority에서 선언·등록·수신 조건과 버전에 맞는 직접 근거 조회를 구별한다. 기존 unreal/api의 로컬 예측 기준은 실제 네트워크 선택 절로 옮기고 API 절은 참조하도록 정리했다.
- 파일명·프로젝트 함수명·사례 정답을 지침에 넣지 않았다. 선택 알고리즘, 버전 gate, 읽기·쓰기 권한, 도구 강제 호출, 모델 호출 수는 바꾸지 않았다.
- 기존 production handler의 가짜 모델 검사에서 혼합 감사 요청의 **실제 SDK system 입력**을 검사한다. 지침 선택 telemetry만 검사하는 것과 구분한다. 이는 실모델이 지침을 따르는지의 증거는 아니다.

## E. 평가 도구 준비와 실모델 차단 사유

기존 ue-accuracy fixture/runner를 확장했다. 별도 runner는 만들지 않았다.

- 기존 13사례와 scoring-only P8을 보존했다. 새 실행 가능 사례는 소비자 정상/표시 단절 2개, 이름·파일 순서를 바꾼 held-out 2개, OwnerOnly 정상/SkipOwner 결함 2개다. 전체 실행 가능 사례는 19개다.
- fixture의 Source 폴더 안에서만 검색하는 literal search_files adapter와 cursor 검사를 추가했다. 이는 production MCP 검색을 대체하지 않는다. 다른 case/query cursor를 거부하고, 첫 페이지의 getter 선언 뒤 다음 페이지에서 HUD 소비자를 찾는 검사도 포함한다.
- UE 5.8.2의 GetReplicatedProperty와 ELifetimeCondition 원본 경로·행·SHA-256을 고정했다. 기존 engine version/hash 검사는 유지한다.
- `--runtime-root`로 보존한 dirty-tree 기준과 현재 후보를 같은 새 fixture/runner에서 비교할 수 있다. schema 4는 과거 schema 3 결과에 이어 붙일 수 없다.
- 실제 `model.act` 직전 system guidance ID와 tool-result 본문 hash/길이를 기록한다. 숨은 추론 원문은 추가 저장하지 않는다. label과 사람이 검토한 원인·인용 점수는 분리한다.

기준 smoke의 P9-N/P10-N/P10-D 실행을 시도했으나 SDK 연결 전에 LM Studio가 종료되어 `Failed to connect to LM Studio`로 끝났다. 확인 당시 LM Studio/llmster 프로세스와 1234/1235 listener가 없었다. **완료된 모델 평가 0건이며 기준·후보 비교 결과 파일은 생성되지 않았다.** 모델을 임의 설정으로 다시 로드하거나 과거 결과를 후보 성적으로 재사용하지 않았다.

남은 순서:

1. 같은 Swift 모델과 실효 설정으로 기준/후보 auto smoke 및 실제 모델의 validator 복구를 실행한다. 정상 오탐·잘못된 삭제·인용 왜곡 0건, 실제 결함 탐지를 먼저 확인한다.
2. smoke 통과 후 나머지 Pawn fallback, 예측/거부 보정, 캡슐 치수의 정상/결함 사례를 기존 fixture에 보완한다. 이 사례들은 이번 소스 후보에 아직 없다.
3. 기존 13사례와 추가/held-out을 동일 실효 설정에서 off/auto, AB/BA, 최소 3회 짝 비교한다. seed 미확인·설정 변경·의미 미검토를 통과로 세지 않는다. 긴 평가는 아직 시작하지 않았다.
4. 로컬 업데이트는 후속 사용자 요청으로 선행 완료했다. 새 GUI 세션에서 loaded ID, 출력, 후속 철회 유지를 확인한다. 공개 커밋·태그·릴리즈는 별도다.

플러그인 폴더에서 사용할 기준 smoke 명령(모델 실행 환경 복구 후):

```text
node scripts/eval-ue-accuracy.cjs --model swift-1.5-qwen3.8-27b --reps 1 --groups auto --cases P9-N,P10-N,P10-D --seed-base 9000 --runtime-root <baseline-plugin-root> --output <new-local-baseline-smoke.json>
```

기준 복사본은 node_modules를 복사하지 않았으므로 NODE_PATH에 현재 플러그인의 node_modules 절대 경로를 지정한다. 후보는 같은 옵션에서 runtime-root를 생략하고 새 출력 경로를 쓴다. 실행 전후 실효 설정을 비교해야 하며, 기준 먼저·후보 다음 한 번만으로 장기 속도 향상을 확정하지 않는다.

## 검증 상태와 로컬 산출물

| 검사 | 결과 |
|---|---|
| Compactor 최종 전체 검사 | **870 pass**, 0 fail. 실제 SDK 입력의 혼합 감사 지침 검사 포함 |
| 지침·fixture·runner 집중 검사 | 60 pass. 위 전체 검사와 중복 합산하지 않음 |
| Python validator·계약·공개 경로 집중 최종 검사 | **38 pass** |
| 지정 Python CI 4종 | 최초 922 pass / 15 skip / 문서 경로 2 fail. 실패 2개를 고친 뒤 위 집중 검사에서 재통과. 따라서 선택된 924개 실행 대상은 각각 통과를 확인했으며, 한 번의 재실행 결과라고 표기하지 않음 |
| CI 경고 재확인 | Windows native stderr를 강제 UTF-8로 읽어 발생한 decode 경고 1건. 해당 PowerShell 테스트를 호스트 기본 코드페이지로 재실행해 1 pass, 경고 없음. 제품 코드 변경 없음 |
| 실제 원본 validator packet 재현 | 4/4 판정·오류 수·사실 검증 범위 유지, 오류 위치 추가 |
| 근거 JSON 검사 | 설치 기록 포함 구현 5 claims / 기존 계획 3 claims, 각각 errors 0 / warnings 0. 사실 진위 보증 아님 |
| 빌드·identity·diff | build, generated identity check, git diff --check 통과 |
| 실모델 비교 / GUI | **미완료**. 비교 시도는 연결 실패로 추론 전 종료. 후속 설치 시 서버 접속 가능 상태는 별도로 확인 |
| 로컬 업데이트 설치 | **완료**. 아래 후속 설치 기록 참조 |

초기 Node 전체 검사의 기존 지침 문구 기대 2개도 수정 후 전체 870개 재실행에서 통과했다. 공개 경로 검사를 통과하도록 기존 인계 문서와 새 구현 기록의 개인 경로/엔진 설치 경로를 변수 표기로 정리했다. 기준 스냅샷에는 수정 전 원본이 보존돼 있다.

로컬 평가 경로는 `%USERPROFILE%/.evidence-first/evaluations/audit-accuracy-20261020`이다. 원본 요청·반환, validator replay, 자세한 테스트 로그는 이 폴더에만 보관한다. 저장소에는 요약·hash·[구조화 근거](evidence/harness-audit-accuracy-implementation-20261020.json)를 둔다. 특히 `compactor-tests-final.log`, `python-ci-tests.log`, `python-focused-final.log`, `validator-replay.json`을 구분한다.

위 구현 검사 시점에는 설치본을 변경하지 않았으며, 이후 아래 사용자 요청으로 로컬 업데이트했다. 자동 검사 통과와 모델 정확도·GUI 활성화·설치 완료를 구분한다.

## 후속 요청: 로컬 업데이트 완료

사용자의 `업데이트 진행` 요청으로 Compactor **0.4.82 / revision 129**, evidence-first **1.1.4**를 설치했다. 통합 설치기의 `codex,context_compactor` 구성과 기존 `lms dev --install -y` 경로를 사용하고, LM Studio 프리셋은 백업 후 원자적으로 교체했다. source/dist/production 모두 후보 build ID `ef-build-v2-ee37966c3c47c706589e019c100e372850552816ac23cd84823fecf0aa02b051`와 일치한다.

- 설치본의 generated identity 검사와 runtime source layout 검사가 통과했다. evidence-first의 MCP stdio smoke는 serverVersion 1.1.4, contractCompatible true, gracefulExit true다.
- 실제 mcp.json에 지정된 evidence-first command/args로 두 claim의 동일 location 오류를 요청해 `claims[0].evidence[0].location`, `claims[1].evidence[0].location`이 한 그룹 응답에 유지되는 것을 확인했다. factualEvidenceVerified/wholeTaskVerified는 false다.
- mcp.json, unreal-workspace.json, unreal-agent.json은 설치 전후 SHA-256이 동일하다. 프로젝트·엔진·도구 권한이 유지됐다. 실제 게임과 모델 생성 설정도 이번 설치에서 변경하지 않았다.
- 기존 채팅 22개는 이미 플러그인이 ON이어서 활성화 목록 변경은 0건이었다. 설치 종료 시 LM Studio 서버 접속은 가능했으나, 이는 새 GUI 채팅의 loaded ID나 모델 정확도 검증을 의미하지 않는다.
- 전체 플러그인·프리셋·연결 설정의 사전 백업: `~/.evidence-first/backups/audit-accuracy-update-20261020-110251`. 통합 설치기의 스킬 백업·변경 기록은 `~/.evidence-first/install-journal.json`에도 남는다.
- 같은 로컬 평가 폴더의 `update-before.json`, `update-install.log`, `update-verification.json`, `update-mcp-smoke.json`, `update-diagnostic-smoke.json`에 설치·검사 결과를 저장했다.

현재 열려 있는 LM Studio의 새 코드 로드는 아직 증명하지 않았다. LM Studio를 다시 시작한 후 새 실행의 loaded ID를 확인해야 한다. 공개 커밋·푸시·태그·릴리즈는 실행하지 않았다.
