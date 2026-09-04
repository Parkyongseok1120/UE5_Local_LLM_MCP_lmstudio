# 프로젝트 검색 자료 생성과 갱신

RAG는 AI가 답하기 전에 필요한 코드나 문서를 찾아보는 방식입니다. 여기서는 엔진과 프로젝트를 읽어서 로컬 검색 데이터베이스인 `rag.sqlite`를 만듭니다. 본인이 사용할 권한이 있는 자료로 만들고, 엔진 소스나 비공개 프로젝트가 들어간 데이터베이스를 공개하지 말아야 합니다.

## 검색 자료 최초 생성

```powershell
python install.py --profile standard --yes --build-rag --index-tier standard --engine-root C:\UE_5.8 --active-project C:\Projects\MyGame\MyGame.uproject
```

Python이 없다면 먼저 `INSTALL.bat` 또는 `install.sh`로 설치를 시작하면 됩니다. 수집 범위는 [수집 범위 선택](Indexing_Tiers.md)을 참고해야 합니다.

검색 자료는 기본적으로 `~/.evidence-first/indexes/<namespace>/rag.sqlite`에 저장됩니다. `<namespace>`는 `unreal58` 같은 엔진별 폴더 이름입니다. `--state-home`으로 저장 위치를 바꿀 수 있습니다. 사용자가 따로 지정한 외부 `indexPath`는 그대로 둡니다.

## 프로젝트 코드 변경 후 검색 자료 갱신

```powershell
.\rag.ps1 set-project -ProjectFile C:\Projects\MyGame\MyGame.uproject
.\rag.ps1 refresh -RefreshScope project_source
.\rag.ps1 doctor
```

`project_source`는 프로젝트 코드와 설정, 구조 요약, `Build.cs`, 함수·클래스 정보를 다시 읽고 검색 자료를 갱신합니다. 기본 `refresh`도 이 범위입니다. 에디터는 실행하지 않습니다.

프로젝트 이름만 넣는 예전 `collect-symbols` 명령 대신 위 방법을 써야 합니다. 같은 이름의 프로젝트 복사본을 정확히 구분하려면 `.uproject` 전체 경로가 필요합니다.

기본 프로젝트를 해제하려면 `.\rag.ps1 clear-project`를 사용합니다.

## 엔진 소스 재수집

```powershell
.\rag.ps1 collect-source -Root C:\UE_5.8\Engine\Source
.\rag.ps1 build-incremental
```

엔진 전체 소스 수집은 시간과 디스크를 많이 씁니다. 프로젝트 코드만 바뀐 경우에는 필요 없습니다. 엔진 공개 함수·클래스 정보만 수집하는 명령은 다음과 같습니다.

```powershell
.\rag.ps1 collect-symbols -Root C:\UE_5.8\Engine\Source -Tier public -SymbolScope engine
```

## 블루프린트와 머티리얼 자료

에디터에서 내보낸 파일을 선택한 프로젝트의 `Saved/LmStudioMetadataExports`에 둔 뒤 실행합니다.

```powershell
.\rag.ps1 refresh -RefreshScope editor_metadata
```

이 명령도 에디터를 열지 않습니다. 새 자료를 내보내기 위해 에디터 실행까지 의도한 경우에만 다음 옵션을 사용합니다.

```powershell
.\rag.ps1 refresh -RefreshScope editor_metadata -AllowEditorLaunch
```

내보내는 방법과 읽을 수 있는 범위는 [에디터 자료 내보내기](Editor_Metadata_Export.md)에 있습니다.

## 여러 프로젝트·엔진의 검색 자료 관리

프로젝트 자료에는 실제 프로젝트 폴더(`project_root`)와 `.uproject` 파일명에서 확장자를 뺀 이름(`project`)을 함께 기록합니다. 이름만 같다고 같은 프로젝트로 취급하지 않습니다.

엔진 버전이나 사용자 지정 엔진 연결도 따로 기록합니다. `build_manifest.json`으로 맞는 자료인지 확인한 뒤 검색합니다. 한 번의 검색에서 서로 다른 엔진의 자료를 합치지 않습니다.

- `RAG_MULTI_ENGINE_QUERY_UNSUPPORTED`: 엔진이 다른 프로젝트를 한 번에 요청한 경우입니다. 나눠서 검색해야 합니다.
- `RAG_ENGINE_INDEX_MISMATCH`: 선택한 엔진에 맞는 자료가 없거나 맞지 않습니다. 해당 엔진으로 자료를 만들어야 합니다.
- 출처가 불명확한 오래된 자료는 임의로 현재 프로젝트에 붙이지 않습니다. 정확한 프로젝트를 선택하고 다시 수집해야 합니다.

## 갱신 명령 실행 오류 해결

Windows에서 스크립트 실행 정책에 걸리면 시스템 설정을 바꾸는 대신 이번 명령에만 적용할 수 있습니다.

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\rag.ps1 doctor
```

Linux와 macOS에서는 PowerShell Core로 `pwsh ./rag.ps1 doctor`처럼 실행합니다. `rag.ps1`은 수집·갱신·상태 확인용입니다. 질문은 LM Studio나 Cline에서 `unreal_rag_search`, `unreal_symbol_lookup`을 통해 하면 됩니다.

자료를 다시 만든 뒤 실행 중인 MCP가 이전 파일을 계속 읽는다면 MCP를 재시작해야 합니다. 엔진 자료 공개 제한은 [EPIC_NOTICE.md](../EPIC_NOTICE.md)를 참고하면 됩니다.
