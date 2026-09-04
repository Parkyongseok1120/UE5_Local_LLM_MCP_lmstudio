# 에디터에서 블루프린트·머티리얼 자료 내보내기

일반 코드 검색은 `.uasset` 안의 노드 연결까지 읽지 못합니다. 내부 구조를 AI에게 보여주려면 Unreal Editor에서 자료를 내보내고 검색 데이터베이스에 넣어야 합니다.

기본 설치나 `install.py --build-rag`는 에디터를 열거나 프로젝트를 수정하지 않습니다. 이 작업은 필요한 프로젝트에서 별도로 진행해야 합니다.

## 지원 자료와 내보내기 스크립트

스크립트는 `tools/ue_export/`에 있습니다.

| 스크립트 | 내용 |
|---|---|
| `export_blueprint_metadata.py` | 블루프린트의 부모 클래스·변수·함수·그래프 |
| `export_material_metadata.py` | 머티리얼·인스턴스·함수·레이어·매개변수 |
| `export_texture_metadata.py` | 텍스처 종류와 설정 |
| `export_mesh_metadata.py` | 메시 슬롯·LOD·지오메트리 컬렉션 |
| `export_world_look_metadata.py` | 후처리·하늘·안개·데이터 레이어 |
| `export_structured_asset_metadata.py` | 데이터 테이블·나이아가라·AI·오디오·입력·UI·GAS |
| `export_animation_metadata.py` | 애니메이션·스켈레톤·물리 에셋·리그·시퀀서 |
| `export_fmod_metadata.py` | FMOD 플러그인이 있을 때 이벤트·뱅크 |
| `export_asset_registry.py` | 에셋 목록과 종류 |
| `export_project_settings.py` | 프로젝트 설정 |
| `export_level_metadata.py` | 맵 정보 |

## 블루프린트·머티리얼 일괄 내보내기

Unreal Editor의 Python에서 실행합니다. `editor_tools`와 `project_exports`를 실제 경로로 바꿔야 합니다. 통합 스크립트는 `tools/ue_export/run_all_exports.py`입니다.

```python
editor_tools = r'C:\Tools\UE5_Local_LLM_MCP_lmstudio\tools\ue_export'
exec(open(editor_tools + r'\run_all_exports.py', encoding='utf-8').read())
project_exports = r'C:\Projects\MyGame\Saved\LmStudioMetadataExports'
run_all_metadata_exports(project_exports, content_path='/Game', tools_dir=editor_tools)
```

특정 폴더만 필요하면 `content_path`를 `/Game/Environment`처럼 좁히면 됩니다. 머티리얼만 내보내려면 같은 스크립트의 `export_materials_only(project_exports, content_path='/Game', tools_dir=editor_tools)`를 사용합니다.

## 블루프린트 노드 연결 정보 내보내기

UE 5.8에서는 Python으로 `EdGraph.Nodes`를 직접 읽는 데 제한이 있어 전체 노드·핀 연결에 C++ 에디터 플러그인이 필요합니다.

통합 설치기는 이 플러그인을 복사하거나 활성화하지 않습니다. 사용하려면 에디터를 닫고 `tools/ue_plugins/LmStudioGraphExporter`를 해당 프로젝트의 `Plugins` 아래에 복사한 뒤 `.uproject`에서 활성화해야 합니다.

플러그인이 있으면 내보내기 스크립트가 `graphs`, `nodes`, `pins`, `graph_links`를 가져옵니다. 없으면 Python에서 읽을 수 있는 부모 클래스·그래프 이름·변수·함수·의존성까지만 나올 수 있습니다. 노드 정보가 없다고 연결이 없다고 판단하지 말아야 합니다.

## 내보낸 자료의 검색 데이터베이스 반영

내보낸 파일은 정확한 프로젝트의 `Saved/LmStudioMetadataExports`에 둡니다. 다른 폴더를 지정한 예전 `editorExportDir` 설정은 기본 Direct 갱신에서 사용하지 않습니다. 어느 복사본에서 나온 자료인지 확인하기 위해서입니다.

```powershell
.\rag.ps1 set-project -ProjectFile C:\Projects\MyGame\MyGame.uproject
pwsh -NoProfile -File .\rag.ps1 refresh -RefreshScope editor_metadata
```

이 명령은 이미 있는 자료만 읽습니다. 에디터 실행까지 의도한 경우에만 다음 명령을 사용해야 합니다.

```powershell
pwsh -NoProfile -File .\rag.ps1 refresh -RefreshScope editor_metadata -AllowEditorLaunch
```

MCP의 `unreal_rag_refresh`도 기본은 `allowEditorLaunch=false`입니다. 실행하려면 에디터 범위와 `allowEditorLaunch=true`를 함께 지정해야 합니다.

프로젝트 실제 경로와 이름을 자료마다 기록합니다. 이름이 같아도 다른 폴더의 복사본과 합치지 않습니다. 예전 자료의 출처가 불명확하면 다시 내보내야 합니다. 검색 자료는 해당 프로젝트의 엔진별 저장소에 들어갑니다.

## 내보내기 결과와 누락 정보 확인

자료 유형은 [unreal_asset_taxonomy.json](../config/unreal_asset_taxonomy.json)에 정의되어 있습니다. `scripts/asset_taxonomy.py`의 `classify_ue_asset_class`, `taxonomy_text_lines`가 분류와 설명에 사용됩니다.

| 자료 구분 | 의미 |
|---|---|
| `graph_material` | 머티리얼 연결 정보 |
| `graph_blueprint` | 블루프린트 연결 정보 |
| `graph_animation` | 애니메이션 관련 구조 |
| `structured_metadata` | 설정·참조 등 정리된 속성 |
| `registry` | 에셋 경로·클래스·분류 |
| `path_only` | 경로만 확인됩니다 |

갱신 후 `unreal_rag_search`로 에셋을 찾으면 됩니다. 자료를 읽는 것과 실제 노드를 고치는 것은 별개입니다. `.uasset` 수정은 에디터 안에서 처리해야 합니다. [자료별 확인 범위](Blueprint_Metadata.md)도 참고해야 합니다.
