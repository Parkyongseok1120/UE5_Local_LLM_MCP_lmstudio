# Unity MCP — v1.4.0 beta 1

Unity 프로젝트의 상태를 모델이 읽고 명시한 작업만 실행하는 별도 MCP 서버입니다. 내부 모델 호출, Planner, 자동 수정·재시도, 패키지 설치·빌드·Git 작업은 없습니다. 기존 Unreal 서버와 실행 경로를 공유하지 않습니다.

## 호환성 및 프로젝트 독립성

- Unity **2022.3 LTS 이상에서 제공되는 공통 Editor API**를 구현 기준으로 사용합니다. 2022.3과 Unity 6 전체 버전을 테스트한 뜻은 아닙니다. 실제 실행한 버전/운영체제는 [검증 기록](Unity_Validation.md)에 구분합니다.
- 특정 Editor 설치 경로, 프로젝트 이름, 게임 assembly, MonoBehaviour/SO 클래스 이름을 제품 코드에 넣지 않습니다. 프로젝트 루트는 `UNITY_PROJECT_ROOT`, 타입은 호출 인자, 버전·기능은 handshake에서 결정합니다.
- 프로젝트를 복사하면 실제 정규 경로가 달라지므로 다른 연결 대상입니다. 각 프로젝트에 별도 MCP 프로세스를 연결합니다.
- Node.js 20 이상. RPC Bridge는 Editor-only이며 UPM 패키지는 작은 runtime debug 등록 계약과 선택 패키지별 관찰 probe도 포함합니다. 기본 Bridge는 특정 게임 assembly나 Test Framework를 참조하지 않습니다. Test Framework가 설치되어 있으면 분리된 선택 어댑터 assembly가 활성화됩니다.
- Editor와 Node MCP는 **같은 OS 호스트**에서 실행합니다. loopback과 PID·실제 경로를 확인하므로 Intel Mac의 Linux VM에서 Mac Editor에 직접 연결하는 구성은 이 alpha에 포함되지 않습니다. 모델 추론은 MCP 호스트가 지원하는 원격 연결을 사용할 수 있습니다.
- 지원하지 않는 기능은 capability=false 및 `capability_unavailable`로 반환합니다. 모델이 필요로 한다는 이유로 패키지를 설치하거나 다른 구현을 선택하지 않습니다.

## 설치

통합 설치기에서 Unreal과 Unity를 함께 설치할 수 있습니다. 두 엔진의 프로젝트 경로와 권한은 각각 관리하며, 프로젝트와 MCP 설정을 백업·기록합니다.

Windows에서는 `INSTALL.bat`를 실행하고 설치 프로필을 선택합니다. 이어서 **Unreal MCP를 추가할지**, **Unity MCP를 추가할지** 각각 묻습니다. 두 항목을 모두 선택하면 **Unreal 프로젝트·엔진·인덱싱 설정 → Unity 프로젝트 설정 → 설치 요약** 순서로 진행합니다. 하나만 선택하면 해당 엔진만 설정하며, 두 항목을 모두 제외할 수도 있습니다.

Unity 폴더 선택 창에서는 `Assets`, `Packages`, `ProjectSettings`가 들어 있는 프로젝트 루트를 지정합니다. 선택 창을 사용할 수 없으면 경로를 직접 붙여 넣을 수 있습니다. 이어서 Unity Editor 실행 파일(`Unity.exe`) 경로를 입력하거나, 호환되는 .NET SDK가 설치되어 있으면 Enter로 건너뜁니다. Unreal의 `.uproject`와 Unity 폴더는 별도로 저장합니다. Unreal에서 AGENT 권한을 선택해도 Unity는 읽기 전용으로 설치합니다.

명령줄에서는 다음과 같이 같은 설치 경로를 사용할 수 있습니다.

```bat
INSTALL.bat --profile custom --components codex,lmstudio,unreal,unity --yes --active-project "D:\Projects\UnrealGame\UnrealGame.uproject" --unity-project "D:\Projects\MyUnityGame" --unity-editor "C:\Program Files\Unity\Hub\Editor\<version>\Editor\Unity.exe"
```

LM Studio 또는 Unreal과 함께 설치하면 기존 LM Studio `mcp.json`에 `unity-tools`를 추가하고 기존 서버 항목을 유지합니다. 설치 결과의 `unity.mcpConfig`에서 경로를 확인할 수 있습니다. 관리 설정은 하나의 설치 기록으로 저장하므로 Unity 단계에서 실패하면 앞서 변경한 Unreal 설정도 복원합니다. 외부 의존성 설치, 생성한 인덱스와 컴파일된 worker는 복원 대상에 포함되지 않습니다. 채팅에서는 작업할 엔진에 맞는 도구를 활성화합니다.

Unity만 설치하는 기존 `--profile custom --components unity` 방식도 유지합니다. 이 경우 기본값은 별도 프로젝트용 MCP 설정이며, 결과의 `mcpConfig`에 있는 `unity-tools` 항목을 사용할 MCP 호스트에 등록해야 합니다.

```sh
./install.sh --profile custom --components unity --yes \
  --unity-project /absolute/path/to/UnityProject \
  --unity-editor /absolute/path/to/installed/Unity
```

Node 의존성 및 외부 C# 분석 작업자를 준비하고 UPM Bridge를 연결합니다. `.NET SDK`가 없으면 명시한 Editor의 사용 가능한 컴파일러/runtime을 확인합니다. 기존 worker는 `--unity-dotnet`과 `--unity-symbol-worker`로 지정할 수 있습니다. `--dry-run`은 프로젝트를 변경하지 않습니다. 기본 MCP 설정 위치는 설치 결과의 `mcpConfig`입니다. `--unity-mcp-config`로 호스트 설정 파일을 지정하면 다른 서버 항목은 유지합니다. 다른 Bridge 경로의 교체는 `--unity-replace-bridge`가 필요합니다. 테스트·Input·UI 패키지를 자동 설치하지 않습니다.

Intel Mac에서 VM 기반 모델 연결까지 설정하려면:

```sh
./install-intel-mac.sh --engine unity --project /absolute/path/to/UnityProject \
  --unity-editor /absolute/path/to/installed/Unity --vm-name my-lm-vm --model provider/model
```

이 경로는 llmster/LM Link만 Linux VM에 두고 **Unity MCP는 Mac에서 실행**합니다. 생성된 `lmstudio-cli.sh` 옆 `lmstudio-cli.conf`에 VM·모델·실제 Python 경로를 보관합니다. 로그인 페어링은 사용자가 승인해야 합니다. 도구 목록, 구조화된 MCP 상태, 모델의 실제 health 도구 요청을 각각 검사하며 텍스트 `true/false`를 연결 증거로 취급하지 않습니다. 모델 연결의 실환경 검증 여부는 [검증 기록](Unity_Validation.md)을 확인하세요.

`--model-endpoint`로 MCP 호스트에서 접근 가능한 `/v1/chat/completions` URL을 지정할 수 있고 CLI에 보관됩니다. 기본값은 `http://127.0.0.1:1234/v1/chat/completions`입니다. Unity 경로에서는 Mac에서 해당 포트가 원하는 VM/모델 서버로 연결되도록 포트 전달을 확인해야 합니다. 다른 서버가 포트를 점유했다면 별도 포트/URL을 지정하세요. VM의 LM Link 온라인 상태와 HTTP 모델 endpoint 연결 검사는 별개입니다.

수동 구성은 다음과 같습니다.

1. 저장소를 계속 유지할 위치에 둡니다. `shared-tool-core`와 `lmstudio-unreal-agent-mcp/src`의 공통 파일 I/O 의존성도 필요하므로 Unity 폴더만 복사하지 않습니다.
2. Unity adapter 의존성을 설치합니다.

```sh
cd /path/to/repository/lmstudio-unity-mcp
pnpm install --frozen-lockfile --ignore-scripts
```

3. 선택한 Unity 프로젝트의 Package Manager에서 **Add package from disk**를 선택하고 저장소의 `unity-editor-bridge/package.json`을 지정합니다. UPM이 선언된 Newtonsoft 의존성을 해결합니다. 실행 중인 MCP는 패키지를 설치하지 않습니다.
4. 다음 명령으로 선택한 프로젝트용 MCP 설정을 출력합니다.

```sh
node /path/to/repository/scripts/configure_unity_mcp.js /absolute/path/to/UnityProject
```

5. 출력의 `unity-tools` 항목을 사용하는 MCP 호스트에 등록합니다. Unity 채팅에는 해당 서버를 선택하고 Unreal 도구는 비활성화합니다. 이 스크립트는 기존 호스트 설정을 자동 덮어쓰지 않습니다. 도구 이름이 같은 파일 서버를 함께 노출하지 않습니다.
6. `unity_status`에서 `connection=connected`, 정규 프로젝트 경로, Editor 버전과 capability를 확인합니다. Editor가 없거나 컴파일 오류로 Bridge가 로드되지 않아도 파일 도구는 사용할 수 있습니다.

기본값은 Observe입니다. 수정하려면 호스트 설정 `ALLOW_WRITE=1`과 Unity 메뉴 **Tools → Evidence First → Allow Edit for this Editor session**을 모두 켭니다. Play/컴파일/import에는 `ALLOW_COMMANDS=1`과 **Allow Execute for this Editor session**이 모두 필요합니다. 메뉴 권한은 Editor를 종료하면 초기화되고 domain reload 사이에는 유지됩니다.

LM Studio의 MCP 설치 방법은 호스트 버전에 따라 달라질 수 있습니다. 생성되는 설정은 stdio MCP 형식이며 모델 선택·로드를 담당하지 않습니다.

## Unity MCP 소스 업데이트

Unity MCP 설정의 `unity-tools.args[0]`은 설치에 사용한 소스 폴더의 `lmstudio-unity-mcp/src/server.js`를 직접 가리킵니다. 같은 폴더에 새 소스를 반영한 다음, 아래 스크립트로 Node 의존성과 MCP 초기화를 확인합니다. 스크립트는 소스를 다운로드하거나 Git 상태를 변경하지 않습니다.

Windows에서는 저장소 루트의 `UPDATE.bat`를 실행하면 설치된 Unity MCP, Unreal Agent/RAG, 대화 압축 플러그인을 순서대로 갱신합니다. 설치되지 않은 엔진은 건너뜁니다. 기본 설정은 `%LMSTUDIO_HOME%\mcp.json` 또는 `%USERPROFILE%\.lmstudio\mcp.json`을 사용합니다. 다른 위치에 설치했다면 `UPDATE.bat "C:\path\to\mcp.json"`처럼 기존 설정 파일을 지정합니다. 기존 프로젝트·엔진·인덱스·권한 설정은 다시 생성하지 않고 유지합니다. `UPDATE.bat --dry-run`은 세 단계의 계획만 확인합니다. 완료 후 LM Studio를 재시작합니다.

```sh
python scripts/update_unity_mcp.py --mcp-config /absolute/path/to/mcp.json --dry-run
python scripts/update_unity_mcp.py --mcp-config /absolute/path/to/mcp.json
```

Unreal Agent/RAG만 검사하려면 다음 명령을 사용할 수 있습니다.

```text
python scripts/update_unreal_mcp.py --mcp-config /absolute/path/to/mcp.json --dry-run
python scripts/update_unreal_mcp.py --mcp-config /absolute/path/to/mcp.json
```

잠금 파일과 기존 Node 의존성이 그대로인 업데이트에서는 `--skip-deps`를 지정할 수 있습니다. 성공하면 MCP 호스트에서 `unity-tools` 연결을 다시 시작해야 새 도구 목록과 코드가 반영됩니다. 스크립트는 설정 파일, 다른 MCP 서버 항목, `ALLOW_WRITE`/`ALLOW_COMMANDS`, Unity 프로젝트 파일을 수정하지 않습니다. 이미 실행 중인 MCP 프로세스를 종료하지도 않습니다.

새 릴리스 폴더로 실행 경로를 옮기거나 Unity Bridge 또는 C# 심볼 작업자를 갱신해야 한다면 기존 `install.py`의 Unity 설치 경로를 사용합니다. 업데이트 스크립트는 현재 설정이 다른 소스 폴더나 Bridge 바인딩을 가리킬 경우 시작 전에 거절합니다. 의존성 설치는 외부 작업이므로 실패 시 새 소스 폴더의 Node 의존성을 확인하고 다시 실행합니다.

## 실제 구현 범위

| 영역 | 구현한 동작 | 현재 제한 |
|---|---|---|
| 연결 | 프로젝트 탐지, token 인증, loopback 동적 포트, 세션/버전 handshake, reload 후 discovery 재조회 | 같은 호스트만 지원; 자동 mutation 재전송 없음 |
| 파일 | 직계 폴더 조회, 문자 그대로 경로/본문 검색과 다중 확장자 필터, 범위 읽기, Receipt 기반 부분 수정, absent 조건 생성 | UTF-8 본문만, 파일 2 MiB, 부모 디렉터리는 미리 존재해야 함 |
| JSON | 명시 경로·깊이 조회, 기존 경로 replace/remove, draft-07 명시 스키마 검증 | root 교체/add/move 미지원, unsafe integer 거부, 원격 스키마 로딩 없음 |
| CSV | 문자열 행·필터 조회, key 기반 cell 교체, 원본 셀 이외의 바이트 보존 | UTF-8, header 필수, 구분자 명시; 중복/없는 key 오류; 행 생성·삭제·schema 미지원 |
| 탐색 | 로드된 Scene 객체/컴포넌트, AssetDatabase 필터, 컴파일된 타입 | 전체 프로젝트 자동 탐색 없음 |
| C# 심볼 | 외부 Roslyn 의미 분석, 선언/타입/멤버/사용/상속/인터페이스, Unity 컴파일 구성과 소스 해시 | 작업자 설정 필요; 선택 assembly 범위, 불완전/오래된 인덱스 명시 |
| 참조 | 코드 사용, 에셋 경로 의존성, 직렬화 propertyPath, 실제 관찰 런타임 관계; 정/역방향 | 명시 수집 범위 한정; 닫힌 Scene 자동 검사 없음, 미사용 추론 없음 |
| 디버그 | object 상태, 실제 Input System/uGUI/3D 물리 관찰, 프로젝트 query/action 등록 및 FSM 검증 | 패키지 없으면 package_absent; 임의 getter/method 실행 없음 |
| 스냅샷 | capture/read/diff/release, 제한적 연속 기록, 수집 당시 값 디스크 보관 | 명시 ObjectRef/직렬화 필드 한정; 자동 Pause/rollback 없음 |
| Inspector/SO | SerializedProperty 조회, scalar/vector/color/quaternion/enum/object reference 수정, 배열 삽입·제거·이동 | 지원하지 않는 타입은 표시; 관리 참조 타입/그래프 교체는 미지원 |
| 관리 참조 | ID/타입/명시 propertyPath의 하위 필드 관찰, 다른 속성 수정 시 기존 그래프 보존 | 무한 재귀 전개하지 않음; 임의 getter 실행 없음 |
| Scene | 열린 Scene 목록, 객체 생성·이름·활성·부모·component 수정, 정확한 Scene 저장, 승인된 객체 삭제/component 제거 | 삭제는 독립적인 Editor 승인 필요; Scene 자동 저장 없음 |
| SO | 런타임에서 발견된 concrete SO 타입으로 `.asset` 생성, 공통 read/patch, 지정 에셋 저장 | 복제 기능 미지원; 새 SO 생성은 Unity CreateAsset로 바로 저장됨 |
| Prefab | isolated 원본 편집, 생성·인스턴스화, exact property override apply/revert, nested/variant의 명시적 목적지 | 원본 저장은 명시적 save 및 sourceReceipt; 열린 Prefab Stage 충돌 거부; 배열 override 확대 적용 거부 |
| 로그·컴파일 | 구독 이후 Console, 구조화된 compiler diagnostics, 명시적 script compilation/import | Editor.log 파일 도구와 소스 해시→assembly 검증 미지원; 과거 성공을 현재 코드 증거로 쓰지 않음 |
| 실행 | 명시적 Play/Stop/Pause/Resume/Step, 런타임 직렬화 snapshot | 런타임 수정과 비직렬화 필드 탐색 미지원 |
| operation | durable 요청/결과, ID 충돌, 중단된 요청의 unknown 처리, 테스트 상태 조회 | 임의 Unity API 강제 중단 불가 |
| Test Framework | 실제 EditMode/PlayMode 테스트, 정확한 이름/분류, 상태·결과 페이지·취소·release | 패키지/API 확인, 단일 실행·시간·개수·저장 상한; 이미지 캡처는 별도 미지원 |
| 파괴적 작업 | 정확한 요청에 대한 Editor 승인 UI, 승인된 삭제·component 제거·override 폐기 | MCP에는 승인 발급 endpoint 없음; 2분 만료·1회 사용·상태/세션 결합 |

DataSO는 프로젝트에서 만든 ScriptableObject입니다. 별도 포맷/데이터베이스를 만들지 않습니다. Material 등의 참조값은 관찰/지정할 수 있지만 Material·Shader 전용 제작 API는 제공하지 않습니다.

## 도구 사용 예

네 추가 영역은 v1.4.0의 필수 완료 기준입니다. 작업자 빌드와 `UNITY_DOTNET`/`UNITY_SYMBOL_WORKER` 설정, 각 도구의 정확한 사용법·스키마·자원 상한은 [심볼·참조·디버그·스냅샷 계약](Unity_Debug_1_4.md)을 참고하세요. 환경 변수를 설정하고 config 생성 스크립트를 실행하면 두 작업자 설정도 출력에 포함됩니다.

Prefab 원본·승인·Test Framework의 요청/결과 계약과 제한은 [Authoring 및 테스트 계약](Unity_Authoring_Tests.md)을 참고하세요.

각 요청은 모델이 선택합니다. 서버가 다음 단계를 호출하지 않습니다.

파일 구조를 볼 때는 `list_directory`로 선택한 폴더의 바로 아래 항목만 조회합니다. `kind`는 `all`(기본값), `files`, `directories` 중 하나입니다. 프로젝트 루트의 가상 목록은 없으며 `Assets`, `Packages`, `ProjectSettings`를 각각 지정합니다. 모델이 관심 있는 하위 폴더를 선택한 뒤 필요할 때만 다음 조회를 요청합니다.

```json
{"path":"Assets","kind":"directories","limit":20}
```

`search_files`의 `extensions`는 마지막 확장자를 정확하게 비교하는 선택적 배열이며 대소문자를 구분하지 않습니다. 여러 확장자 중 하나에 해당하면 포함하고, `query`가 있으면 프로젝트 상대 경로의 대소문자를 구분하는 부분 문자열과 함께 적용합니다. `.cs`는 `.cs.meta`를 포함하지 않습니다. 확장자를 생략하면 기존 검색처럼 종류를 제한하지 않습니다. `content:true`는 검색어가 있을 때만 허용하며, 선택한 확장자의 파일 본문에서 일치하는 줄도 찾습니다. 확장자만 지정하면 파일 본문은 읽지 않습니다.

```json
{"path":"Assets/03.Scripts/Craft","extensions":[".cs",".json",".prefab"],"limit":20}
```

`search_files`의 `total`은 이번 제한된 탐색에서 수집한 결과 **행** 수입니다. 본문 검색은 한 파일에서 여러 행이 나올 수 있습니다. `incomplete:true`이면 요청 범위 전체를 검사하지 못했습니다. `truncated`와 `nextCursor`는 이미 수집한 행의 다음 페이지를 뜻하며 미탐색 구간을 이어받지 않습니다. 여러 확장자를 섞어 검색했을 때 첫 페이지가 한 종류로만 채워져도 다른 종류의 부재를 뜻하지 않습니다. 각 종류를 반드시 확인해야 하면 별도 조건으로 조회합니다. 새 검색이나 폴더 조회의 커서는 도구·세션·경로·필터·관찰 결과에 묶여 있고, 다른 요청으로 재사용하면 `snapshot_changed`가 납니다.

구조 개요 요청에서는 상위 폴더부터 필요한 만큼만 내려가고, 역할을 설명해야 하는 대표 파일만 읽습니다. 전체 파일 수집을 완료 조건으로 삼지 않습니다.

```json
{"kind":"assets","query":"t:MyData","limit":10}
```

`unity_find` 결과의 `target`을 그대로 `unity_object_read`에 전달합니다. 이름을 ID처럼 사용하지 않습니다.

```json
{"target":"<실제 ObjectRef 객체>","propertyPaths":["health","items.Array.data[0]"],"limit":10}
```

위 예시의 target 문자열은 설명용 자리표시자입니다. 실제로는 결과에 있는 JSON ObjectRef 객체를 넣습니다.

```json
{
  "target":"<실제 ObjectRef 객체>",
  "scope":"asset",
  "receipt":"<방금 읽은 receipt>",
  "operationId":"unique-operation-0001",
  "patches":[{"op":"set","propertyPath":"health","value":"100"}]
}
```

Unity 정수는 64비트 정확도를 위해 decimal 문자열로 전달합니다. enum/flags는 정수값, 벡터는 숫자 배열, 객체 참조는 ObjectRef 또는 명시적 null입니다. `sceneInstance`는 일반 Scene 객체, `asset`은 ScriptableObject 에셋 수정 범위입니다. Prefab 원본을 이 두 범위로 수정할 수 없습니다.

배열 인덱스는 현재 Receipt의 순서 기준입니다. `array_insert`는 삽입한 원소의 `value`를 반드시 지정합니다. 이 alpha에서는 직접 set이 가능한 원소 타입만 삽입합니다. `array_remove`는 객체 참조 배열의 null 처리 후 실제 슬롯 제거까지 수행합니다. `array_move`의 `toIndex`는 이동 후 위치입니다. 관리 참조의 타입 변경/공유관계 재구성은 비활성화되어 있습니다.

Scene/SO 변경은 dirty만 만듭니다. 저장하려면 정확한 Scene 경로 또는 SO target/receipt와 `acknowledgeExistingDirty=true`를 전달합니다. 같은 Scene 또는 같은 asset 파일 내의 기존 사용자 변경·sub-asset도 저장될 수 있습니다. SO 저장은 SaveAssetIfDirty이며 OnWillSaveAssets 훅을 호출하지 않습니다.

`unity_editor`는 API 요청을 발행하면 `accepted`를 반환합니다. 이후 모델이 `unity_status`, `unity_logs`, `unity_operation`을 요청해 관찰해야 합니다. `accepted`는 import 완료, 컴파일 성공, 테스트 통과를 뜻하지 않습니다. `sourceAssemblyVerification=unknown`을 유지합니다.

## 충돌·실패·수명주기

- 파일 receipt는 MCP 프로세스 세션/경로/내용에 묶이고 15분 후 만료됩니다. 객체 receipt는 Editor domain에 묶이며 reload 후 무효입니다. 오래된 receipt를 현재 값으로 자동 갱신하지 않습니다.
- 조회 cursor는 결과/세션/쿼리에 묶입니다. 상태가 달라지면 `snapshot_changed`입니다. 전체 파일을 읽었어도 응답에는 요청 범위만 포함됩니다.
- Runtime ObjectRef는 Play session에 묶입니다. Stop 후 또는 새 Play에서 실패하며 같은 이름 객체로 대체하지 않습니다.
- mutation은 operationId를 필수로 사용합니다. 응답이 유실되면 `unity_operation`으로 확인합니다. 같은 ID의 다른 요청은 충돌합니다. domain reload 중 미완료 기록은 `outcome_unknown`이며 자동 실행하지 않습니다.
- journal은 프로젝트 Library/EvidenceFirst/operations에 최대 1,000개를 보관하고 초과 시 거부합니다. 자동 삭제/eviction하지 않습니다. Library 삭제·디스크 손실 이후 exactly-once를 보장하지 않습니다. journal을 비운 뒤 이전 ID를 재사용하지 마세요.
- 짧은 Undo group은 하나의 호출에만 사용합니다. rollback을 시도해도 프로젝트 callback의 부작용은 남을 수 있으므로 `partially_applied`와 `undoCompleted`를 분리합니다. 범용 atomic transaction을 주장하지 않습니다.
- 네트워크 timeout은 미적용을 의미하지 않습니다. `outcome_unknown`으로 표시하며 재실행하지 않습니다. 일반 동기 Unity API는 강제 취소하지 않습니다. Test Framework의 협력적 취소는 `cancel_requested`와 실제 소유 job 종료 관찰을 구분합니다.

## 응답 및 보안 경계

기본 페이지 50개, 최대 200개; RPC 요청 128 KiB, 응답 64 KiB; 로그 최대 500개/256 KiB입니다. 로그 개별 message/stack은 잘림을 표시하고 구독 시작/생략 수를 반환합니다. 조회 필드 하나가 예산보다 크면 임의로 null로 바꾸지 않고 좁은 조회를 요청하는 오류를 반환합니다.

일반 파일 쓰기는 Assets의 텍스트 allowlist만 허용합니다. Packages와 ProjectSettings는 기본 읽기 전용이며 embedded/local package 쓰기 allowlist는 아직 없습니다. Library/Temp/숨김 경로는 모델 파일 도구에서 차단합니다. `.meta`, `.prefab`, `.unity`, `.asset`은 일반 쓰기로 우회할 수 없습니다. symlink/junction 및 새 파일 부모를 재검사하며 hard-linked 수정도 거부합니다. 기존 lock/temp-rename는 협력하는 프로세스 간 제어이고 외부 Editor의 모든 경쟁 쓰기에 대한 OS 원자적 CAS는 아닙니다.

discovery token은 private 파일과 RPC 인증에만 사용하며 도구 결과에 반환하지 않습니다. 같은 OS 사용자나 실행 중 프로젝트 코드에 대한 sandbox가 아닙니다. Scene/Asset 로드, 직렬화, import, OnValidate, ExecuteAlways, AddComponent의 RequireComponent 처리 등에서 프로젝트 코드나 Unity 자체 부작용이 발생할 수 있습니다. 서버는 관련 수정 대상을 추가로 판단하지 않습니다.

## 개발 검사

```sh
node --test lmstudio-unity-mcp/test/*.test.js
node lmstudio-unreal-agent-mcp/test/run-tests.js
UNITY_TEST_VERSION=<설치된 버전> node scripts/test_unity_bridge.js /absolute/path/to/Unity /existing/work-parent
node scripts/test_unity_rpc.js /generated/unity-bridge-test-project
```

첫 Editor harness는 기존 사용자 프로젝트를 사용하지 않고 임시 프로젝트를 생성합니다. 후속 RPC 검사 때문에 해당 테스트 Editor를 실행 상태로 남기고 PID를 출력합니다. 검사가 끝나면 그 PID만 종료해야 합니다. 제품의 Unity 지원과 테스트 harness의 명시적 테스트 시퀀스를 혼동하지 않습니다.
