# 2차 후속 작업 통합 및 빌드 실패 코퍼스 검증

2026-10-24. 기준 브랜치는 `Develop`의 `c89db44`다. D1, B1·B2, C1·C3·C5의 완료 커밋을 통합하고 C2를 구현했다. 이 작업에서는 push, 로컬 설치, LM Studio 설정 변경을 하지 않았다. 기존 `unity-symbol-worker/bin/Release/net8.0`의 DLL/EXE/PDB 변경은 커밋에서 제외한다.

## 통합된 작업

| 작업 | 원래 커밋 | Develop 통합 커밋 | 결과 |
|---|---|---|---|
| D1 | `ebfb58e` | `1c56497` | 실행 시간·토큰·projection·파일/줄·지침·쓰기/빌드 결과 분석 CLI |
| B1·B2 | `752c9ba` | `8481b2a` | 보관 본문 최대 24,576자, 소스 줄 범위 재읽기, projection의 선언 개요, 이전 발화의 출처 표시 |
| C1·C3·C5 | `b80e3c7` | `f26c5f8` | 변경 파일 빌드 사전검사, 해시를 확인하는 줄 범위 편집, 빌드 성공 후 관련 Automation 테스트 제안 |
| C2 | 완료된 산출물 미발견 | 이번 후속 커밋 | 실제 실패 기록을 바탕으로 한 축약 코퍼스·정적 경고·전후 측정 |

`generated-runtime-identity.ts` 충돌은 합쳐진 소스에서 재생성했다. C1의 사전검사는 서버가 기록한 Source 변경만 대상으로 하며, 새 C2 규칙은 모두 경고다. C1은 기존의 명확한 generated.h/구조 오류만 빌드를 막고 `force` 재시도를 지원한다. 실제 UHT/UBT를 정적 검사로 대체하지 않는다.

## 통합 검토에서 수정한 문제

- 포터블 실행 목록에 빠졌던 `direct-build-change-ledger.js`, `direct-build-precheck.js`, `line-range-hash.js`와 새 Python 규칙 모듈을 포함했다. 기존 Node require 의존성 폐쇄 검사 및 별도 디렉터리의 Python import 검사를 사용한다.
- `<n>\t` 줄 번호가 붙은 실제 읽기 응답에서도 source outline이 원래 선언을 찾는다. 플러그인의 독립 컴파일 경계를 지켜 외부 공유 JS를 직접 import하지 않는다.
- 시작 줄을 모르는 비영(非零) byte offset 보관본에는 임의의 소스 줄 번호를 부여하지 않는다. 줄 범위 요청은 오류와 문자 offset 재시도 안내를 반환한다.
- 줄 범위 편집도 이전 본문+새 본문의 합계 패치 제한을 적용한다. 단일 편집과 bundle 모두 실패 시 파일이 그대로인지 확인한다.
- 주석·문자열의 `UCLASS()`나 가짜 include를 generated.h 차단 근거로 삼지 않는다. 주석 마스킹 후에도 원래 줄 번호를 보존한다.
- 빌드가 진행되는 동안 변경 기록의 용량이 초과되면, 이전 빌드의 성공 처리로 그 사실을 지우지 않는다.
- 분석기는 본문 없는 읽기 메타데이터를 반환 줄 수로 세지 않는다. 오류 코드 없는 실패를 성공으로 기록하지 않으며, 지침 선택이 같은 라운드도 이유·생략 사유를 출력한다.

## C2 코퍼스와 측정

원본 대화에서 선택된 버전의 빌드 호출 57회 중 실패 51회를 확인했다. 실패 원인별 39개 사례로 묶었으며 22개 사례(실패 30회)에 대상 규칙을 지정했다. 나머지 17개 사례(실패 21회)도 분모에 남긴다. 각 관측의 대화 ID·빌드 순서·오류 메시지와 축약 broken/fixed 소스는 [코퍼스](../tests/fixtures/ue_build_failures/corpus.json)에 있다. 전체 대화·실제 프로젝트 스냅샷은 저장소에 추가하지 않았다.

기준은 `git archive f26c5f8 scripts config`로 별도 임시 디렉터리에 추출한 변경 전 Python 코드다. 같은 코퍼스와 로컬 설치 UE **5.7.4** 헤더를 사용했다. 결정적 단위 테스트의 작은 가상 엔진과 이 실측은 별개다.

| 지표 | 변경 전 | 변경 후 |
|---|---:|---:|
| 대상 경고가 broken에 있고 fixed에는 없는 사례 | 1 / 39 | 20 / 39 |
| 그 사례에 대응하는 과거 실패 횟수 | 5 / 51 (9.8%) | 28 / 51 (54.9%) |
| fixed에도 대상 경고가 남은 사례 | 0 | 0 |

이 수치는 **축약 소스에서 기록된 오류 유형의 탐지 여부**다. 과거 프로젝트 전체를 재컴파일한 결과나 실제 빌드 실패율 감소가 아니다. fixed 판정도 지정된 경고 코드가 사라지는지만 확인하며 모든 경고의 오탐률 또는 완전한 빌드 성공을 뜻하지 않는다. 전후 사례별 결과와 코퍼스 hash는 [측정 JSON](evidence/ue-build-corpus-20261024.json)에 보관한다.

추가한 경고는 잘못된 엔진 include 경로, 중복 reflected 타입, 부모 UPROPERTY 가림, 부모 UFUNCTION 재선언, RepNotify 선언 누락, 대응 헤더의 첫 include, 단순 override 시그니처 불일치, 관측되지 않은 명시적 엔진 멤버를 다룬다. 엔진 조회는 기존 헤더 카탈로그·상속 탐색을 재사용하며 호출당 읽기 제한과 전체 조회 횟수/시간 제한을 둔다. 한도가 끝나거나 부모 타입을 확인하지 못하면 부재로 판단하지 않는다. 임의의 외부 라이브러리 include를 엔진 include로 분류하지 않는다.

실측에서 `OnRep_TeamId` 누락 및 `APlayerController`의 잘못된 멤버 사례는 제한된 헤더/상속 탐색으로 판정하지 못했다. 그 2건과 규칙을 지정하지 않은 21건은 탐지하지 못한 채 남는다. 큰 Actor 헤더의 읽기 제한을 해제하거나 전체 엔진 타입 스캔을 추가하지 않았다.

정상 `TakeDamage`의 `struct FDamageEvent const&` 표기와 `const FDamageEvent&`를 같게 처리한다. 값 매개변수의 최상위 cv와 이름 없는 `unsigned int`도 보존·정규화한다. 부모 오버로드가 여러 개인데 일부만 반환된 경우 불일치 판단을 생략한다. `FKey`/`EKeys`는 실제 `InputCoreTypes.h` 선언을 확인하며 `_API` export 표기가 붙은 필드도 읽는다. 같은 변수 이름이 다른 함수에 등장해도 타입을 넘겨 쓰지 않는다.

재측정 명령:

```powershell
python scripts/evaluate_ue_build_corpus.py --engine-root 'C:\Program Files\Epic Games\UE_5.7'
python scripts/analyze_lmstudio_run.py 1792829847868 --message-index 3 --compare 1792815622255 --compare-message-index 3
```

## 검증과 남은 실행 작업

- 플러그인: TypeScript 빌드 및 937개 테스트 통과. runtime identity·Unreal/Unity tool contract·design guidance 생성물 검사 통과.
- Unreal MCP: 358개 통과, 2개 플랫폼 조건 skip. PATH는 테스트 프로세스에만 Windows System32 및 사용 도구 경로를 지정했다.
- Python: 정적 규칙·엔진 조회·분석기·교차 파일 검사·패키징 묶음은 169개 통과, 기존 배포 문서 링크 문제 1개 실패. 공유 엔진 선언/정의 조회·계약·파서·도구 manifest 회귀 검사는 별도 78개 통과했다. 서로 다른 테스트 파일 기준 합계 247개 통과, 1개 실패다.
- 기존 배포 문서 문제: `test_package_has_all_platform_launchers_and_no_local_state`의 `docs/VERSIONING.md → Harness_Audit_Accuracy_Implementation_20261020.md` 링크 누락은 재현된다. 해당 문서/배포 정책은 이번 변경 범위에서 수정하지 않았다. 포터블 플러그인의 실제 의존성 설치·컴파일 검사는 통과했다.
- [구조화 작업 근거](evidence/harness-second-round-completion-20261024.json)의 형식 검사를 수행한다. 형식 통과는 주장 진위 또는 모델 성능을 보증하지 않는다.

B4 설정 실험과 같은 감사 요청 3회 비교는 설치 후 별도로 수행해야 한다. 이번 작업에서 실제 모델의 속도·재읽기·인용 정확도 개선을 확인했다고 주장하지 않는다.
