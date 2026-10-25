# 버전 관리

제품 전체와 각 구성 요소는 별도 버전을 사용합니다. 같은 숫자로 억지로 맞출 필요는 없습니다.

현재 제품 릴리스는 **1.4.2**이며 배포 구성은 **2.1.33**입니다. 반복 읽기·근거 상태 보존, 인용·엔진 근거 대조, 빌드 전 정적 검사와 범위 편집, CI·배포 검사를 보강했습니다. 자세한 내용은 [1.4.2 정식 변경 사항](Release_Notes_1_4_2.md)에 있습니다.

| 구성 | 현재 표시 | 기준 파일 |
|---|---|---|
| 제품 | 1.4.2 | [installer/manifest.json](../installer/manifest.json) |
| Node 파일·실행 서버 | 0.3.30 | [package.json](../lmstudio-unreal-agent-mcp/package.json) |
| Unity MCP·Editor Bridge | 1.4.0 | [MCP package.json](../lmstudio-unity-mcp/package.json), [Bridge package.json](../unity-editor-bridge/package.json) |
| 근거 검토 서버 | 1.1.4 | [evidence_packet_contract.py](../skills/evidence-first-code-audit/scripts/evidence_packet_contract.py), [evidence_first_mcp.py](../skills/evidence-first-code-audit/scripts/evidence_first_mcp.py) |
| 대화 압축기 | 0.4.83 / revision 130 | [package.json](../lmstudio-context-compactor-plugin/package.json), [manifest.json](../lmstudio-context-compactor-plugin/manifest.json) |
| 압축 배포 구성 | 2.1.33 | [installer/manifest.json](../installer/manifest.json) |

제품을 새로 배포할 때 제품 버전을 올립니다. 도구의 공개 입력·출력이나 동작이 바뀌면 해당 구성 요소 버전을 올립니다. 배포 파일 구성이나 설치 내용이 바뀌면 배포 구성 버전을 검토해야 합니다.

릴리스에는 각 구성 요소 버전을 함께 적습니다. 이미 공개한 안정·시험 버전 태그를 강제로 옮기지 말아야 합니다. 예전 상태가 필요하면 Git 기록과 태그로 확인하면 됩니다.

`portablePackage.releaseReady`는 자동 검사와 배포 준비 상태에 쓰는 값입니다. 모든 장비·엔진·프로젝트에서 실제 실행을 보장하지 않습니다. 반복 읽기 수정의 자동 회귀 결과와 실제 모델의 장기 감사 정확도·속도는 구분하며, 이번 수정 후의 장기 실모델 비교는 미실행입니다. 과거 로컬 설치 기록은 해당 커밋의 문서에 남기고 현재 릴리스 설치 여부와 혼동하지 않습니다.
