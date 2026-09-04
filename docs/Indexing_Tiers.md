# 검색 자료 수집 범위 선택

수집 범위가 넓을수록 AI가 찾아볼 자료가 늘어나지만 만드는 시간과 디스크 사용량도 늘어납니다. 프로젝트 코드 위주로 쓴다면 보통 `standard`부터 확인하면 됩니다.

| 범위 | 수집하는 내용 | 쓸 때 |
|---|---|---|
| `lite` | 프로젝트 C++·설정 파일, 에셋 경로 | 작고 빠르게 프로젝트 텍스트만 찾을 때 |
| `standard` | 위 내용에 엔진 공개 함수·클래스, 프로젝트 선언 정보와 구조 요약 추가 | 일반적인 코드 검토와 수정 |
| `full` | 위 내용에 `Engine/Source` 전체 텍스트 추가 | 엔진 내부 구현까지 따라가야 할 때 |

`.uasset`와 `.umap` 경로를 모았다고 내부 노드를 읽은 건 아닙니다. 블루프린트와 머티리얼 내부는 [에디터 자료 내보내기](Editor_Metadata_Export.md)가 별도로 필요합니다.

## 설치 구성과 수집 범위의 차이

`--profile full`은 설치 구성 이름입니다. 검색 자료 전체 수집을 뜻하지 않습니다. 자료를 만들려면 `--build-rag`, 깊이를 고르려면 `--index-tier`를 사용합니다.

```powershell
python install.py --profile standard --yes --build-rag --index-tier standard
```

`install.py --build-rag`는 설치기가 관리하는 Python으로 수집과 검색 자료 생성을 직접 실행합니다. PowerShell 없이 가능합니다. 에디터를 실행하거나 프로젝트 플러그인을 설치하지 않습니다.

## 프로젝트와 저장 위치

설치 화면에서 `.uproject`를 선택하면 기본 프로젝트로 지정됩니다. 폴더를 선택하면 그 안에서 프로젝트를 찾습니다. `standard`와 `full`은 선택한 엔진과 맞는 프로젝트만 같은 자료에 넣습니다. 맞지 않는 프로젝트는 이유를 알리고 제외하며, 활성 프로젝트를 조용히 빼지는 않습니다.

공유 설정은 `~/.lmstudio/config/unreal-workspace.json`에 저장합니다. `indexingTier`가 수집 범위입니다. 데이터베이스는 별도로 `~/.evidence-first/indexes/<namespace>/rag.sqlite`에 둡니다. `--state-home`으로 상위 위치를 바꿀 수 있습니다.

같은 이름의 프로젝트도 실제 경로가 다르면 구분합니다. 엔진별 저장 폴더도 분리합니다. 예전 자료의 출처를 확인할 수 없으면 다시 수집해야 합니다.

## 수집 범위 축소 시 기존 자료 정리

`full`에서 낮추면 엔진 전체 소스 입력인 `raw_source.jsonl`을 제거하고 다시 만듭니다. `lite`로 낮추면 이전 엔진 선언 정보와 프로젝트 요약 입력도 정리합니다. 예전 모듈 그래프 입력은 새 검색 자료에서 사용하지 않습니다.

## 검색 자료 갱신 명령

```powershell
.\rag.ps1 set-project -ProjectFile C:\Projects\MyGame\MyGame.uproject
.\rag.ps1 refresh -RefreshScope project_source
.\rag.ps1 doctor
```

에디터에서 이미 내보낸 자료만 읽으려면 `-RefreshScope editor_metadata`를 사용합니다. 에디터 실행까지 의도했을 때만 `-RefreshScope editor_metadata -AllowEditorLaunch`를 사용합니다.

Linux와 macOS에서 위 유지보수 명령을 쓸 때는 `pwsh`가 필요합니다. 설치 자체의 필수 조건은 아닙니다.
