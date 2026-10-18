# 좁은 읽기 하네스 구현·검증 기록

기준 HEAD: `fbff059979edc8980eab970e7a7bba9e0b1d5181`. 이 작업은 기존 미커밋 구현을 이어받았다. 아래 내용은 인계받은 변경을 포함한 최종 후보의 상태이며 전부 이번 실행에서 새로 작성한 것으로 세지 않는다. commit/push, 실제 설치본 재설치, 모델 재로드는 하지 않는다.

## 구현 계약

| 단계 | 후보에 반영한 동작 | 확인 경계 |
|---|---|---|
| P0 | trace에 읽은 줄·엔진 줄·중복 비율·첫 편집 전 읽기 수·성공 편집당 호출 수를 기록한다. byte window와 전체 읽기를 구별하고 결과가 없는 편집을 성공으로 세지 않는다. `repeatReceipt`만 달라진 요청은 같은 재읽기로 세되 생략된 본문은 읽은 줄 수에 더하지 않는다. | 필요한 줄 수와 실제 편집 시간은 대화 로그만으로 만들지 않는다. 수정 전 동일 조건 baseline과 실모델 A/B는 아직 없다. |
| P1 | 범위 완료 시 `rangeComplete:true`, `nextStartLine` 생략. 엔진 및 큰 파일의 암묵적 전체 읽기 거부. 실패한 편집의 후보 주변 ±10줄 또는 토큰 검색과 현재 hash 제공. 긴 선언은 괄호 균형으로 자른다. | 기존 `requestedRangeComplete`도 보존한다. 명시적 byte window는 긴 한 줄의 복구 경로이므로 허용한다. hash는 현재 관찰 버전이며 모델이 본 원문이나 현재 수정 성공을 뜻하지 않는다. |
| P2 | 검색 `contextLines`/`include`, `read_symbol` declaration/member, engine_local signature 옵션. signature 응답은 정의 본문을 생략하고 서버가 끝을 확인한 구현의 `rangeSuggestion`을 보존한다. JS/Python owner 영역 공통 fixture. | 기본 body/full 유지. signature는 모델에 전달하는 본문을 줄이며, 정확한 구현 위치를 얻기 위한 서버의 소스 탐색은 유지한다. lexical 결과는 overload·매크로·호출 가능성의 의미 검증이 아니다. |
| P3 | 단일 파일과 bundle의 `edits[]`, 같은 기준 hash에서 각 문자열이 유일하고 서로 겹치지 않는지 검사. 원자 적용·rollback·영역별 hunk·receipt touch. | 파일당 최대 8영역, 영역별 32줄, 기존 설정의 합계 줄·UTF-8 byte 제한 유지. bundle의 파일 수 제한은 그대로다. |
| P4 | Unreal 관찰 hash를 `expectedHash`로 재사용. 현재 턴의 좁은 읽기 최대 3파일·6,144자를 pin하고 실제 입력 예산 압박 시 해제. 현재 원문에 있는 동일 요청에만 repeat receipt 첨부. | 파일 상태 검증은 provider가 담당한다. project와 byteBudget이 다르면 동일 요청으로 합치지 않는다. receipt를 영속 기억으로 승격하지 않는다. |
| P5 | 지침 정본에 좁은 읽기 순서·필요 범위만 재획득·쓰기 hunk 근거 사용을 반영하고 generator로 파생물을 만든다. | core/proof 및 tool schema 크기 상한을 유지한다. |

## 실모델 평가 고정 과제

| ID | 요청 | 판정할 사실·필요 범위 |
|---|---|---|
| T1 | `UGameplayStatics::ApplyDamage` 인자와 반환형 설명 | 설치 엔진의 선언과 인자 목록. 구현 전체 읽기 불필요. |
| T2 | T1에서 사용한 구조체/타입의 지정된 멤버 하나 확인 | 요청한 owner의 멤버 선언. 다른 owner의 같은 이름과 구별. |
| T3 | 격리 synthetic 프로젝트 3파일의 4영역을 지정된 값으로 변경 | 사전 지정 exact text, 최종 파일 hash, 실패 시 무변경, 필요한 파일당 기준 hash. |
| T4 | 같은 synthetic 프로젝트를 긴 감사로 압축한 뒤 지정 영역 수정 | 현재 입력의 exact oldText 재사용, 서버 conflict 시 해당 범위 복구, 최종 수정 내용. |
| T5 | T1/T2에서 이미 읽은 동일 버전에 후속 질문 | 중복 도구 호출·중복 전달 줄 수와 정확한 답변. |

각 케이스의 실제 source hash와 필요한 줄 범위를 baseline 실행 전에 고정해야 한다. 실제 UE 소스는 읽기 전용으로 쓰고 변경 과제는 임시 프로젝트에서만 실행한다. 모델·양자화·설정·build ID가 같은 B/후보를 AB/BA 순서로 각 3회(채택 후보 5회) 실행한다. 정확도 하락이 없고 사전 지정한 읽기/중복/편집 비용 지표가 10% 이상 개선됐을 때 채택한다. 과제 정의만으로 실행 또는 성능 개선을 주장하지 않는다.

## 검증과 남은 경계

- 인계 상태의 Python 전체 실행: 22 실패 / 1,338 통과 / 18 skip. 상당수 설치 실패는 테스트 하위 `cmd`의 PATH에서 node/npm을 찾지 못했다. 새 모듈 두 개의 패키지 목록 누락과 schema 예산 초과는 코드/패키징 문제로 별도 수정했다.
- 검사는 테스트 프로세스의 PATH만 보정한다. 시스템 PATH나 사용자의 설치 설정은 변경하지 않는다. 통합 설치 테스트는 임시 codex/lmstudio/state 디렉터리를 대상으로 한다.
- semantic guard 5회 측정: 71, 64, 63, 63, 67ms, 모두 성공. 작은 C++ synthetic 입력의 측정이며 대형 파일·실모델 성능 증거가 아니다. 비동기 전환 없이 기존 검사를 유지했다.
- LM Studio의 저장된 HTTP 포트는 1234이며 `http://127.0.0.1:1234/api/v1/models` 조회는 연결 거부였다. 서버 시작·모델 로드/재로드는 하지 않았다. 따라서 실모델 A/B와 10% 채택 판정은 미검증이다.
- Node MCP 설정은 작업 트리의 서버 파일을 가리킨다. 이미 실행 중인 Node/Python 프로세스의 모듈 캐시는 자동 갱신되지 않으므로 새 프로세스 시작 시 후보 코드를 읽는다. Compactor 작업 트리의 build 성공은 설치·실행 중인 플러그인의 교체를 뜻하지 않는다.

최종 자동 검증 결과:

| 검사 | 결과 |
|---|---|
| `node lmstudio-unreal-agent-mcp/test/run-tests.js` | 331 통과 / 2 skip / 실패 0 |
| Compactor 디렉터리의 `npm test` (generator·TypeScript build 포함) | 825 통과 / 실패 0 |
| `python -m pytest -q` | 1,365 통과 / 20 skip / 실패 0, 246.05초 |
| 위 전체 회귀 이후 signature 구현 범위 제안 보완: engine definition / engine_local / header identity / RAG server / owner fixture 관련 회귀 | 103 통과 / 실패 0 |
| Unity Node 회귀 (공유 change-evidence 변경 후 실행) | 131 통과 / 5 skip / 실패 0 |
| design-guidance / tool-contract / unity-tool-contract / runtime-identity `--check` | 4종 모두 통과 |
| `python scripts/verify_encoding.py` 및 새 파일 별도 검사 | 추적 텍스트 파일 1,172개와 새 텍스트 파일 11개 통과 |
| 근거 패킷 validator | 7개 claim 구조·기록 일관성 통과; 사실과 작업 전체를 인증하는 검사는 아님 |

최종 검토에서 `aaaa` 안의 `aaa`처럼 서로 겹치는 동일 문자열도 다중 영역 편집의 모호한 위치로 거부하도록 보완했다. 해당 경로는 실패 전후 파일 무변경 회귀로 검증했다. 버전 고정 테스트도 최종 메타데이터에 맞췄다.

구성 요소 버전은 Unreal Node `0.3.27`, Compactor `0.4.78` / revision `125`, 배포 구성 `2.1.27`이다. 제품 버전은 `1.4.0`이다. 테스트는 작업 트리의 소스와 로컬 빌드를 검증했으며 실제 설치본 활성화나 실모델 성능 검증을 뜻하지 않는다. [근거 패킷](evidence/harness-narrow-read-implementation-20261019.json)에 소스·테스트와 미검증 범위를 함께 기록했다.
