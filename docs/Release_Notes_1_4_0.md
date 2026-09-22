# v1.4.0-beta3 변경 사항

v1.4.0-beta3는 beta2 이후의 실제 장시간 조사 경로를 기준으로 출력 예산, Hybrid 작업 문맥, Git 결과 재조회, 구조화 도구 경계와 false completion 방지를 보강합니다. 기존 deterministic 압축과 안전 경계는 유지하며, Unreal Direct 도구와 Unity 도구는 확인된 프로젝트 종류에 따라 분리해 노출합니다.

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
- bounded 조사 출력이 한도에 도달해도 같은 모델의 도구 없는 최종 보고 기회를 최대 한 번 보장하며, 최종 timeout·취소·잘림을 별도 상태로 기록합니다.
- 현재 SDK 입력을 기준으로 파일 버전·반환 범위·재획득 범위를 측정하고, Unreal line-range와 byte-window 계약을 구분합니다. 평가 oracle과 실제 제품 projection을 분리합니다.
- Git 결과에 관찰된 author·committer와 repository 책임 범위를 혼합하지 않고, 확인되지 않은 책임자를 추정하지 않습니다.
- 평가 하네스는 정상 접근 기준선, controlled 압력 조건, causal modelInputId, 실패·timeout 보존을 사용해 종료 동작과 보고서 정확도를 별도로 검증합니다.
- 생성 예산 resolver가 입력 적합성, 실제 act의 `maxTokens`, 출력 reserve와 safety headroom을 연결합니다. `maxPredictedTokensReached`를 context 초과, reasoning·도구 인수·일반 보고서·강제 최종 보고의 잘림과 구분하고, 보고서가 잘린 경우 허용된 추가 보고 기회를 최대 한 번만 사용합니다.
- Hybrid를 기본 작업 문맥으로 사용하되 deterministic 모드도 유지합니다. 확인된 도구 결과를 scope별 evidence archive에 보관하고 bounded projection으로 다시 조립하며, compacted prefix·scope·lineage와 새 delta가 검증된 경우에만 다음 입력으로 원자적으로 교체합니다.
- Hybrid semantic handoff는 압축 이벤트당 최대 한 번의 도구 없는 호출로 제한합니다. 잘못된 JSON, 잘못된 reference, length·timeout·취소는 note로 commit하지 않고 이전 note와 deterministic facts로 돌아갑니다. summary는 assistant claim이며 빌드·쓰기 승인·전체 검토 완료 같은 실행 사실을 만들지 않습니다.
- Git 대형 결과는 8,192자 first-consumer 기준과 전체 batch fit을 분리하고, 본문 중심 bounded view를 우선 제공합니다. `limit`·`byteBudget`·`page`·`cursor`와 실제 반환량을 연결하여 source page와 archive page, source EOF와 archive EOF를 구분합니다.
- `git_diff_file` 등 공개 registry/schema에 없는 raw tool 이름이나 XML·pseudo-XML 호출은 실행하거나 인수로 복원하지 않습니다. 원래 허용된 읽기 과업과 현재 registry를 기준으로 새 structured planning을 최대 한 번 생성하며, 정상 source/archive pagination 진행과 malformed-call recovery counter를 분리합니다.
- Git 조사 진행 여부는 응답 fingerprint나 timing metadata가 아니라 성공한 source/version/range의 실제 추가 coverage로 계산합니다. 같은 page 재조회, MCP 내부 volatile metadata 변경, 이미 읽은 범위의 부분 재조회, duplicate success와 새 error의 결합은 신규 진행으로 세지 않으며, no-progress 시 부분 보고를 전달하되 task complete로 승격하지 않습니다.
- read-only Git 결과를 첫 소비 전에 작은 metadata projection으로 치환해 다시 읽게 만드는 경로를 줄이고, archive-only recovery에서도 verified Git read catalogue와 archive reader를 함께 유지합니다. 기간·저자·실제 읽은 범위와 archive ref를 보존하며, 필요한 원문 재획득과 불필요한 중복 재획득을 구분합니다.
- prediction 전후 템플릿·token 계수, exposed tool schema, 실제 요청 상한, reasoning·visible 소비량, SDK lifecycle, guard, dispatch·result를 동일 `modelInputId`로 연결합니다. Git subprocess, serialization, model prefill과 summary 비용도 별도로 계측합니다.
- Human-Bartender 실제 임시 Git 저장소와 공개 schema를 연결한 통합 회귀를 추가하고, 232·650개 paging, cursor 오류·TTL·cache eviction·서버 재시작 구분, 최신 다중 호출 pairing, raw tool 경계, archive 재조회와 no-progress를 검증합니다. Unity·Unreal read/write/build·approval·receipt·취소·BOUNDED 회귀도 유지합니다.
- 긴 조사 작업의 사용 경계를 README에 정리했습니다. 현재는 변경 커밋 목록, 핵심 파일 선정, 제한된 파일 분석처럼 중간 경계를 두면 안정적이며, 장기 목표는 기존 압축·archive·scope·safety 기반 위에서 bounded context와 근거 재조회를 자동으로 이어 최종 보고까지 완주하는 것입니다.

## 구성별 버전

| 구성 | 버전 |
|---|---|
| 통합 제품 | 1.4.0 beta 3 (`v1.4.0-beta3`) |
| 휴대용 배포 구성 | 2.1.22 |
| Unreal Node MCP | 0.3.23 |
| Unity MCP·Editor Bridge | 1.4.0-beta.4 |
| 근거 검토 서버 | 1.1.1 |
| 대화 압축기 | 0.4.66 / revision 113 |

## Unity beta 범위

Unity 구성 요소는 통합 제품 v1.4.0-beta3에 포함되는 beta 계열입니다. 구현 기준은 Unity 2022.3 이상에서 제공되는 공통 Editor API이며, 실제 Editor 통합 검증은 Unity 6000.3.14f1 Intel macOS 중심입니다. 다른 Unity 버전과 Windows·Linux Editor의 전체 통합 실행을 보장하지 않습니다.

서버와 Bridge는 내부 모델, Planner, 자동 수정 루프를 실행하지 않습니다. 모델이 선택한 명시적 도구 요청만 처리하며 파일 범위, 권한, receipt와 프로젝트·Editor 세션을 다시 확인합니다. 자세한 기능과 검증 범위는 [Unity 설치·지원 범위](Unity_Setup.md), [Unity 검증 기록](Unity_Validation.md)에 있습니다.

## 검증과 배포 경계

v1.4.0-beta3는 Develop에 병합된 `bc39f44f4b9ee2f635179f5e892d12984a3d48bd`에서 만들었으며, PR #20의 GitHub Actions 전체 매트릭스가 통과한 뒤 `v1.4.0-beta3` 태그로 게시한 prerelease입니다. 검증에는 전체 GitHub Actions CI, 세 운영체제의 Node MCP·Unity MCP·대화 압축기 검사, Windows Python 전체 회귀·반복 검사, 정적·인코딩·PowerShell 검사, 깨끗한 휴대용 패키지 생성과 인벤토리 해시 확인이 포함됩니다.

자동 검사는 모든 장비, 엔진 버전, 프로젝트, 플러그인 조합을 인증하지 않습니다. 배포 ZIP과 함께 제공되는 SHA-256 파일로 다운로드한 패키지의 무결성을 확인할 수 있습니다.
