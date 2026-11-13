# UE5_Local_LLM_Agent_lmstudio

**언리얼 프로젝트에서 코드 찾기, 수정, 빌드 확인까지 이어서 할 수 있게 만든 로컬 개발 도구입니다.**

처음에는 로컬 LLM을 언리얼 개발에 써보려고 했습니다. 직접 써보니 답변 품질이 기대보다 낮았고, 예전 엔진 버전 기준으로 답해서 제가 쓰는 버전과 맞지 않는 경우도 있었습니다. 그래서 모델이 현재 프로젝트 코드와 설치된 엔진 소스를 직접 찾아보고, 수정한 뒤에는 빌드 결과까지 확인할 수 있게 이 도구를 만들었습니다.

## 어떻게 만들었는지

LM Studio에서 모델이 MCP 도구를 호출하면 아래 서버가 검색과 파일 작업을 처리합니다.

- **`unreal-rag`**는 프로젝트 코드와 설치된 언리얼 엔진 소스에서 관련 코드의 위치를 찾습니다.
- **`unreal-agent`**는 해당 파일을 읽고, 권한이 있으면 필요한 부분을 수정하거나 Unreal Build Tool로 빌드합니다. 빌드에서 나온 오류는 모델이 다시 확인할 수 있게 돌려줍니다.
- **[Context Compactor](lmstudio-context-compactor-plugin/README.md)**는 별도로 만든 LM Studio 플러그인입니다. 대화가 길어지면 이전 내용을 줄이고, 도구가 반환한 기록은 필요할 때 다시 읽을 수 있게 합니다.

`코드 검색 → 파일 읽기 → 필요한 부분 수정 → 빌드 → 오류 확인`

<img width="1920" height="1080" alt="프로젝트 화면" src="https://github.com/user-attachments/assets/cd25e0fe-d6fd-4ea8-be24-d1606bb644aa" />

## 만들면서 고친 문제

| 문제 | 어떻게 고쳤는지 |
| --- | --- |
| 프로젝트 코드와 엔진 소스를 오가며 찾아야 함 | 프로젝트와 엔진의 검색 자료를 따로 만들고, 작업할 프로젝트의 `.uproject`와 엔진 버전을 기준으로 필요한 코드를 찾게 했습니다. |
| 모델이 읽은 뒤 파일이 바뀌면 잘못된 코드를 덮어쓸 수 있음 | 파일을 읽을 때 받은 버전 증표로 수정 시점의 파일 상태를 확인합니다. 그사이에 파일이 바뀌면 편집을 거부합니다. |
| 도구를 반복해서 쓸 때 앞에서 본 내용을 다시 처리하느라 느려짐 | 모델에 이미 보낸 내용은 같은 자리에 두고, 새로 확인한 내용만 붙여 이전 입력을 재사용하게 했습니다. 대화가 길어지면 Compactor가 오래된 내용을 줄입니다. |

## 확인한 결과

| 확인한 것 | 결과 |
| --- | --- |
| 도구를 7라운드 호출하는 재현 테스트에서 이전 입력 재사용 | 서버 처리 토큰 중앙값 **126,818 → 31,697개**, 입력 처리 시간 중앙값 **132.6 → 35.0초**. [측정 기록](docs/Harness_Behavior_Latency_Final_Plan_20261019.md#92-성능-실측-w-p1--근거) |
| 언리얼 빌드 오류 사전 검사 | 과거 실패 51회에 해당하는 사례를 작은 코드로 옮겨 비교했습니다. 사전 검사에서 잡은 오류 유형에 해당하는 실패가 **5회 → 28회**였습니다. [검사 결과](docs/Harness_Second_Round_Completion_20261024.md#c2-코퍼스와-측정) |

실제 모델로 코드를 찾아 수정하고, 빌드 오류를 보고 다시 고치는 흐름까지 확인했습니다. 별도의 긴 대화 한 건에서는 52라운드 동안 도구를 90번 호출했고 압축이 6번 일어났습니다. 이 대화에서 도구 호출 누락과 컨텍스트 초과는 없었습니다. [대화 검증 기록](docs/Harness_Run_1793426512873_Verification_20261101.md)

압축이 여러 번 쌓인 뒤에는 모델이 앞서 확인한 내용을 놓쳐 작업 흐름이 매끄럽게 이어지지 않는 경우가 있었습니다. 최종 보고서에도 일부 부정확한 내용이 남았습니다.

> Unreal Engine 5를 대상으로 만들었고, Unity 6도 실험적으로 지원합니다. v1.4.7을 마지막으로 업데이트와 유지보수를 종료합니다. [v1.4.7 변경 사항](docs/Release_Notes_1_4_7.md)

## 설치 방법

Windows에서 저장소를 받은 뒤 설치기를 실행합니다.

```powershell
git clone https://github.com/Parkyongseok1120/UE5_Local_LLM_Agent_lmstudio.git
cd UE5_Local_LLM_Agent_lmstudio
.\INSTALL.bat
```

Linux와 Apple Silicon Mac에서는 같은 폴더에서 `./install.sh`를 실행하면 됩니다. Python이 없으면 설치기가 사용자 폴더에 필요한 버전을 설치합니다. [Intel Mac 설치](#intel-mac-설치)

설치 화면에서 프로젝트와 엔진을 고르고 검색 자료를 만들지 선택하면 됩니다. 처음에는 `STANDARD` 구성과 읽기 전용인 `SAFE` 권한으로 시작하면 됩니다. 파일 수정이나 빌드까지 맡기려면 `AGENT` 권한을 선택하세요. 검색 자료를 만들 범위는 설치 구성과 별도로 고릅니다.

ZIP으로 받았다면 계속 보관할 폴더에 풀어 주세요. 설치 후에도 그 폴더의 파일을 씁니다. 첫 설치에서는 필요한 패키지까지 받을 수 있게 `--skip-deps`를 빼고 실행하세요. [설치 안내](docs/Integrated_Installer.md)

위 절차는 Unreal용입니다. Unity는 별도 MCP 서버와 Editor Bridge를 연결해서 씁니다. [Unity 설치 방법과 지원 범위](docs/Unity_Setup.md) · [Unity 검증 기록](docs/Unity_Validation.md)

## LM Studio 연결 설정

1. 설치가 끝나면 LM Studio를 재시작합니다.
2. 모델을 불러온 뒤 채팅에서 선택합니다. 언리얼에서 검색부터 수정까지 여러 도구를 이어 쓰는 작업은 **Muse Glimmer 30B**를 먼저 써보고, 그다음으로 **Qwen 3.8 27B**를 추천합니다. [모델 설정](docs/Model_Profiles.md)
3. 채팅에서 `unreal-rag`와 `unreal-agent`를 켭니다. 언리얼 프로젝트를 작업할 때는 `js-code-sandbox`를 꺼두세요. 작업 폴더가 프로젝트와 다릅니다.
4. 작업할 프로젝트를 지정합니다. 검색 자료를 아직 만들지 않았다면 아래 절차로 만들고 작업을 요청합니다.

설치나 업데이트를 하면 기존 채팅에서 `codex/unreal-context-compactor`가 켜집니다. 필요 없는 채팅에서는 끌 수 있습니다. [LM Studio 설정](docs/LMStudio_Unreal_Agent_Setup.md) · [Rider·Cline 설정](docs/Cline_Rider_Unreal_Agent_Setup.md)

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

## 사용 범위

- 빌드 권한이 있으면 프로젝트 빌드와 자동화 테스트를 돌릴 수 있습니다.
- 블루프린트와 머티리얼 내부를 보려면 에디터에서 자료를 내보내야 합니다. [에디터 자료 내보내기](docs/Editor_Metadata_Export.md)
- 긴 보고서가 출력 한도에 걸리면 대화 압축기가 기존 본문을 남겨 두고 한 번 자동으로 이어 씁니다. 그래도 끊기면 “이어서 써줘”라고 요청하면 됩니다. [보고서 이어쓰기 조건](lmstudio-context-compactor-plugin/README.md#보고서의-출력-한도와-이어쓰기)

기본 모드인 `Direct`에서는 모델이 도구를 고르고, 서버가 프로젝트 접근 범위와 수정 권한을 확인합니다.

## 긴 작업을 맡길 때

프로젝트 전체를 한 번에 검토해 달라고 하기보다, 어디부터 볼지 지정하면 확인하기 편합니다. 예를 들면 이렇게 요청할 수 있습니다.

```text
최근 변경한 커밋부터 보고, 영향이 큰 파일 3개를 먼저 검토해줘.
문제가 있으면 파일이랑 이유를 같이 적어줘.
```

압축이 여러 번 일어나면 모델이 앞에서 읽은 내용이나 이미 시도한 방법을 놓칠 수 있습니다. 같은 일을 반복하기 시작하면 “지금까지 바꾼 파일이랑 남은 문제만 정리해줘”라고 요청하고 그다음 작업을 이어가면 됩니다.

## Intel Mac 설치

Intel Mac에서는 Linux x64 VM에서 `llmster`와 LM Link를 씁니다. 이미 VM이 있다면 VM 안에 저장소를 받고 아래 명령을 실행합니다. 이 구성에서는 GUI용 대화 압축기 대신 헤드리스 압축기를 씁니다.

```sh
./install.sh --profile standard --yes --headless-lmlink
```

VM 생성과 프로젝트 연결까지 한 번에 하려면 호스트 macOS에서 전용 설치기를 실행합니다.

```bash
./install-intel-mac.sh \
  --project /absolute/path/MyGame.uproject \
  --engine-root "/Users/Shared/Epic Games/UE_5.7"
```

처음 실행할 때 로그인이나 연결 승인이 필요하면 터미널에 표시되는 주소를 브라우저에서 열면 됩니다. 설치가 끝나면 프로젝트에서 `./lmstudio-cli.sh`로 채팅을 시작하세요. 이 실행기가 `mcp.json`에 등록된 도구를 연결합니다.

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
| 이번 버전 변경 확인 | [1.4.7 정식 변경 사항](docs/Release_Notes_1_4_7.md), [버전 관리](docs/VERSIONING.md) |

기여 방법은 [CONTRIBUTING.md](CONTRIBUTING.md), 보안 제보는 [SECURITY.md](SECURITY.md), 엔진 자료 취급은 [EPIC_NOTICE.md](EPIC_NOTICE.md)를 참고하면 됩니다.
