# 에셋 종류와 읽을 수 있는 범위

에셋 파일이 검색된 것과 내부 구조가 읽힌 것은 다릅니다. AI가 노드·핀·연결을 설명하기 전에 어떤 자료가 있는지 확인해야 합니다.

| 자료 수준 | 알 수 있는 것 |
|---|---|
| `path_only` | `.uasset` 경로와 이름 |
| `registry` | 에셋 경로·클래스·분류 |
| `graph_material` | 내보낸 머티리얼 표현식·연결·매개변수 |
| `graph_blueprint` | 내보낸 블루프린트 그래프·노드·핀 |
| `graph_animation` | 내보낸 애니메이션·몽타주·시퀀서 구조 |
| `structured_metadata` | 설정값과 다른 에셋 참조 등 정리된 정보 |

그래프 자료가 없는 에셋을 이름만 보고 해석하지 말아야 합니다. 세부 클래스별 대응은 [unreal_asset_taxonomy.json](../../config/unreal_asset_taxonomy.json)이 기준입니다. 문서에 같은 큰 표를 복제하지 않습니다. `scripts/asset_taxonomy.py`의 `classify_ue_asset_class`, `taxonomy_text_lines`가 이 분류를 사용합니다.

## 작업별 에셋 분류

- 렌더링: 메시, 머티리얼·함수·레이어·인스턴스, 텍스처, 조명과 후처리를 포함합니다.
- 월드·레벨: 맵, 배치와 데이터 레이어를 포함합니다.
- 캐릭터·애니메이션: 스켈레톤, 애니메이션 블루프린트, 몽타주, 리그, 시퀀서를 포함합니다.
- 게임 동작: 블루프린트, 입력, 물리, GAS, AI 설정을 포함합니다.
- 표현·데이터: UI, 사운드, 시각 효과, 데이터 테이블과 곡선을 포함합니다.
- 에디터·배포: 에셋 목록, 참조, 내보내기와 패키징 설정을 포함합니다.

카툰처럼 사실적인 표현과 다른 스타일에서는 머티리얼 함수, 후처리, 전역 조정값을 함께 확인합니다. Material Function과 Material Layer는 `graph_material` 자료를 확인하고, Material Parameter Collection은 주로 스칼라·벡터 기본값이며 노드 그래프와 구분합니다.

내보내는 방법은 [에디터 자료 안내](../../docs/Editor_Metadata_Export.md)에 있습니다. 내부 노드 변경은 에디터에서 수행하고 저장·검증 결과가 있어야 완료라고 적습니다.
