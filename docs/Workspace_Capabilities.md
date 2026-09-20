# Workspace 기능과 호환 경계

## 적용 구조

Workspace 기능은 Unity 및 Unreal MCP의 기존 프로세스 안에서 실행한다.
공통 구현은 `shared-tool-core/workspace.js`와 `shared-tool-core/git.js`가 소유한다.
별도 모델, 작업 계획, 다음 도구 선택, 완료 판정은 포함하지 않는다.

신규 읽기 도구:

- `workspace_status`: 실제 프로젝트 root, identity, engine, mutation owner 확인.
- `git_status`: 현재 index/worktree 상태.
- `git_log`: 지정 revision의 커밋 메타데이터.
- `git_changed_files`: 명시한 두 버전 또는 index/worktree의 변경 파일 목록.
- `git_diff_file`: 정확히 지정한 파일 하나의 diff.
- `git_read_file`: 지정한 커밋에 들어 있는 파일 내용.

Unity는 서버가 바인딩한 프로젝트와 다른 `project` 인자를 거부한다.
Unreal은 기존 프로젝트 resolver를 사용한다. 압축기는 엔진별 도구 필터링을
유지하면서 양쪽의 project 지원 도구에 선택된 프로젝트를 바인딩한다.

## Git 계약

`comparison`은 생략하지 않는다.

```json
{
  "comparison": "range",
  "base": "HEAD^1",
  "head": "HEAD",
  "paths": ["Assets/Scripts"]
}
```

- `range`: `base`와 `head` 모두 필수. 응답은 해석된 전체 커밋 ID를 포함한다.
- `worktree`: index와 작업 트리 비교. `base`/`head` 금지.
- `staged`: HEAD와 index 비교. `base`/`head` 금지.
- 구 `unity_git`의 `last_commit`은 호환용이다. merge commit은 부모를 명시한 range를 사용한다.
- 경로는 프로젝트 상대 경로이며 literal로 처리한다. traversal/pathspec magic을 허용하지 않는다.
- 디렉터리, symlink, gitlink를 일반 소스 파일로 읽지 않는다.
- Git blob SHA-256은 원본 bytes 기준이다. 출력 개행 정규화와 구분되며 쓰기 receipt로 사용할 수 없다.

한 번 만든 결과를 제한된 캐시에 저장하고 서명한 cursor로 같은 결과의 다음 페이지를 반환한다.
cursor는 같은 쿼리에서 사용하며 `startLine`과 함께 보내지 않는다.
만료되면 원래 쿼리를 다시 실행한다. 로그 수집이 상한에 도달하면 `incomplete=true`,
`total=null`을 반환하므로 전체 저장소를 확인했다고 해석하면 안 된다.

수집 한도는 Git 실행당 10초/4 MiB, 저장소당 캐시 16 MiB/8개/5분,
Workspace 인스턴스당 저장소 8개다. 작업 트리 수집은 원자적 파일시스템 스냅샷이 아니다.
status/worktree diff는 submodule 내부 상태를 조사하지 않고 그 제한을 응답에 표시한다.
외부 fsmonitor·diff·textconv·서명 검증 프로그램 실행을 막고, 선택한 worktree 파일에
실행형 clean/process filter가 있으면 명시적으로 거부한다. 상위 프로세스의 `GIT_*`
변수로 다른 저장소나 index가 선택되지 않도록 환경을 분리한다.

## 컨텍스트와 관찰

Git 비교는 `gitObservation`으로 보존한다. 커밋 파일을 읽은 기록을 현재 작업 트리의
읽기 범위나 편집 receipt와 합치지 않는다. 일반 파일 결과에는 schema version,
workspace identity, 파일 hash, 실제로 반환한 범위를 표시한다.
파일을 읽었다는 사실은 의미 검토 완료를 뜻하지 않는다.

압축 후보는 크기에 따른 단조성을 가정하지 않고 제한된 후보를 실제로 측정한다.
선택한 후보 중 예산을 만족하는 것을 사용하며 모든 가능한 길이를 최적화했다고 주장하지 않는다.

아래 옵션은 실제 장기 작업 및 호스트 검증 전까지 기본 비활성이다.

- `pastReasoningTokens=0`: 켜면 SDK의 구조적 경계가 있는 오래된 reasoning만 제한한다.
  최신 assistant reasoning과 tool request/result는 유지하며, 변경 후 다시 측정한다.
- `reviewProgress=false`: 최대 4개 메모 안에서 assistant 검토 주장을 허용한다.
  실제 관찰된 파일 버전 및 완료된 도구 참조가 필요하다. 파일 변경·삭제, 프로젝트
  혼합, 새 첨부가 추가된 요청에서는 이전 주장을 보수적으로 폐기한다.
  커밋 blob을 현재 파일 검토 주장으로 인정하지 않는다. 기준 문서의 의미적 일치나
  검토 완료를 서버가 증명하지 않는다.
- `separateAttachments=false`: typed 문서 첨부를 서명된 참조로 보존하는 실험이다.
  이미지와 사용자 요청은 유지한다. 전처리기보다 먼저 RAG가 문서를 평문으로 바꾸는
  호스트 구성에서는 경계를 추측하지 않는다. 원문 marker 예시는 일반 텍스트로 취급한다.
  유효하게 서명된 참조가 다른 요청/기록에 복사되면 명시적으로 실패한다.

첨부 참조는 같은 설치의 키와 LM Studio 파일 저장소를 필요로 한다. 실험을 켠 대화를
구버전으로 되돌리거나 다른 설치로 옮길 때는 원본 문서를 다시 첨부해야 한다.
키/문서가 없는 참조에서 이전 원문을 복원한다고 보장하지 않는다.

## 쓰기·업데이트·롤백

쓰기 소유자는 기존 엔진 runtime이다. Unity의 `Files` 인스턴스와 receipt,
기존 프로젝트의 lock namespace, Unreal의 CAS·bundle journal·시작 시 복구 경로를 유지한다.
Workspace 읽기 호출 뒤에도 기존 파일 읽기 receipt를 같은 runtime에서 사용할 수 있다.
다른 runtime으로 재시작하면 기존 receipt는 재사용할 수 없으며 새로 읽어야 한다.

설치기는 `WORKSPACE_CAPABILITIES=1`을 등록한다. `0`으로 바꾸고 해당 MCP를 다시
연결하면 신규 Workspace 도구를 숨기며 호출도 거부한다. 기존 파일/엔진 도구는 유지된다.
Unity의 `unity_git`은 신규 도구가 켜졌을 때 목록에서 숨기되 호환 호출을 유지한다.
이 호환 경로도 수정된 strict Git 계약을 따른다. 기존 silent fallback을 복원하지 않는다.

전체 패키지 롤백은 이전 릴리스의 설치기로 수행한다. 일부 shared 파일만 덮어쓰는
혼합 설치는 지원하지 않는다. 새로운 모듈은 패키지 필수 파일 및 압축기 소스 fingerprint에 포함된다.

## 검증과 다음 전환 조건

현재 Windows에서 공통 Git, 엔진 어댑터, 압축기, 설치/배포 계약 테스트와 실제 Qwen의
Git 조회를 검증한다. Qwen 결과는 별도 Git 원본과 대조한다. 이 시험은 Unity Editor,
Unreal UBT/Automation, LM Studio GUI 전처리 순서 검증을 대신하지 않는다.

P2의 기존 writer 및 legacy 제거는 다음 조건을 통과한 뒤 수행한다.

1. 실제 Unity Editor compile/Test Runner 및 Unreal UBT/필요 Automation.
2. 실제 LM Studio에서 typed attachment → 전처리 → RAG → prediction 순서 확인.
3. 같은 모델/프로젝트/요청으로 긴 작업의 완료율과 잘못된 완료 주장 비교.
4. 기존 프로젝트의 receipt/CAS/잠금/복구 및 설치→업데이트→롤백 회귀 없음.

현재 전환은 기존 프로세스·쓰기 소유자를 공유하는 방식이다. 엔진의 모든 일반 파일
구현을 새 서버로 옮기거나 기존 writer를 삭제하는 전환까지 완료한 것으로 간주하지 않는다.
