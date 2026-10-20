# 2026-10-20 안정화 구현 기록

기준 HEAD: `eb476dfc4b4f25bfdd62e5193bc00c8ae1523ad5` (`main`). 사용자의 `진행` 요청으로 [전체 계획](Harness_Stability_And_Accuracy_Plan_20261020.md)의 첫 안정화 묶음을 구현했다. 이 기록은 로컬 작업 트리 기준이며, 설치·릴리즈 완료 보고가 아니다.

## 구현한 동작

| 작업 | 변경 | 확인 범위 |
|---|---|---|
| W1 | 실제 입력에 주입한 refs/derived protocol을 생성 답변에서 분리한다. 스트리밍, 완성 메시지 fallback, 마지막 flush, 다음 도구 라운드 이력에 같은 필터를 사용한다. | 분할 위치 전체, CRLF, 반복 블록, 정상 한국어·영문, 코드·인용 보존, malformed/누락 종료, 실제 production handler의 가짜 모델 3라운드 검증 |
| W2 일부 | 같은 anchor의 재시도에서 새 runtime fact가 생겨도 기존 블록만 재사용하던 분기를 수정했다. 기존 부분을 보존하고 아직 전달하지 않은 부분을 덧붙인다. | 부분 projection 후 reasoning recovery 경로에서 새 안내가 빠지는 것을 재현한 뒤 수정. 미변경 재시도 중복 없음, 새 도구 교환의 기존 prefix 보존 검사 |
| W3 | 문맥을 모르는 bare `GetNetMode()`를 가짜 API로 판정하던 정규식 경고를 제거했다. | 상속/명시적 수신자/미확정 문맥과 기존 명백한 가짜 API 경고 회귀 |
| W4 | 엔진 선언·구현을 수정 전에 확인하고 검색 실패·컴파일 오류로 API 부재/제거 이력을 지어내지 않도록 기존 지침을 보완했다. `OnRep` 직접 호출 및 예측적 로컬 쓰기는 실제 효과·보정 경로로 판단한다. | 정본과 생성 지침의 일치, 빌드. 모델 판단 정확도 개선은 아직 평가하지 않음 |
| W5 | 단순 편집/빌드 receipt에 검증 호출을 반복하지 않도록 안내했다. 중립 결과의 mode/claimType 복구 방법과 `Confirmed/Info` 조합을 오류 메시지에 제시한다. | 기존 유효/무효 패킷과 새 복구 사례. 사실·심각도를 바꿔 통과시키라는 안내는 하지 않음 |

W1은 해당 실행에서 실제로 주입한 protocol만 후보로 삼는다. 사용자 입력을 다시 쓰지 않고, fenced code·blockquote·들여쓰기 예시·인라인 설명·일반 JSON은 유지한다. 원래 captured 메시지와 reasoning은 진단/추론 경계에 남으며 일반 본문만 정리한다. raw reference를 권한·실행 증거로 승격하지 않는다. 새로운 모델 호출은 추가하지 않았다. 내부 자료만 출력된 경우에도 완료 판정이 원문을 답변으로 세지 않도록, 기존 DeliveryController에 정리한 메시지 사본을 전달한다. 일반·bounded 경로에서 보고 완료와 작업 완료가 모두 false인 회귀를 추가했다.

필터는 **자동 재출력 경로의 보정**이다. 사용자가 첨부한 과거 GUI 현상 전체가 이 경로에서 발생했다는 인과 증명은 아직 없다. 실제 GUI 호스트의 저장/복원과 표시, 명시적인 로그 분석 요청은 수동 채택 검사에 남는다. malformed/과도하게 긴 데이터는 정상 답변 손상을 피하려고 보존하므로 모든 가능한 문자열 유출을 차단하는 보안 필터로 취급하면 안 된다.

## W2 재현과 제한

처음에는 동일 history의 합성 A→B 사례만 있었다. 이번에는 production `createPredictionLoopHandler`로 72,000자 결과를 첫 입력에서 부분 projection으로 줄이고, 추론 한도 종료 후 같은 history로 재시도했다. 작업 디렉터리가 있어 note protocol을 사용하는 조건에서 첫 입력과 재시도 모두 새 partial summary가 없었다. 부분 결과 자체의 `bodyComplete=false`와 `readHandle`은 두 입력에 남았으므로 원문 복구 기능이 상실된 것은 아니다.

수정 후 첫 입력은 summary 없음, 재시도는 summary 있음으로 확인됐다. 같은 anchor에 기존 부분을 그대로 두고 새 부분을 추가한다. 입력 후보 구성은 shown map을 직접 변경하지 않고, 기존 최종 입력 commit 지점이 갱신한다. 신규 도구 교환에서 이전 입력 전체가 prefix로 남는 회귀도 통과했다.

**같은 anchor에서 새 안내가 생긴 재시도는 전체 입력이 이전 입력의 완전한 prefix라는 뜻이 아니다.** 추가 위치 이후 user/tool suffix가 이동할 수 있다. 이미 보낸 블록 내용과 앞부분은 유지하지만 실제 서버 재처리 비용은 측정하지 않았다. 과거 스냅샷 누적, 값이 A→B→A로 돌아올 때 최신성 표현, 프로젝트 전환 등 W2 전체 수명 문제는 이번 최소 수정의 완료 범위가 아니다. 성능 채택은 실모델 비교가 필요하다.

## 검증과 실행 환경

- Compactor 빌드와 guidance/toolcontract/unitycontract/identity 정합성 검사, runtime source layout 검사를 수행했다.
- 출력·루프·projection·prefix 회귀와 Python 계약 테스트를 추가했다. 최종 Compactor 전체 **866개 통과**, Unreal MCP 전체 **337개 통과 / 플랫폼 조건 2개 제외**, Python `tests` 전체 **1,374개 통과 / 18개 제외**다. 패키지 20개와 집중 계약 52개 검사는 Python 전체에 포함되므로 합계에 중복 가산하지 않는다.
- Windows의 상속 PATH가 길고 중복되어 하위 `cmd`가 `node`/`ping`을 찾지 못한 초기 검사 실패가 있었다. 테스트 프로세스 안에서 유효 경로를 중복 제거하고 Node/System32를 앞에 둔 뒤 재실행했다. 시스템/사용자 PATH는 변경하지 않았다.
- 일부 초기 Python 전체 검사도 같은 환경에서 실패가 쌓여 중단했다. 경로를 정리한 최종 검사 결과와 구분한다.
- LM Studio SDK의 읽기 전용 `llm.listLoaded()` 결과는 시작과 마무리 확인 모두 `[]`였다. 모델을 임의 설정으로 로드하거나 실모델 추론을 실행하지 않았다.

`compileall`, 생성 정본 검사, 로컬 identity/status, `git diff --check`도 통과했다. 두 근거 패킷 형식 검사는 각각 8개/2개 주장, 오류 0·경고 0이며 사실 진위나 전체 작업 완료를 보증하지 않는다. 이는 로컬 Windows 검사이며 GitHub의 다른 OS CI나 Unreal 빌드 실행 결과가 아니다.

배포 패키지 검사는 처음에 새 소스가 required runtime 목록에 없어 추출본 컴파일이 실패했다. 목록과 기존 패키지 계약 테스트를 보완한 뒤 패키지 검사 20개가 통과했다. 이는 임시 테스트 패키지이며 사용자의 설치본을 업데이트한 것이 아니다.

구현 경로의 [구조화 근거](evidence/harness-stability-implementation-20261020.json)는 초기 조사 패킷과 별도로 기록했다.

빌드 ID: `ef-build-v2-d182c5b0a4d6c59ad5bc571e80b27a43c017707c61a604d69b8239eb7aad5459`.
소스 버전: Compactor **0.4.81 / revision 128**, evidence-first **1.1.3**, installer payload **2.1.31**. 제품 릴리즈 표기는 **1.4.1**을 유지한다. 이 버전들이 현재 GUI에 활성화됐다고 보고하지 않는다.

## 남은 채택 조건

1. W0: 실제 GUI의 loaded ID와 생성 fragment → SDK 메시지 → 일반 본문 연결을 확인한다.
2. W2/W6: 스냅샷 누적 비용/최신성, 입력 재처리 원인, 실제 중복 읽기를 측정한다. cache/GPU/parallel/context 설정은 이번에 바꾸지 않았다.
3. W4/W7: 동일 모델·템플릿·실효 설정으로 smoke, 정상/결함 및 미사용 사례, 13사례 off/auto 짝비교를 수행한다. 지침 변경만으로 정확도 향상을 확정하지 않는다.
4. W7: 실제 GUI 새/이어지는 세션, 분리된 게임 fixture의 읽기·편집·실패 복구·빌드, 통합 설치 payload와 실제 활성 빌드를 확인한 뒤 유지보수 릴리즈에 채택한다.

현재 사용자의 Unreal 게임 checkout, LM Studio 설정, 설치된 플러그인은 수정하지 않았다. 커밋·푸시·태그·릴리즈는 실행하지 않았다. 이전 기록의 약 49.9 tokens/s와 이번 변경의 속도를 비교하는 새 측정치는 없다.

## 후속 요청: 로컬 업데이트 설치

이 절은 위 구현 종료 시점 이후 사용자의 `업데이트 진행` 요청으로 실행한 결과다. Compactor 0.4.81 / revision 128을 `lms dev --install -y` 경로로 설치하고, evidence-first 스킬을 통합 설치기로 교체했다. LM Studio evidence-first 프리셋은 이전 파일을 별도로 백업하고 원자적으로 교체했다. 기존 Unreal 권한의 정규화를 피하려고 `codex,context_compactor` 구성으로 설치하고, Unreal MCP는 기존 mcp.json을 사용하는 전용 updater로 의존성과 stdio 초기화만 갱신·확인했다.

- 설치된 manifest/package 버전, 필터 소스, evidence 계약과 프리셋이 저장소와 일치한다.
- 설치된 `.lmstudio/production.js`와 `dist/generated-runtime-identity.js`에 위 빌드 ID가 있다. 설치본의 identity 검사와 runtime source layout 검사도 통과했다.
- 설치된 evidence-first의 stdio smoke는 serverVersion 1.1.3, contractCompatible true, gracefulExit true다.
- Unreal Agent 26개, Unreal RAG 8개 도구의 별도 stdio 초기화가 통과했다. 실제 게임 빌드나 모델 호출은 실행하지 않았다.
- mcp.json은 설치 전후 SHA-256이 동일하다. 기존 ALLOW_WRITE/ALLOW_COMMANDS/ALLOW_UNREAL_BUILD는 모두 1로 유지됐다. 프로젝트·엔진·인덱스 연결도 해당 파일과 함께 보존됐다.
- 기존 채팅 21개는 이미 플러그인이 ON이어서 활성화 목록 변경은 0건이었다.
- 통합 설치 백업은 `~/.evidence-first/backups/20261020-100814-2efa3877`, 프리셋 백업은 `~/.evidence-first/backups/harness-stability-preset-20261020-100843`이다. 설치·검증 로그는 같은 평가 디렉터리의 `update-*.log`, `update-verification.json`에 있다.

파일 업데이트는 완료됐지만 현재 실행 중인 GUI가 새 코드를 로드했다는 뜻은 아니다. LM Studio 재시작 후 실제 채팅에서 확인해야 하며, 재시작은 이번에 실행하지 않았다. 실모델 정확도·성능 평가와 공개 릴리즈는 계속 미완료다.
