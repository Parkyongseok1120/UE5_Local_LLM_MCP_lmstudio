# 설치·업데이트·복구

설치는 저장소 맨 위의 `INSTALL.bat` 또는 `install.sh`에서 시작하면 됩니다. 둘 다 같은 `install.py`를 실행합니다. 용도마다 다른 설치 파일을 찾을 필요는 없습니다.

## 지원 운영체제와 필수 프로그램

| 환경 | 실행 방법과 제한 |
|---|---|
| Windows 10/11, x64·arm64 | `INSTALL.bat`. Python이 없으면 Windows PowerShell로 초기 설치를 진행합니다. |
| Apple Silicon macOS | `./install.sh`. Rosetta로 실행해도 실제 장비에 맞는 프로그램을 선택합니다. |
| Ubuntu 22.04/24.04, x64·arm64 | `./install.sh`. glibc 환경을 기준으로 합니다. Alpine처럼 musl을 쓰는 환경은 지원하지 않습니다. |
| Intel macOS | LM Studio·언리얼·대화 압축기 구성은 설치할 수 없습니다. Codex 규칙이나 Cline 등만 고르는 사용자 지정 구성은 가능합니다. |

Python 3.10 이상이 있으면 초기 실행에 사용합니다. 없으면 버전과 SHA-256이 고정된 uv를 받아 검증한 뒤 사용자 폴더에 Python 3.12를 준비합니다. 실제 도구는 설치기가 관리하는 Python을 사용합니다. 시스템 전체에 Python을 등록하거나 PATH를 바꾸지 않습니다.

`python3 install.py`를 직접 실행하려면 Python이 이미 있어야 합니다. 별도 위치의 Python은 `PYTHON=/path/to/python3.12 ./install.sh`로 지정할 수 있습니다.

LM Studio 관련 구성에는 LM Studio 0.4 이상과 `lms` 명령이 필요합니다. Node.js와 npm은 설치 과정에서 준비합니다. 다운로드 주소·버전·해시의 기준 파일은 [runtime-manifest.json](../installer/runtime-manifest.json)입니다.

최소 Ubuntu 환경에 다운로드 도구가 없다면 다음 패키지를 설치해야 합니다.

```sh
sudo apt-get update
sudo apt-get install -y ca-certificates curl tar coreutils
```

## 설치 구성과 권한 선택

1. 설치할 구성을 고릅니다.
2. 언리얼 기능을 포함했다면 읽기 전용인지 수정·빌드까지 허용할지 고릅니다.
3. `.uproject` 파일이나 프로젝트를 찾을 폴더를 선택합니다.
4. Epic Games Launcher에서 엔진을 찾거나 직접 엔진 폴더를 선택합니다.
5. 검색 자료를 만들지, 만든다면 어디까지 수집할지 고릅니다.
6. 마지막 요약에서 선택한 내용을 확인합니다.

| 설치 구성 | 포함되는 것 |
|---|---|
| `SAFE` | 코드 검토 규칙, LM Studio 설정, 대화 압축기를 포함합니다. 프로젝트 접근 도구는 없습니다. |
| `STANDARD` | 위 구성에 언리얼 검색·파일 작업 도구를 추가합니다. 기본 권한은 읽기 전용입니다. |
| `FULL` | 호환용 이름이며 필요한 구성은 `STANDARD`와 같습니다. |
| `CUSTOM` | 필요한 구성 요소를 직접 고릅니다. |

설치 구성의 `SAFE`와 권한 선택의 `SAFE`는 구분해야 합니다. 권한에서 `SAFE`는 파일 수정·명령 실행·빌드를 막는다는 뜻입니다. `AGENT`는 이 작업들을 허용하며 설치 화면에서 한 번 더 확인합니다. 확인을 거절하면 읽기 전용으로 계속 진행합니다.

검색 범위는 설치 구성과 별개입니다. `FULL`을 골라도 엔진 전체 수집이나 쓰기 권한이 자동으로 켜지지 않습니다. [수집 범위 설명](Indexing_Tiers.md)을 보고 `lite`, `standard`, `full` 중 고르면 됩니다.

## 명령행 설치 옵션

```powershell
python install.py --profile standard --yes
python install.py --profile standard --yes --build-rag --index-tier standard --engine-root C:\UE_5.8 --active-project C:\Projects\MyGame\MyGame.uproject
```

첫 명령은 읽기 전용 설치입니다. 두 번째는 지정한 엔진과 프로젝트로 검색 자료도 만듭니다. 경로는 실제 위치로 바꿔야 합니다. 파일 수정과 빌드를 허용하려면 아래 두 옵션이 모두 필요합니다.

```powershell
python install.py --profile standard --yes --enable-agent-mode --accept-agent-risk
```

`--build-rag`는 설치기가 관리하는 Python으로 수집과 검색 자료 생성을 직접 실행합니다. PowerShell은 필요하지 않습니다. Unreal Editor를 열거나, 에디터 내보내기 스크립트를 실행하거나, 프로젝트에 플러그인을 넣거나, `.uproject`를 수정하지 않습니다.

블루프린트 내부까지 읽으려면 [에디터 자료 내보내기](Editor_Metadata_Export.md)를 따로 진행해야 합니다.

## 설치 후 LM Studio 설정

설치 후 LM Studio를 재시작하고 사용할 AI 모델을 직접 선택합니다. `unreal-rag`, `unreal-agent`를 켜면 됩니다.

대화 압축기 `codex/unreal-context-compactor`는 설치하고 목록에 고정만 합니다. 채팅에서 활성화하지는 않습니다. 새 채팅과 기존 채팅 모두 스위치가 꺼져 있는지 확인해야 합니다(`OFF`). 긴 대화에서 필요할 때 해당 채팅의 단일 스위치만 켜면 됩니다. `Observe only`는 대화를 바꾸지 않고 사용량만 측정하는 옵션입니다.

LM Studio·언리얼 구성에는 압축기 파일 설치가 포함됩니다. 일반 설치에서 제외하는 선택지는 없습니다. `--skip-context-compactor --allow-skip-context-compactor`는 지원하지 않는 긴급 우회용입니다.

`lms`를 못 찾으면 `LMSTUDIO_CLI` 또는 LM Studio 설치 위치를 확인해야 합니다. 설치기는 사용자 LM Studio 폴더의 플러그인 파일과 설정을 확인하지만 개별 채팅 저장소는 바꾸지 않습니다.

## 설치 위치와 업데이트

압축을 푼 폴더는 프로그램이 계속 실행되는 위치입니다. 임시 폴더에 설치하거나 설치 직후 지우지 말아야 합니다.

검색 데이터베이스는 기본적으로 `~/.evidence-first/indexes/<namespace>/rag.sqlite`에 저장됩니다. `<namespace>`는 엔진별 저장 폴더 이름입니다. `--state-home`으로 상위 위치를 바꿀 수 있습니다. 별도로 지정한 외부 `indexPath`는 임의로 이동하지 않습니다.

새 버전을 설치할 때 사용 가능한 기존 검색 자료를 재사용합니다. 이전 배포 폴더 안에 있던 자료는 조회 가능한지 확인한 뒤 옮깁니다. 같은 이름의 프로젝트도 실제 경로가 다르면 구분하며, 다른 엔진 버전의 자료를 한 번의 검색에 섞지 않습니다.

첫 설치에서는 `--skip-deps`를 쓰지 말아야 합니다. 이 옵션은 이미 있는 의존 패키지를 재사용할 때만 의미가 있습니다. `@modelcontextprotocol/sdk/server/index.js`를 못 찾는다면 옵션을 빼고 다시 실행하면 됩니다. Python이 없는 새 장비에서는 `--skip-runtime-bootstrap`도 빼야 합니다.

## 설치 복구와 연결 상태 확인

```powershell
python install.py --profile standard --yes
.\rag.ps1 doctor
```

위 복구 예시는 읽기 전용으로 돌아갑니다. 수정·빌드 권한을 유지하려면 원래 쓰던 권한 옵션을 함께 지정해야 합니다.

설치 완료와 검색 준비 완료는 별도입니다. `ragReadiness`가 검색 자료를 실제로 조회할 수 있는지 알려줍니다. `--build-rag`를 선택했는데 자료를 조회할 수 없으면 설치 실패로 처리합니다. 생성을 건너뛰었다면 자료가 없다는 상태가 표시될 수 있습니다.

LM Studio에서 `unreal_rag_health`, `unreal_get_active_project`, `get_workspace_info`로 검색 상태와 프로젝트·권한을 확인할 수 있습니다.

Linux와 macOS에서 유지보수용 `rag.ps1`을 쓰고 싶을 때만 PowerShell 7이 필요합니다.

```sh
pwsh -NoProfile -File ./rag.ps1 doctor
pwsh -NoProfile -File ./rag.ps1 refresh -RefreshScope project_source
```

관리 대상 설정 파일을 이전 상태로 되돌리려면 `python3 install.py --rollback`을 사용합니다. 외부 패키지 설치와 생성한 검색 자료까지 모두 되돌리는 기능은 아닙니다.

Cline 설정 위치가 일반적인 위치와 다르면 `--cline-settings`를 사용합니다. 다른 AI 도구에 규칙 파일을 둘 위치는 `--rule-path`로 지정할 수 있습니다. Windows 배치 실행을 자동화할 때만 `INSTALL_NO_PAUSE=1`로 종료 후 키 입력 대기를 끌 수 있습니다.
