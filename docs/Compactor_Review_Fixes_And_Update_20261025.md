# Compactor 독립 검토 후 수정 및 로컬 업데이트 — 2026-10-25

기준은 `Develop`의 `0acefab991acc32e7e56cabe223bc9c25cfcaf4d`다. [승인된 검토·계획](Compactor_Independent_Review_And_Fix_Plan_20261025.md)의 F1–F6을 구현했다. **오프라인 회귀와 설치 검증은 완료했으며, 수정 후 장기 실모델 감사는 미실행이다.** 테스트 성공을 실모델 반복 루프 해소나 감사 정확도 향상의 증명으로 해석하지 않는다.

## 실제 수정 사항

| 항목 | 이전 문제 | 수정 후 동작 | 주요 구현 |
|---|---|---|---|
| F1, P1 | 검증 도구 요청과 모델의 proof 선언만으로 verified처럼 기록 | 유일하게 짝지어진 요청/성공 결과 및 인용·엔진 근거 공백을 검사한다. 현재 관찰 SHA와 실제 전달 줄 범위에 묶인 `assistant_claim`으로 기록하며 사실 검증이나 파일 감사 완료로 표시하지 않는다. proof 누락/오류, 미읽은 줄, 모호한 버전은 제외한다. | `quote-anchoring.ts:acceptedValidatePackets`, `prediction-loop.ts`, `reference-context.ts:auditChecksFromPacket`, `read-ledger.js:recordCheck` |
| F2, P1 | 집중 확인·복구·실패한 읽기도 무진전으로 누적해 조기 종료 | 필요한 집중 읽기, 현재 입력의 근거 복구, 새 근거, 다른 도구 작업은 정체 구간과 경고 누적을 초기화한다. 실패 읽기는 unresolved로 남긴다. 실제 중복만 이어지는 대조군은 보고 전환한다. `report_now`도 부족한 근거를 완료로 취급하지 않는다. | `prediction-loop.ts`, `resume-facts.ts`, `direct-config.ts` |
| F3, P2 | 전체 복구 참조가 좁은 읽기로 교체되거나 부분 archive를 전체 coverage로 오인 | 전체 복구 참조를 보존한다. 본문 대체 전에 archive 경로·SHA·범위가 요청을 복구할 수 있는지 검사하고 불충분하면 본문을 유지한다. 줄 범위를 입증할 수 없는 문자 페이지는 전체 줄 가용성으로 승격하지 않는다. | `read-ledger.js:attachArchiveRef/archivePageFacts`, `working-context.js:archiveLocator` |
| F4, P2 | A→B→A처럼 과거 해시로 돌아온 파일의 최신 관찰 갱신 누락 | 직전 관찰과 다르면 과거에 읽은 해시여도 새 버전 전환이다. 원문을 전달하고 최신 관찰을 바꾸며 과거 claim을 자동 복원하지 않는다. | `read-ledger.js:classify/record`, `evidence-manager.ts` |
| F5, P2 | 같은 집중 범위를 매번 최초 집중 확인처럼 처리 | 첫 집중 읽기와 새 범위는 허용한다. 같은 집중 범위가 현재 입력에 보일 때만 반복 본문을 대체한다. projection/compaction으로 사라졌다면 필요한 복구를 허용한다. archive의 실제 전달 줄도 현재 가용성에 반영한다. | `read-ledger.js`, `evidence-manager.ts:updateReadVisibility/screenSourceRead` |
| F6, P2 | 입력에 주입한 budget/ledger/projection 안내가 일반 답변으로 재출력 | 기존 입력 데이터 슬롯은 유지한다. 이번 실행이 주입한 내부 프로토콜을 stream/fallback/이력 변환에서 처리한다. 실제 답변, 원본 도구 결과, 사용자 설명 요청, 인용, 코드 블록, 출처 불명 JSON은 보존한다. 내부 안내만 남은 결과를 최종 보고로 세지 않는다. | `context-manager.ts`, `internal-data-output.ts` |

기존 원장 복원 시 전달 범위·관찰 이력은 유지하지만 과거의 self-declared verified 자료를 새 검증 권위로 옮기지 않는다. 공개 MCP/Archive 도구 schema는 변경하지 않았다. 사용자에게 기록되는 원래 도구 응답과 모델 입력용 변환을 구분한다.

독립 배포본 검사에서 추가로 `src/read-ledger.js`의 포터블 패키지 허용 목록 누락을 재현했다. `scripts/build_integrated_package.py`와 기존 배포 계약 검사를 수정했다. 배포본만으로 `npm ci`와 `verify:install`이 성공했다.

## 회귀 및 빌드

| 검사 | 결과 | 한계 |
|---|---|---|
| plugin 전체 `npm test` | **963 통과, 0 실패, 0 제외** | 기준 949개에서 14개 추가 및 기존 기대 동작 수정. TypeScript 컴파일 포함 |
| identity / guidance / Unreal·Unity tool contract 생성물 | **모두 통과** | 소스와 생성물 일치 검사 |
| 분석·trace Python | **24 통과** | 실제 대화 형식 및 분석기 회귀 |
| `tests/test_integrated_package.py` | **19 통과, 1 실패** | 기존 VERSIONING 문서 링크의 배포 누락, 아래 설명 |
| Unreal MCP 전체 `npm test` | **358 통과, 0 실패, 2 제외** | Windows에서 제외하는 POSIX 전용 검사 2개 |
| 수정 후 장기 실모델 감사 및 3회 A/B | **미실행** | 새 시간·토큰·정확도·누출률 실측 없음 |

남은 패키지 실패는 `docs/VERSIONING.md`가 가리키는 `Harness_Audit_Accuracy_Implementation_20261020.md`의 배포 누락이다. 기준 `0acefab`에도 링크가 있고 패키지 허용 목록에는 없음을 확인했다. 이번 수정에서 해당 문서 계약은 변경하지 않았다.

회귀는 원본 응답과 모델 입력을 구별해 검사한다. 동일 SHA 전체 반복, 새 범위/최초 집중 읽기, 현재 본문 소실 후 복구, 부분 archive locator, A→B→A, proof/인용/버전 귀속, 실제 중복의 수렴과 정상 진행의 경고 초기화, 실패 읽기 재시도, metadata-only 가짜 보고, fragment 경계·취소·코드 블록 보존을 포함한다. 기존 병렬 예산·record version 불일치·restore 검사도 유지했다.

새 검사는 `test/source-reacquisition.test.cjs`, `test/prediction-loop.test.cjs`, `test/internal-data-output.test.cjs` 및 `tests/test_integrated_package.py`에 있다. 별도 검토자가 찾은 archive 가용성 누락과 취소 시 미완성 코드 블록 손실도 회귀와 함께 수정했다.

기존 첨부 로그의 51라운드→16라운드 비교는 수정 전 계획 문서의 관찰이다. 이번 패치의 전후 성능 수치로 재사용하지 않는다. 이전 보고의 “33개 본문 대체”도 이번 수정 후 실모델 결과가 아니다.

## UPDATE.bat 실제 실행

- `UPDATE.bat --dry-run` **종료 0**, 이어서 `UPDATE.bat` **종료 0**. `UPDATE_NO_PAUSE=1`, 관리 Python, 정리한 PATH는 해당 프로세스에만 적용했다.
- 백업: `%USERPROFILE%\.evidence-first\backups\compactor-review-fixes-20261025-031825` — plugin 파일 1,371개 및 설정 5개.
- 로그: 백업 폴더의 `update-dry-run.log`, `update.log`, `update-exit-code.txt`, `verification.json`.
- 설치 ID: `ef-build-v2-ec5a32513f4a375db26b6b7ce1bb16d6086021406bfe8b37d54648795bf507bd`.
- checkout과 설치본의 모든 JS/TS 소스 바이트가 일치한다. checkout/설치 source/설치 dist/`.lmstudio/production.js` ID도 일치한다.
- `mcp.json`, `settings.json`, `config/unreal-workspace.json`, `config/unreal-agent.json`, 감사 프리셋의 SHA-256이 백업과 같다. 프로젝트 binding과 권한은 그대로다.
- 대화는 02:36:28 이후 갱신되지 않았고 API 로그에도 최근 실행 흔적이 없었다. 창 닫기는 tray 프로세스를 유지했으므로 유휴 상태로 확인한 기존 프로세스를 종료하고 03:21에 LM Studio를 재실행했다. 이전 PID 종료와 새 창을 확인했다.
- **새 실행의 `direct_runtime_identity.loadedBuildId`는 아직 관찰하지 않았다.** 프로세스 재시작과 설치 ID 일치를 실행 telemetry 확인이나 장기 실험으로 대신하지 않는다.

원본 대화·실제 소스 fixture·비밀 설정은 저장소에 복사하지 않았다. 진단 로그는 `%USERPROFILE%\.evidence-first\diagnostics\compactor-independent-review-20261025`에 있다. 시작 전부터 수정돼 있던 `unity-symbol-worker` 바이너리 3개는 이번 변경에서 제외했다.

## 남은 위험과 권고

이 후보는 감독하에 사용하며 다음 실제 감사 결과를 비교할 단계다. 원장은 관찰·전달·자기 주장 이력이지 감사 결론의 사실 여부를 판정하는 출처가 아니다. Client RPC 등의 엔진 의미 주장은 필요한 엔진 소스 근거를 계속 요구한다.

필요한 집중/복구 읽기와 실패 읽기는 정체로 강제 종료하지 않는다. 모델이 계속 새 범위를 읽거나 잘못된 locator만 재시도하는 모든 실행까지 종료시킨다고 보장하지 않는다. 기존 실행/예산 제약은 유지한다. 필터도 실제 주입한 프로토콜을 대상으로 하며 모델이 전혀 다른 일반 문장으로 바꾸는 모든 재출력을 탐지하지는 않는다.

다음 실모델 회귀에서는 동일 프로젝트 snapshot·프롬프트·모델·설정으로 반복 요청과 본문 재전달, 복구 성공, 일반 채팅 metadata 노출, 미해결 근거, 최종 보고 정확도와 소요 시간을 따로 측정한다. 보고 생성과 감사 완료도 구분해야 한다.
