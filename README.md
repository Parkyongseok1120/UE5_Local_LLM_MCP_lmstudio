# UE5_Local_LLM_MCP_lmstudio 1.3.3

LM Studio에서 돌리는 AI가 내 언리얼 프로젝트를 찾아보고, 코드를 읽고, 필요한 부분을 고칠 수 있게 연결하는 도구입니다.

AI에게 프로젝트 전체를 매번 붙여 넣는 대신, 필요한 내용을 로컬 검색 자료에서 찾아서 보여줍니다. 이 검색 방식을 RAG라고 부릅니다. MCP는 AI가 검색·파일 읽기·수정·빌드 같은 기능을 호출할 때 쓰는 연결 방식입니다.

## 설치 방법

```powershell
git clone https://github.com/Parkyongseok1120/UE5_Local_LLM_MCP_lmstudio.git
cd UE5_Local_LLM_MCP_lmstudio
.\INSTALL.bat
```

Linux와 macOS에서는 같은 폴더에서 `./install.sh`를 실행하면 됩니다. Python이 없으면 실행에 필요한 버전을 사용자 폴더에 설치합니다. 시스템 전체의 Python 설정이나 PATH는 바꾸지 않습니다.

설치 화면에서 프로젝트와 엔진을 고르고, 검색 자료를 만들지 선택하면 됩니다. 처음에는 `STANDARD` 구성에 읽기 전용인 `SAFE` 권한으로 시작하면 됩니다. AI에게 파일 수정과 빌드까지 맡기려면 `AGENT` 권한을 따로 선택해야 합니다.

**설치 구성과 검색 범위는 별개입니다.** `FULL` 구성을 골랐다고 엔진 소스 전체를 수집하거나 쓰기 권한을 켜는 건 아닙니다. 검색 자료는 별도로 만들어야 합니다.

압축 배포본을 받았다면 오래 둘 폴더에 풀어서 설치해야 합니다. 설치 후에도 그 폴더의 파일을 실행하므로 지우거나 임시 폴더에 두면 안 됩니다. 첫 설치에서 `--skip-deps`를 쓰면 필요한 패키지가 없어서 실패할 수 있습니다.

자세한 선택 항목은 [설치 안내](docs/Integrated_Installer.md)에 있습니다.

## LM Studio 연결 설정

1. 사용할 AI 모델을 불러오고 모델 목록에서 직접 선택합니다.
2. 설치 후 LM Studio를 재시작하고 `unreal-rag`, `unreal-agent`를 켭니다.
3. 채팅의 `codex/unreal-context-compactor`는 기본적으로 꺼두어야 합니다(`OFF`). 오래된 채팅에 켜져 있으면 직접 끄면 됩니다.
4. 정확한 프로젝트를 지정하고 질문하거나 수정할 내용을 요청합니다.

대화 압축기는 긴 대화의 오래된 내용을 줄여 주는 보조 기능입니다. 설치했다고 채팅에서 켜지는 건 아닙니다. 필요할 때 해당 채팅의 단일 스위치만 켜면 되고, 별도 활성화 설정은 없습니다.

언리얼 작업 채팅에서는 LM Studio의 `js-code-sandbox`를 꺼두어야 합니다. 이 도구의 작업 폴더는 언리얼 프로젝트와 다르므로 프로젝트 파일 작업에 쓰면 안 됩니다.

설정 예시와 확인 방법은 [LM Studio 연결](docs/LMStudio_Unreal_Agent_Setup.md), Rider와 Cline을 쓴다면 [Rider·Cline 연결](docs/Cline_Rider_Unreal_Agent_Setup.md)을 보면 됩니다.

## 프로젝트 검색 자료 생성

이 저장소에는 언리얼 엔진 소스나 완성된 검색 데이터베이스가 들어 있지 않습니다. 본인이 사용하는 엔진과 프로젝트로 직접 만들어야 합니다.

```powershell
python install.py --profile standard --yes --build-rag --index-tier standard --engine-root C:\UE_5.8 --active-project C:\Projects\MyGame\MyGame.uproject
```

이후 프로젝트 코드가 바뀌면 아래처럼 갱신하면 됩니다.

```powershell
.\rag.ps1 set-project -ProjectFile C:\Projects\MyGame\MyGame.uproject
.\rag.ps1 refresh -RefreshScope project_source
.\rag.ps1 doctor
```

경로는 실제 설치 위치로 바꿔야 합니다. `rag.ps1`은 검색 자료를 관리하는 명령입니다. AI에게 질문하는 곳은 LM Studio나 Cline입니다.

같은 이름의 프로젝트를 여러 곳에 복사해 뒀다면 `.uproject` 전체 경로로 구분해야 합니다. 서로 다른 엔진 버전의 검색 자료도 따로 관리합니다. [검색 자료 관리](docs/RAG_Setup.md), [수집 범위 선택](docs/Indexing_Tiers.md)에 정리했습니다.

## 지원 기능과 제한 사항

- 코드와 설정 파일을 찾고 읽을 수 있습니다.
- 쓰기 권한을 켜면 기존 파일 일부를 고치거나 새 파일을 만들 수 있습니다.
- 빌드 권한을 켜면 선택한 프로젝트의 엔진으로 빌드와 자동화 테스트를 실행할 수 있습니다.
- 블루프린트나 머티리얼 내부는 에디터에서 내보낸 자료가 있어야 살펴볼 수 있습니다. 파일 경로만 검색됐다고 노드 연결까지 읽은 건 아닙니다.

기본 동작인 `Direct`에서는 AI가 필요한 도구와 작업 순서를 정합니다. 서버는 파일 접근 범위와 권한, 다른 프로그램이 바꾼 파일을 덮어쓰지 않는지 확인합니다.

기존 파일 수정에는 읽기나 직전 수정 결과에서 받은 `fileVersionReceipt`를 매번 전달해야 합니다. 이 값은 “어느 파일의 어느 상태를 보고 수정하는지”를 확인하는 표식입니다. 서버가 알아서 이전 값을 골라 주지는 않습니다. 빌드하지 않았다면 빌드 성공이라고 말하면 안 됩니다.

## 문서 목록

| 하려는 일 | 문서 |
|---|---|
| 설치·업데이트·복구 | [설치 안내](docs/Integrated_Installer.md) |
| LM Studio 연결 | [LM Studio 설정](docs/LMStudio_Unreal_Agent_Setup.md) |
| Rider·Cline 연결 확인 | [연결 방법](docs/Cline_Rider_Unreal_Agent_Setup.md), [설치 후 점검](docs/Rider_Cline_Smoke_Checklist.md) |
| 검색 자료 만들기·갱신 | [검색 자료 관리](docs/RAG_Setup.md), [수집 범위](docs/Indexing_Tiers.md) |
| 블루프린트·머티리얼 읽기 | [에디터 자료 내보내기](docs/Editor_Metadata_Export.md), [읽을 수 있는 내용](docs/Blueprint_Metadata.md) |
| 모델 설정 | [설정값 설명](docs/Model_Profiles.md) |
| 오류 해결 | [문제 해결](docs/Troubleshooting.md) |
| 수정·빌드 권한 이해 | [권한 설정](docs/Safe_Agent_Mode.md), [도구 사용 규칙](docs/LMStudio_MCP_Tool_Discipline.md) |
| 내부 구조 확인 | [구성 설명](docs/ARCHITECTURE.md), [검색 대상 구분](docs/Project_Routing.md), [Build.cs 읽기](docs/Build_Cs_Parser.md) |
| 이번 버전 변경 확인 | [1.3.3 변경 사항](docs/Release_Notes_1_3_3.md), [버전 관리](docs/VERSIONING.md) |

실제 프로젝트에서의 동작은 엔진 버전, 플러그인, 개발 환경에 따라 확인이 필요합니다. 자동 검사를 통과한 것과 에디터에서 직접 실행해 본 것은 구분해서 적어야 합니다.

기여 방법은 [CONTRIBUTING.md](CONTRIBUTING.md), 보안 제보는 [SECURITY.md](SECURITY.md), 엔진 자료 취급은 [EPIC_NOTICE.md](EPIC_NOTICE.md)를 참고하면 됩니다.
