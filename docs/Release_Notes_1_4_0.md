# v1.4.0-beta1 변경 사항

v1.4.0-beta1은 Unity용 MCP와 Editor Bridge를 통합 배포본에 추가하고, LM Studio 대화 압축기의 프로젝트 구분·응답 표시·연속성 보존을 개선합니다. Unreal Direct 도구와 Unity 도구는 확인된 프로젝트 종류에 따라 분리해 노출합니다.

## 주요 변경 사항

- Unity 프로젝트용 별도 Node MCP와 UPM Editor Bridge를 추가했습니다. 파일 탐색·읽기, 제한된 수정, Editor 상태, 직렬화 객체, Prefab, 테스트, 심볼·참조, 스냅샷과 명시적 디버그 관찰 기능을 제공합니다.
- Unity 파일 검색은 여러 확장자와 경로 조건을 함께 처리하며, 본문을 전부 읽지 않는 얕은 탐색과 페이지 단위 결과를 사용합니다.
- Unity와 Unreal 프로젝트 도구는 설정값과 정확한 프로젝트 표식을 기준으로 구분합니다. 프로젝트 종류를 확인하지 못한 대화에는 두 엔진의 프로젝트 도구를 보류합니다.
- 대화 압축기는 도구 호출 라운드와 최종 본문을 LM Studio 화면에 진행 중인 상태로 전달하고, 프롬프트 처리 진행률을 표시합니다.
- 압축 뒤 연속성 메모는 별도 모델 없이 제한된 형식으로 파싱합니다. 관찰된 사실과 이전 assistant 판단을 분리하고, 다음 도구나 실행 순서를 메모가 지시하지 못하게 제한합니다.
- 긴 리뷰를 연속 압축할 때 사용자 요청의 앞·뒤 문맥과 여러 Git 관찰을 bounded checkpoint에 보존합니다. 원시 reasoning은 연속성 사실로 승격하지 않으며, 라운드별 호출·결과 fingerprint는 디버그 관찰에만 사용합니다.
- Workspace Git 결과는 요청 경로 기준, 실제 repository 경로, base·head·현재 HEAD와 결과 완전성을 구조화해 반환합니다. 서버는 잘못된 경로를 추측 보정하거나 다음 도구와 완료 여부를 결정하지 않습니다.
- 설치·업데이트 시 `codex/unreal-context-compactor`를 설치하고 저장된 기존 채팅에서 기본 활성화합니다. `UPDATE.bat`은 설치된 Unity MCP, Unreal Agent/RAG와 압축기를 함께 갱신하며 기존 프로젝트·엔진·인덱스·권한 설정을 보존합니다.
- 릴리스 브랜치에서도 GitHub Actions가 실행되며 Unity MCP, Unreal Direct MCP, 대화 압축기, 휴대용 설치 패키지를 Windows·Ubuntu·macOS에서 검증합니다.

## 구성별 버전

| 구성 | 버전 |
|---|---|
| 통합 제품 | 1.4.0 beta 1 (`v1.4.0-beta1`) |
| 휴대용 배포 구성 | 2.1.22 |
| Unreal Node MCP | 0.3.23 |
| Unity MCP·Editor Bridge | 1.4.0-beta.4 |
| 근거 검토 서버 | 1.1.1 |
| 대화 압축기 | 0.4.61 / revision 108 |

## Unity beta 범위

Unity 구성 요소는 통합 제품 v1.4.0-beta1에 포함되는 beta 계열입니다. 구현 기준은 Unity 2022.3 이상에서 제공되는 공통 Editor API이며, 실제 Editor 통합 검증은 Unity 6000.3.14f1 Intel macOS 중심입니다. 다른 Unity 버전과 Windows·Linux Editor의 전체 통합 실행을 보장하지 않습니다.

서버와 Bridge는 내부 모델, Planner, 자동 수정 루프를 실행하지 않습니다. 모델이 선택한 명시적 도구 요청만 처리하며 파일 범위, 권한, receipt와 프로젝트·Editor 세션을 다시 확인합니다. 자세한 기능과 검증 범위는 [Unity 설치·지원 범위](Unity_Setup.md), [Unity 검증 기록](Unity_Validation.md)에 있습니다.

## 검증과 배포 경계

릴리스는 전체 GitHub Actions CI, 세 운영체제의 Node MCP·대화 압축기 검사, Windows Python 전체 회귀·반복 검사, 정적·인코딩·PowerShell 검사, 깨끗한 휴대용 패키지 생성과 인벤토리 해시 확인을 통과한 커밋에서 만듭니다.

자동 검사는 모든 장비, 엔진 버전, 프로젝트, 플러그인 조합을 인증하지 않습니다. 배포 ZIP과 함께 제공되는 SHA-256 파일로 다운로드한 패키지의 무결성을 확인할 수 있습니다.
