# 버전 관리

제품 전체와 각 구성 요소는 별도 버전을 사용합니다. 같은 숫자로 억지로 맞출 필요는 없습니다.

| 구성 | 현재 표시 | 기준 파일 |
|---|---|---|
| 제품 | 1.3.3 | [installer/manifest.json](../installer/manifest.json) |
| Node 파일·실행 서버 | 0.3.22 | [package.json](../lmstudio-unreal-agent-mcp/package.json) |
| 근거 검토 서버 | 1.1.1 | [evidence_packet_contract.py](../skills/evidence-first-code-audit/scripts/evidence_packet_contract.py), [evidence_first_mcp.py](../skills/evidence-first-code-audit/scripts/evidence_first_mcp.py) |
| 대화 압축기 | 0.4.51 / revision 98 | [package.json](../lmstudio-context-compactor-plugin/package.json), [manifest.json](../lmstudio-context-compactor-plugin/manifest.json) |
| 압축 배포 구성 | 2.1.17 | [installer/manifest.json](../installer/manifest.json) |

제품을 새로 배포할 때 제품 버전을 올립니다. 도구의 공개 입력·출력이나 동작이 바뀌면 해당 구성 요소 버전을 올립니다. 배포 파일 구성이나 설치 내용이 바뀌면 배포 구성 버전을 검토해야 합니다.

릴리스에는 각 구성 요소 버전을 함께 적습니다. 이미 공개한 안정·시험 버전 태그를 강제로 옮기지 말아야 합니다. 예전 상태가 필요하면 Git 기록과 태그로 확인하면 됩니다.

`portablePackage.releaseReady`는 자동 검사와 배포 준비 상태에 쓰는 값입니다. 모든 장비·엔진·프로젝트에서 실제 실행을 보장한다는 뜻은 아닙니다. 변경 내용은 [1.3.3 변경 사항](Release_Notes_1_3_3.md)에 있습니다.
