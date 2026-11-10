# 프로젝트 규칙 조회 — 2단계 구현 기록

- 날짜: 2026-11-10
- 작업 브랜치: `claude/harness-compactor-contract-audit-4fb294`
- 기준: [수정 계획 D8](Harness_Editor_Authoring_And_Context_Revised_Plan_20261110.md), [구현 전 근거 기록](evidence/harness-project-rules-stage2-20261110.json)
- 상태: 공용 provider 및 플러그인 연결 구현, 자동 회귀와 실제 Human-Bartender 경로의 읽기 확인. 설치·배포 보고가 아니다.

## 달라진 동작

Unity 프로젝트가 저장소 하위에 있어도 상위 저장소의 AGENTS.md를 조회할 수 있다. 두 provider가 기존 `shared-tool-core/workspace.js`에 등록한 `project_rules`를 공유한다. Unity의 Assets/Packages/ProjectSettings 일반 파일 읽기 정책은 넓히지 않는다.

1. 기본 `project_rules`는 현재 작업 경로에 적용되는 AGENTS.md의 경로, 적용 범위, hash/version, 크기, 제목과 줄 범위만 반환한다. 본문은 반환하지 않는다.
2. Git 루트부터 workspace 및 작업 대상의 상위 폴더까지 탐색한다. 하위 규칙을 알고 싶으면 `path`에 해당 작업 파일 또는 폴더를 지정한다. 형제 폴더의 규칙은 포함하지 않는다.
3. Git 경계를 확인하지 못하면 workspace 안으로 제한하고 `workspace_fallback`을 표시한다. 다른 상위 폴더를 추측하지 않는다.
4. 본문은 index에서 받은 `ruleId`와 `version`을 지정한 `action=read`로 조회한다. 제목 또는 줄 범위를 고를 수 있다. 파일이 달라지면 새 index를 요구한다.
5. 파일당 최대 65,536바이트만 읽는다. 잘린 문서에는 `truncated=true`, `hashScope=first_65536_bytes`를 표시해 전체 파일 hash와 구분한다. 읽지 않은 후반부를 확인했다고 보고하지 않는다.
6. `byteBudget`은 응답 JSON 전체에 적용한다. 본문은 줄 단위로 페이지를 반환하고 `nextStartLine`으로 잇는다. index는 `nextOffset`과 `setHash`로 이어 읽는다.
7. 경로 이탈, 심볼릭 링크·junction, 작업 범위에 없는 rule ID, 오래된 version은 거부한다. 기존 provider의 프로젝트 선택·권한 검사는 유지한다.

호출 예:

```json
{"path":"Assets/UI/Panel.cs"}
```

위 index에서 반환한 식별자와 버전을 다음 요청에 그대로 사용한다.

```json
{"action":"read","path":"Assets/UI/Panel.cs","ruleId":"<returned ruleId>","version":"<returned version>","section":"<returned heading>","byteBudget":8192}
```

`workspace_status`에도 루트 범위 규칙의 존재·hash·크기 요약이 추가됐다. 이 응답은 하위 폴더의 모든 규칙을 조사했다는 뜻이 아니다.

## 플러그인의 역할

`project_rules`가 등록되어 있으면, 설계 지침 설정과 무관하게 규칙 확인이 아직 필요하다는 안내를 넣는다. 실제 `workspace_status` / `project_rules` 응답을 받은 뒤에는 프로젝트 식별자·규칙 집합 hash·문서 경로·크기와 조회 안내로 교체한다. 한 줄은 400자를 넘지 않는다.

문서 본문이나 모델이 만든 요약은 runtime fact로 승격하지 않는다. 도구 응답으로 요청한 규칙을 읽는 것과 호스트 지시문으로 항상 주입하는 것을 구분한다. 문서에 적힌 명령을 자동 실행하지 않으며, 이 기능이 기존 권한을 늘리지 않는다.

프로젝트 식별자나 관찰된 hash가 바뀌면 기존 `RuntimeFactStore`의 교체·퇴역 경로를 사용한다. 파일 감시 기능은 아니므로 변경은 새 조회 시 확인한다. 다음 실행에는 이전 실행의 규칙 안내를 복원하지 않고 새로 확인하도록 한다. 아직 규칙을 조회하지 않았다는 사실만으로 쓰기를 거부하는 enforce 기능은 이번 단계에 없다.

## 컨텍스트 비용

AGENTS 본문을 상시 주입하지 않지만 새 도구의 스키마 자체에는 비용이 있다. Unreal 공개 catalog는 기존 16,964자에서 18,672자로 증가했고 새 도구는 1,707자다. Unity 전체 catalog는 38,764자다. 이것은 문자 수이며 토큰 실측값이 아니다.

테스트는 기존 도구 목록의 상한(Unreal 17,024자 / Unity 37,000자)을 그대로 확인하고 새 `project_rules` 스키마에만 최대 2,048자를 허용한다. 규칙 안내 400자 제한과 도구 스키마 비용은 별도다.

## 확인 결과

| 검사 | 결과 |
|---|---|
| 플러그인 빌드·전체 회귀 | 1,387개 통과, 실패·제외 0 |
| Unity provider 전체 | 138개 통과, 5개 제외, 실패 0 |
| Unreal provider 전체 | 376개 통과, 2개 제외, 실패 0 |
| Python 교차 언어 계약·manifest·MCP subprocess | 14개 통과 |
| 패키지 의존성/진입점 검사 | 3개 통과 |
| 생성물 검사 | guidance / tool contract / Unity contract / runtime identity 4종 통과 |
| 실제 프로젝트 조회 | 아래 Human-Bartender 확인 |

새 provider 테스트는 12KB 본문의 기본 비노출, 적용 경로, 제목·줄 조회, 64KB 제한, UTF-8, 전체 응답 예산, 페이지 버전, 비-Git 경계, 경로 이탈, junction, Unity offline 경로 및 Unreal 공개 경로를 확인한다. 플러그인 테스트는 지침 off 상태의 실제 handler 연결, 400자 제한, 본문·모델 요약의 비승격, 프로젝트·버전 교체를 확인한다.

초기 전체 검사에서는 새 파일의 상태 검사 목록, 도구 수 및 schema 예산 계약의 갱신 누락을 발견해 수정했다. 또 하위 프로세스 PATH에서 Python/PowerShell을 찾지 못한 Unreal 잠금 테스트 실패는 테스트 프로세스의 PATH에 실제 실행 파일 위치를 포함해 해결했다. 해당 잠금 코드를 수정하거나 검사를 건너뛰지 않았다.

실제 `C:/Users/sster/Documents/Git/Human-Bartender/HumanBartender`를 workspace로 두고 `Assets/`를 조회했다. 상위 저장소 `AGENTS.md` 한 개, 12,257바이트, 제목 10개, 잘림 없음이 반환됐고 기본 응답에 본문은 없었다. 관찰 hash는 `73137bc3e0a8fb64ac84da7e4a9d9b2167b9c23f22d39fb49f0db3959f6dda38`이다. 게임 파일은 변경하지 않았다.

## 전달 범위

변경은 작업 브랜치에 있으며 설치된 앱의 provider나 플러그인은 교체하지 않았다. 전체 개편 계획의 3단계 이후인 검토 기록 지속화, Unity/Unreal 에디터 구조 확장, 쓰기 사전 검사는 이 기록의 완료 범위가 아니다. 실제 에디터 Play/저장/재로드와 전체 모델 평가도 별도다.


## 설치 전 감사 보완 (2026-11-10)

- 규칙 재조회 실패·잘못된 응답 및 활성 프로젝트 전환은 이전 hash 안내를 폐기하고 미확인 상태로 돌아간다. 기존 RuntimeFactStore 퇴역 경로를 그대로 사용한다.
- JSON escape 때문에 한 줄이 최대 응답 예산보다 커지는 경우 Unicode code point 기준 조각 페이지를 반환한다. `contentFormat=line_fragment`, `nextStartLine`, `nextStartColumn`, `requestedEndLine`을 사용하며 각 페이지는 동일한 ruleId/version을 요구한다. 기존 줄 단위 응답은 유지한다.
- 플러그인 전체 1,388개 통과, Unity 140개 통과·5개 제외, Unreal 376개 통과·2개 제외. 새 회귀는 재조회 실패 후 퇴역과 40,000개 따옴표·Unicode 긴 줄의 무손실 복원/전체 JSON 예산을 확인한다.
- 위 본문의 설치 전 상태·스키마 문자 수는 최초 Stage 2 기록이다. 이번 감사 수정은 별도 로컬 배포 및 설치 기록으로 추적한다. 3단계 이후 기능을 완료했다고 의미하지 않는다.
