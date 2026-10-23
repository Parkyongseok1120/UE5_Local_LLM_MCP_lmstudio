# 인용 대조·지침 재진입·projection 후 축소 후속 수정

2026-10-24. `Develop`의 `e55663b` 이후 작업 트리 수정 기록이다. 앞선 수정본을 재검토해 남은 인용 대조 결함 3건을 재현하고 수정했다. 이 기록의 확인 수준은 **TestVerified**이며 LM Studio 실모델의 성능·출력 개선을 확인한 기록은 아니다.

## 기존 수정본에서 유지한 변경

- `quote-anchoring.ts`: 부재·비교·제안 문맥의 코드 이름을 긍정 인용에서 제외한다. 중간 바이트 읽기처럼 줄 위치를 입증할 수 없는 응답은 줄 인덱스에 넣지 않는다. 짧은 인용은 다른 파일의 이름 존재만으로 통과시키지 않는다.
- `round-input.ts`: `kept`와 `dropped` 양쪽의 `selectionScope`를 검사한다. scope가 달라졌으면 현재 후보에서 다시 선택하며, 예산에 맞지 않으면 이전 instruction을 버리고 새 scope만 기록한다. omission telemetry는 실제 재사용한 instruction에 맞춘다.
- `round-input.ts`, `context-manager.ts`: 채택할 작업 입력이 projection된 경우에만 `workingInputProjected`를 통해 `deepen`을 전달한다. 기존 low-water 후보 탐색을 같은 라운드에 수행한다. target 미달이어도 기존 high/hard/next-action 조건에 맞으면 실행할 수 있으며, mandatory floor 예외를 유지한다. 복구 후보에만 적용한 projection은 이 플래그를 설정하지 않는다.

## 후속 재현과 수정

### 1. 같은 위치 반복으로 검사 해제

원문이 `A.cpp:12 void Tick() { DoWork(); }`, `B.cpp:50 void Cleanup() {}`일 때 `A.cpp:12`의 observation에 `Line 12: Tick() calls Cleanup().`를 넣으면 검사가 생략됐다. 추가 위치 여부를 observation 전체의 정규식 일치 하나로 판정한 것이 원인이다.

이제 observation을 절 단위로 대조한다. 같은 위치를 반복해도 검사는 유지한다. 명시한 추가 위치는 해당 절에서만 사용하고 실제 반환된 범위와 대조한다. `cpp:315-317`은 이름이 유일한 형제 구현 파일로 해석하므로 헤더와 구현을 함께 설명하는 기존 사례도 처리한다. 다른 절에 붙인 추가 위치는 앞선 잘못된 인용을 면제하지 않는다.

재사용한 경로는 기존 `locate`, `observedCodeTokens`, 반환 원문 인덱스이다. 패킷 형식이나 prediction-loop의 호출 계약은 추가하지 않았다.

### 2. 쉼표로 나열한 부재 설명 오탐

`never calls Cleanup(), Destroy(), or Reset()`에서 뒤쪽 두 이름을 긍정 인용으로 수집했다. 한국어 `Cleanup(), Destroy() 호출은 없다`에서도 첫 이름이 잘못 수집됐다.

이제 쉼표 목록은 앞의 부정·제안 표현과 뒤의 한국어 부정 표현을 공유한다. 쉼표 뒤에 독립적인 긍정 주어·서술어가 명확한 경우에만 절을 나눈다. `but`, `지만`, 세미콜론 등으로 구분한 별도의 긍정 절에서 발생한 `PlaySound2D()` 오인용은 계속 검출한다. 호출 인자·매크로 괄호 내부의 쉼표는 기존대로 절 경계가 아니다.

### 3. 개행 직후 잘린 바이트 읽기의 다음 행 오염

`offsetBytes: 0`, `hasMore: true`인 응답이 개행으로 끝나면 `split`의 마지막 빈 항목이 읽지 않은 다음 행으로 등록됐다. 앞서 읽은 실제 다음 행을 빈 문자열로 덮어썼다.

이제 EOF 이전의 부분 바이트 응답에서는 마지막 split 항목을 항상 제외한다. 마지막 항목은 잘린 행 또는 마지막 개행 뒤의 아직 읽지 않은 행이다. 응답 안에서 완전히 읽은 빈 행은 유지한다. 실제 EOF의 마지막 빈 행도 유지한다. LF·CRLF를 모두 검증했다.

## 검증

| 검사 | 결과 |
|---|---|
| 추가 회귀 테스트 5개 | 수정 전 모두 실패, 수정 후 모두 통과 |
| 인용 대조 테스트 | 16개 통과 |
| Compactor 전체 테스트 | **901개 통과, 실패 0** |
| TypeScript 빌드 | 통과 |
| runtime identity / tool contract / guidance / Unity contract 검사 | 통과 |
| `test_trace_lmstudio_run.py`, `test_tool_manifest_contract.py`, `test_cross_language_tool_contract.py` | **22개 통과** |
| Unreal MCP 전체 테스트 | 위 교차 언어 테스트가 `node test/run-tests.js`를 실행해 성공 종료 확인 |
| `git diff --check` | 통과 |

추가 회귀 테스트는 같은 위치 반복, 절별 추가 위치와 형제 구현 파일, 영어·한국어 부정 목록, 별도 긍정 절의 오인용, 개행 경계의 바이트 읽기와 실제 EOF를 검증한다. 기존 수정본의 지침 scope 재선택 및 B2 테스트도 전체 테스트에 포함됐다.

Windows 하위 프로세스 검사에는 프로세스 한정 PATH를 적용했다. node, git, System32, Windows PowerShell, Python 경로를 포함했으며 시스템 전역 PATH는 변경하지 않았다. source 변경에 맞춰 `generated-runtime-identity.ts`를 재생성했다.

## 남은 확인 범위

- 인용 대조는 코드 이름을 비교하는 보조 검사이다. 의미가 불확실한 문장, 넓은 인용 범위, 반환되지 않았거나 식별이 모호한 추가 위치는 보수적으로 처리하므로 모든 잘못된 인용을 검출하지는 않는다.
- 실제 호출 인자·조건·동작의 사실 검증은 하지 않는다.
- 실모델에서 B2가 프롬프트 재작성 횟수와 소요 시간을 줄이는지, 최종 보고서 오인용이 줄어드는지는 같은 감사 요청의 LM Studio 실행으로 별도 확인해야 한다.
- 이번 작업은 소스·테스트·문서 정리까지 수행했다. 커밋·푸시·LM Studio 설치·재시작은 수행하지 않았다.
