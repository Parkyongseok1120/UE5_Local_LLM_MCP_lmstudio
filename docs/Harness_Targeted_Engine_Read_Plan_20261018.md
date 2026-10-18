# 엔진 소스 범위 읽기·근거 재사용·파일 수정 작업 관리 개선 계획

작성일: 2026-10-18, Asia/Seoul  
전달 대상: Claude Opus 5.5  
검토 기준 HEAD: `fbff059979edc8980eab970e7a7bba9e0b1d5181`  
저장소: `UE5_Local_LLM_MCP_lmstudio`  
추가 검토: Swift 1.5 수정 전 로그 1,829줄, SSOT·SOLID·계약·수명 경계, Claude Code 공식 자료와 파일 수정·작업 순서 조사 반영.  
현재 상태: 코드 재검토 및 계획 작성 완료. 아래 개선의 구현·설치·실모델 성능 평가는 아직 수행하지 않음. 커밋 없음.

## 1. 실행자에게 전달할 목적과 범위

사용자는 모델이 관련 파일 전체와 대형 엔진 소스를 한꺼번에 읽고, 컨텍스트 압축 후 같은 내용을 다시 읽어 정확도와 속도가 함께 나빠지는 상황을 개선하려 한다. Claude Code의 공식 탐색·컨텍스트 관리 방식을 참고하되, 현재 LM Studio + Unreal RAG MCP + 파일/실행 MCP + Compactor의 책임 구분을 유지한다.

이번 코드 재검토로 초기 제안을 좁혔다. **새 Compactor, 작업 메모리, 범용 근거 묶음 도구를 추가하지 않는다. 기존 엔진 조회의 실패 후 탐색과 잘린 구현의 다음 읽기를 구체화하고, 압축 안내를 현재 질문에 필요한 근거만 확보하도록 수정하는 것이 1차 범위다.** Swift 1.5 로그에서 드러난 근거 없는 심각도 판단, 반박된 가설의 재등장, 완료 범위 과장과 보고 지연도 기존 가이던스·복구 경로의 평가 대상으로 추가한다. 새로운 상태 계층이나 복구 루프를 만드는 근거로 삼지 않는다. 성능 개선은 가설이며 동일 조건의 실모델 비교로 확인한다.

실행 시 지킬 사용자 조건:

- 커밋하지 않는다. 마지막에 실제 최종 변경에 맞는 커밋 제목과 본문 초안만 제공한다.
- 기존 권한·프로젝트 바인딩·Unity 쓰기 명령 설정을 유지한다. 이 계획은 재설치나 실행 중인 MCP/Compactor의 교체를 요구하지 않는다.
- 기존 변경을 되돌리거나 관련 없는 정리를 하지 않는다. 이 문서의 기준 HEAD와 현재 작업 트리가 달라졌다면 해당 함수와 테스트만 다시 대조한다.
- Unreal 코드에 네임스페이스를 가능한 한 추가하지 않는 사용자 지침을 따른다.
- 구현 중에도 파일 목록/검색 → 관련 함수 → 필요한 주변 규칙 순서로 읽는다. 모든 관련 파일의 전체 내용을 선제적으로 수집하지 않는다.
- 기존 하네스·Unity 작업을 다시 시작하지 않는다. 이번 목표는 소스 근거 취득·재사용과 관련 파일 수정의 정확성·효율이다.
- SSOT·SOLID·계약·수명 관리·책임/경계/분리는 **작업 중 모든 변경에 적용할 사용자 요구**다. 마지막에만 문서 검사하는 항목이 아니다. §3.1–3.3과 §8을 구현 전·중·후에 적용한다.
- 이번 인계 문서는 실행 계획이다. 별도 Opus 조사에서도 원칙 이름만으로 결함을 판정하거나 광범위한 리팩터링을 시작하지 않고, 실제 변경 이유·호출 경로·반례부터 확인한다.

## 2. 재검토 결과: 초기 제안에서 수정한 부분

| 초기 방향 | 코드로 다시 확인한 사실 | 이번 계획의 결정 |
|---|---|---|
| 잘못된 owner를 빼고 `engine_local`로 다시 조회 | owner가 없으면 함수 이름을 파일명 후보 탐색에 사용한다. 엔진 전체 선언에서 owner를 역검색하는 기능은 아니다. | 단순 owner 제거 재시도를 해결책으로 삼지 않는다. 관찰된 모듈의 텍스트 검색으로 실제 선언·정의 후보를 찾는다. |
| 선언과 구현을 한 번에 묶어 주는 기능 추가 | 이미 같은 조회에서 선언과 구현 후보를 반환한다. 구현 후보는 최대 3개, 발췌는 40줄/3200자로 제한된다. | 새로운 bundle 도구를 만들지 않고 기존 결과의 후보 위치와 부분 읽기 안내를 개선한다. |
| 목표·반박 가설·미해결 질문을 보존할 새 계층 | continuity memory와 검증된 assistant note가 이미 있고 실제 입력에 주입된다. | 새 ledger·새 영속 메모리·새 요약 모델 호출을 추가하지 않는다. |
| 압축된 근거에서 함수 부분을 바로 복원 | archive의 `startOffset`은 UTF-16 단위이며 파일 줄→offset 변환이나 함수 검색 API가 없다. | 좁은 근거가 보관되어 있으면 복원한다. 전체 파일 archive에서 위치를 모르면 fresh `read_symbol`/`read_file_range`를 허용한다. |
| 수정 전에 항상 다시 읽기 | 현재 유효한 쓰기 결과 영수증으로 다음 수정을 진행하는 경로가 있고 회귀 테스트도 통과한다. | 유효한 현재 버전 증명을 재사용한다. 매 수정마다 의무적인 재읽기를 추가하지 않는다. |
| 엔진 읽기 결과 캐시를 새로 만들기 | 헤더 파일 목록과 후보 탐색 캐시가 이미 있다. 현재 본문은 재확인하는 경계가 있다. | 1차에서 새 본문 캐시를 만들지 않는다. 모델에 반환하는 중복량부터 측정한다. |
| 도구 지연 로딩·멀티에이전트·LSP를 함께 도입 | 기존 엔진별 도구 필터와 복구용 읽기 도구 축소가 있다. 추가 방식의 로컬 모델 효과는 미측정이다. | 1차 범위에서 제외한다. 선행 변경의 실측 결과가 필요할 때만 별도 제안한다. |

### 2.1 엔진 조회 실패의 실제 경로

`symbol_lookup_capability` → `engine_local_symbol` → `lookup_engine_header_evidence(local_source_identity=...)` → `_bounded_local_lookup` 순서다.

- `_bounded_local_lookup`는 `(owner or symbol)`에서 시작해 파일명 후보와 부모 타입을 제한적으로 확인한다. owner 없는 `RestartPlayer` 조회가 `GameModeBase.h`를 찾아준다고 가정하면 안 된다.
- 선언을 찾으면 같은 시간·파일·바이트 예산 안에서 `attach_definition_candidates`를 호출한다. 선언+구현 조회는 이미 합쳐져 있다.
- 결과가 0건이면 `engine_local_symbol`은 `Engine/Source/Runtime`, `maxFiles: 50000`, 같은 owner-qualified query의 `search_files`를 **제안**한다. 서버가 이 검색을 자동 실행하는 것은 아니다.
- 이 제안은 잘못 추정한 owner를 유지할 수 있다. 그러나 첨부 모델 기록의 전체 지연이 이 코드 때문에 발생했다고 확정하지 않는다.
- 파일 MCP의 잘못된 엔진 경로에 대한 NOT_FOUND 복구는 이미 해당 모듈의 파일명 검색을 제안한다. 이 별도 경로를 다시 구현하지 않는다.

작은 임시 엔진 fixture로 다음을 재현했다. 실제 엔진 전체 성능 측정은 아니다.

| 조회 | 결과 | 의미 |
|---|---:|---|
| `AController::RestartPlayer` | 0 | 지정 타입 후보에서 찾지 못함 |
| `RestartPlayer` | 0 | owner를 빼도 전역 선언 역검색이 되지 않음 |
| `AGameModeBase::RestartPlayer` | 1 | 정확한 타입·헤더 후보로는 찾음 |

세 경우 모두 `apiAbsenceVerified:false`를 유지했다. 특정 게임 API 이름을 복구 로직에 하드코딩하지 않는다.

### 2.2 Compactor의 실제 경로

`direct-compaction-core`의 사실 수집 → `buildContinuityMemory`의 objective·도구 결과·관찰 범위·미해결 항목 → `composeModelHistory`의 입력 주입이 이미 연결되어 있다. 별도 assistant note는 decisions/rejectedHypotheses/openQuestions를 검증하며 최대 4항목/1500자의 전체 스냅샷 계약을 갖는다. assistant 판단을 검증된 도구 사실로 승격하지 않는다.

`WorkingContext`는 근거를 보관하고 projection에 identity/version/range/readHandle을 남긴다. `evidence_first_read_context`는 보관된 body 또는 envelope를 페이지로 읽는다. 별도 semantic handoff의 예산·쿨다운도 이미 있다.

수정 후보는 `resume-facts.ts`의 다음 안내다.

> Do not treat them as fully read. Page the omitted body with evidence_first_read_context(action=read, readHandle, view=body) or reread the source range.

현재 입력에 원문이 전부 없다는 관찰은 맞다. 다만 현재 질문에 필요한 범위인지 구분하는 조건이 약하다. Swift 로그 1724줄은 이 문구를 재읽기의 이유로 인용하지만, 실제 입력과 대조 실험이 없어 문구의 인과 효과는 아직 가설이다. 중단된 답변의 주장을 재확인하라는 안내도, 그 주장을 뒷받침하는 필요한 근거를 확인한다는 의미를 유지한다.

### 2.3 Swift 1.5 수정 전 로그 분석 — 이번 추가분

분석 원본: 사용자 첨부 로그(첨부 ID `cf833b63-6e4b-4cb1-a130-45d9458f42c1`, 로컬 제공, 배포 미포함). 아래 줄 번호는 원본의 1-based 줄 번호다. 원본은 164,923 bytes, 1,829줄이며 SHA-256은 `c1bdff416b0f8a367accc870354a4da03642af486cbaa1ce8d1a5e1ea6d0de7d`다. **Swift 1.5라는 모델명과 수정 전이라는 구분은 사용자 제공 정보**이며 정확한 모델 ID·양자화·컨텍스트·설정·당시 실행 바이너리 버전은 기록에서 확인하지 못했다.

이 첨부는 주로 모델의 분석 서술이며 일부 호스트 안내가 포함된다. 원시 structured tool request/result와 각 라운드 실제 모델 입력의 짝이 없다. 따라서 “읽었다”, “512자로 압축됐다”, “검색 0건”이라는 서술을 실제 도구 실행 횟수·입력 토큰·압축 횟수로 집계하지 않는다. 문자 수로 실행 시간을 추산하지 않는다. 아래는 **로그에서 관찰한 행동과 현재 코드에 근거한 개선 후보**이며, Compactor가 모든 문제의 원인이라는 확정 진단이 아니다.

| ID | 로그에서 관찰한 문제와 근거 | 반대 근거·판단 한계 | 계획 반영 |
|---|---|---|---|
| S1 | 헤더·cpp 전체를 묶어 읽으려 함(53–83). 93줄에 `held_reservations_insufficient`, 최소 1,844 추정 토큰 필요, 다음 라운드 단독 재요청 안내가 표시됨. 97줄은 단독 재시도를 말하면서 Tests 조회도 같이 하겠다고 함. | 작은 프로젝트 전체 감사의 첫 전체 읽기는 합리적일 수 있음. 실제 재시도 인자는 없고 reservation 누수도 입증되지 않음. broker는 이미 정확한 단독 재요청 안내를 제공함. | E0/E3/E5, R14: 반환 결과 먼저 사용, 거절된 필요한 읽기만 단독 재시도. 예산 확대나 모든 읽기의 직렬화를 해결책으로 삼지 않음. |
| S2 | RPC·OnRep 동작을 기억과 가상 wrapper 코드로 추측해 오류로 올렸다가 번복(135–227, 1065–1067, 1478–1507). 505줄은 `Killer->Team != DeadPlayer->Team` 조건을 인용하고도 아군 처치 점수가 올라간다고 설명함. | 후보를 탐색하는 행위 자체는 정상. 문제는 근거 없는 P0/P1 확정, 인용한 조건과 결론의 불일치, 반박된 후보의 반복임. 실제 게임 버그로 채택하지 않음. | E2/E4/E5, R15: 호출·분기·상태 변경을 좁게 연결하고 source-first 판단. 스타일·미래 위험·확인된 결함을 구분. |
| S3 | 268줄에서 `AGameModeBase` 소속을 설명했으나 510줄부터 `AController::RestartPlayer`로 바뀜. 검색 실패와 예상보다 짧은 헤더를 엔진 삭제/커스텀 소스 문제로 해석(760–854). 840줄의 다른 소속 발견 뒤에도 848줄에서 기존 가정을 유지함. | 초기 지식 부족만의 문제가 아님. 나중에 소속을 바로잡는 서술도 있음(948–949). 정확한 당시 검색 인자는 미확인. | E1/E3/E4, R1/R10/R16: 실제 호출부로 owner 후보를 정하고 반박 근거의 참조를 보존. 검색 0건·예상 파일 길이는 엔진 손상 증명이 아님. |
| S4 | `Private/Latency_MultiCombat.cpp`를 못 찾고 모듈 등록 누락·게임 시작 불가 P0를 추정(1212–1245). 이후 모듈 루트와 `IMPLEMENT_PRIMARY_GAME_MODULE`을 발견해 철회(1258–1262). | 1247줄의 `IMPLEMENT_MODULE` 검색 서술만으로 실제 literal/regex 인자를 확정할 수 없음. 검색 범위 완주와 질의가 모든 등록 매크로를 포함한다는 사실은 별개임. | E1/E4/E5, R17: 파일명 복구를 재사용하고 모듈 루트·관련 매크로 변형을 고려. 링크/실행 오류를 만들어 내지 않음. |
| S5 | archive handle과 offset으로 복원하려는 시도는 있음(930–934, 984–1018). 그러나 “전체를 읽었다는 요약은 있지만 현재 원문이 없다”는 이유로 여러 핵심 파일 전체를 재읽겠다고 함(1721–1724). 1724줄은 `do not treat as fully read` 문구를 직접 근거로 듦. | archive 미사용이 아님. 필요한 근거 선택의 문제에 가깝지만, 모델의 자기 설명만으로 문구의 인과 효과가 확정되지는 않음. 당시 실제 입력은 미확인. | E3/E5, R8–R10/R18: 원문 부족을 현재 질문 단위로 판단. 기존 note와 근거 참조 재사용, 필요하면 해당 함수만 재획득. |
| S6 | “모든 엔진 API 검증”, “모든 파일 전체 읽기”라고 선언(1315–1319, 1386, 1686–1688)하면서 같은 기록에서 일부 본문 누락을 인정하고 생략 내용까지 추정(1699–1700). | 불필요한 꼬리까지 반드시 읽어야 한다는 뜻이 아님. 필요한 질문에 충분한 근거와 모든 파일/동작의 검증 완료는 다름. | E4/E5, R13/R19: 취득 범위·현재 가시성·의미적 검토·실행 검증을 분리하고 미확인 범위를 정확히 보고. |
| S7 | `ServerFireWeapon (Unreliable)`, `SetupEnhancedInput` 같은 이름과 바이트 위치를 기억으로 만들고, 클라이언트가 `FireWeapon`을 호출한다는 사실만으로 권한 결함을 의심(1719, 1801–1825). | 235·474·563·689줄에는 RPC 경로를 이해한 서술이 있고, 끝부분에서 좁은 확인으로 돌아가기도 함. 바이트 범위 읽기 자체가 잘못은 아님. | E2/E3, R20: 실제 반환 symbol/line으로 입력→일반 함수→RPC→권한 분기→상태 변경을 연결. 함수명이나 호출 위치만으로 결론 내리지 않음. |
| S8 | 본문에 `<tool_call>` XML 블록 2개가 노출(1537–1562). 그 직후 확보했다고 말한 파일이 XML의 대상과도 다름(1566). | 실제 dispatch/result 기록이 없어 parser 실패나 결과 오배달을 확정할 수 없음. 본문의 도구 호출 흉내를 실행·성공으로 세면 안 됨. | E0/E4/E5, R21: 기존 raw-tool-intent 탐지·재계획·partial delivery 계약에 exact-text fixture를 추가하고 이벤트 짝을 확인. XML 자동 실행기를 만들지 않음. |
| S9 | 보고서를 정리하겠다고 여러 차례 선언(1320, 1686, 1702, 1794)한 뒤 전체 취득/새 결함 후보로 되돌아감. 제공된 기록은 1829줄의 추가 읽기 의도로 끝남. | 첨부에 최종 감사가 없다는 사실만 확인됨. 실제 세션이 무한 루프·크래시·사용자 중단이었다고 단정하지 않음. | E4/E5, R22: 첫 유용한 보고·최종 전달 여부, 새 근거 없는 재조사를 측정. 기존 종료/보고 이어쓰기 경로를 재사용. |
| S10 | 428줄에 추론 생성 한도와 도구 요청 부재로 한 번 재시도한다는 호스트 안내가 표시됨. 이어 432–439줄에서 다시 파일별 전체 분석을 시작하겠다고 함. | 정확한 토큰 수·finish reason·재시도 이벤트 수는 미확인. 현재 코드에는 이미 한 번의 reasoning recovery와 짧은 다음 단계 지침이 존재함. | E0/E4/E5, R23: 기존 reasoning recovery 경로 replay와 실모델 관측. 무조건 출력 한도를 늘리거나 복구 루프를 추가하지 않음. |

**현재 게임 소스와의 좁은 대조:** 아래는 2026-10-18 현재 파일의 소스 확인이며 당시 실행 파일과 해시가 같다는 증거는 아니다. 게임 전체 감사를 대신하지 않으며 빌드·멀티플레이 실행을 검증한 것도 아니다. 파일 해시는 근거 JSON에 저장했다.

- [Character.cpp:188](../../UE5_Latency-Resilient-Multiplayer-Combat/Source/Latency_MultiCombat/Private/LatencyMultiCombatCharacter.cpp:188)의 `FireWeapon`은 authority이면 `HandleServerFire`, 로컬 클라이언트이면 `ServerFire`로 이어진다. `ServerFire_Implementation` 뒤 `HandleServerFire`에는 authority 검사가 있고 그 뒤 탄약을 감소시킨다. [Character.h:83](../../UE5_Latency-Resilient-Multiplayer-Combat/Source/Latency_MultiCombat/Public/LatencyMultiCombatCharacter.h:83)의 실제 선언은 `ServerFire`, `Server, Reliable`이다. 따라서 “클라이언트에서 일반 함수를 호출했다”만으로 권한 결함을 확정할 수 없다.
- [Character.cpp:85](../../UE5_Latency-Resilient-Multiplayer-Combat/Source/Latency_MultiCombat/Private/LatencyMultiCombatCharacter.cpp:85)에 Enhanced Input과 legacy fallback이 있고, 415줄 `LegacyShoot`은 `ShootPressed`로 연결된다. 기억한 이름으로 넓은 범위를 재읽기 전에 해당 symbol을 찾으면 되는 사례다.
- [GameMode.cpp:128](../../UE5_Latency-Resilient-Multiplayer-Combat/Source/Latency_MultiCombat/Private/LatencyMultiCombatGameMode.cpp:128)의 `RestartPlayer(Controller)`는 GameMode 멤버 함수 내부의 호출이다. 실제 callsite를 먼저 확인해야 하며 `Controller->RestartPlayer()`라는 호출을 만들어서는 안 된다.
- [Latency_MultiCombat.cpp:6](../../UE5_Latency-Resilient-Multiplayer-Combat/Source/Latency_MultiCombat/Latency_MultiCombat.cpp:6)의 `IMPLEMENT_PRIMARY_GAME_MODULE`은 모듈 루트에 있다. `Private/` 경로 실패나 다른 매크로 이름의 검색 0건은 등록 누락 증명이 아니다.

이번 추가 분석의 결론은 **읽기량 축소만으로 충분하지 않다**는 것이다. 정확한 근거 선택 → 반박된 가설 재사용 → 증명 수준에 맞는 판단 → 보고 전달까지 함께 평가한다. 모델의 추론 능력·tool-call 형식 적합성, 가이던스, 출력 한도, 압축 후 근거 선택이 각각 대안 원인이다. 하네스 문구 변경의 효과와 모델 고유 한계를 실측으로 구분한다.

## 3. 기존 소유자와 근거 위치

아래 링크의 줄 번호는 기준 HEAD에 해당한다. 실행자는 파일 전체를 읽기 전에 해당 함수와 호출부를 확인한다.

| 책임 | 코드 근거 |
|---|---|
| RAG 공개 스키마·엔진 조회 dispatch | [direct_rag_contract.py:116](../scripts/direct_rag_contract.py:116), [direct_rag_symbol.py:86](../scripts/direct_rag_symbol.py:86) |
| 프로젝트/엔진 식별·0건 suggestion·응답 fitting | [direct_rag_engine_local.py:37](../scripts/direct_rag_engine_local.py:37) |
| 타입 후보·상속·모듈·공유 검색 예산 | [engine_header_evidence.py:868](../scripts/engine_header_evidence.py:868), [동일 파일:944](../scripts/engine_header_evidence.py:944) |
| 구현 후보와 발췌 한계 | [engine_definition_evidence.py:73](../scripts/engine_definition_evidence.py:73), [동일 파일:189](../scripts/engine_definition_evidence.py:189) |
| 문자열 단위 최종 응답 축소 | [direct_rag_evidence.py:132](../scripts/direct_rag_evidence.py:132) |
| 엔진 경로 검색·검색 cursor | [direct-search-capability.js:104](../lmstudio-unreal-agent-mcp/src/direct-search-capability.js:104) |
| 줄·함수 읽기와 lexical 한계 | [direct-read-capabilities.js:195](../lmstudio-unreal-agent-mcp/src/direct-read-capabilities.js:195), [direct-symbol-extraction.js:22](../lmstudio-unreal-agent-mcp/src/direct-symbol-extraction.js:22) |
| 목표·과거 근거·관찰 범위 | [continuity-memory.js:195](../lmstudio-context-compactor-plugin/src/continuity-memory.js:195) |
| 판단·반박 가설·질문의 명시적 상태 | [continuity-model-notes.js:22](../lmstudio-context-compactor-plugin/src/continuity-model-notes.js:22), [동일 파일:308](../lmstudio-context-compactor-plugin/src/continuity-model-notes.js:308) |
| 입력 합성·원문 존재 메타데이터 | [context-manager.ts:215](../lmstudio-context-compactor-plugin/src/context-manager.ts:215), [input-availability.js:423](../lmstudio-context-compactor-plugin/src/input-availability.js:423) |
| projection·archive 복원 도구 | [working-context.js:455](../lmstudio-context-compactor-plugin/src/working-context.js:455), [evidence-archive.js:600](../lmstudio-context-compactor-plugin/src/evidence-archive.js:600) |
| 재개 안내 생성·실제 연결 | [resume-facts.ts:34](../lmstudio-context-compactor-plugin/src/resume-facts.ts:34), [prediction-loop.ts:234](../lmstudio-context-compactor-plugin/src/prediction-loop.ts:234) |
| 현재 버전 증명 및 연속 수정 | [direct-edit-bundle-preflight.js:16](../lmstudio-unreal-agent-mcp/src/direct-edit-bundle-preflight.js:16), [file-snapshot-flow.test.js:207](../lmstudio-unreal-agent-mcp/test/file-snapshot-flow.test.js:207) |
| 모델 trace와 기존 실모델 평가 | [trace_lmstudio_run.py:1](../scripts/trace_lmstudio_run.py:1), [eval-audit-pressure.cjs:1](../lmstudio-context-compactor-plugin/scripts/eval-audit-pressure.cjs:1) |

추가 로그와 대조한 기존 책임:

| 책임 | 코드 근거와 변경 판단 |
|---|---|
| 병렬 읽기 예산·단독 재시도 안내 | [budget-broker.ts:74](../lmstudio-context-compactor-plugin/src/budget-broker.ts:74), [동일 파일:253](../lmstudio-context-compactor-plugin/src/budget-broker.ts:253). 로그만으로 알고리즘 변경 근거 없음. |
| 실제 실행 여부·제안/실행 인자 관측 | [tool-boundary.ts:155](../lmstudio-context-compactor-plugin/src/tool-boundary.ts:155). 거절은 `not_executed`; 기존 관측을 평가에 사용. |
| 본문 XML 탐지·제한된 재계획 | [raw-tool-intent.ts:14](../lmstudio-context-compactor-plugin/src/raw-tool-intent.ts:14), [prediction-loop.ts:839](../lmstudio-context-compactor-plugin/src/prediction-loop.ts:839). 탐지 결과를 실제 실행으로 승격하지 않음. |
| 추론 한도 복구와 다음 단계 안내 | [prediction-loop.ts:1225](../lmstudio-context-compactor-plugin/src/prediction-loop.ts:1225), [execution-instructions.ts:3](../lmstudio-context-compactor-plugin/src/execution-instructions.ts:3). 이미 한 번의 복구와 전체 재분석을 피하라는 지침이 있음. |
| 최종 보고·중단 본문 이어쓰기 | [prediction-loop.ts:1284](../lmstudio-context-compactor-plugin/src/prediction-loop.ts:1284), [동일 파일:1340](../lmstudio-context-compactor-plugin/src/prediction-loop.ts:1340). 선택된 audit 모드·기존 종료 조건을 유지. |

`doNotDuplicate`: budget broker, raw-tool-intent/replanning, reasoning recovery, report continuation/finalization, header catalog, 타입/상속 탐색, definition candidates, read_symbol/read_file_range, path policy, snapshot/receipt, archive/catalog/readHandle, continuity memory, assistant note lifecycle, semantic handoff budget/cooldown, 입력 측정 및 기존 평가 runner.

### 3.1 SSOT·SOLID·계약을 실제 변경에 적용하는 기준

이미 [core.md:6](../docs/model-guidance/core.md:6)와 [design.md:7](../docs/model-guidance/design.md:7)에 정본·변경 권한·SOLID·실패/수명 기준이 있다. [이전 책임·수명 계획:348](../docs/Harness_Responsibility_Lifecycle_Plan_20261007.md:348)에는 구현 결과도 기록되어 있다. 이전 계획의 문제 목록을 현재 미해결 결함으로 다시 가져오지 않는다. 아래는 관련 현재 소스와 대조해 **이번 변경에서 보존할 조건**으로 구체화한 것이다.

| 기준 | 구현 중 확인할 질문과 적용 조건 |
|---|---|
| SSOT | 같은 사실을 확정/변경하는 곳은 어디인가? 원본 설정·현재 상태·관찰된 사실·과거 archive·모델 판단·화면 표시는 구분한다. 파생 view나 동기화된 저장 사본이 있다는 이유만으로 위반이라고 하지 않는다. 각 사본에는 출처·버전·갱신/무효화 책임이 있어야 한다. |
| SRP | 이번 변경 이유를 기존 어느 소유자가 맡는가? RAG는 후보/근거와 다음 읽기 제안, 파일 MCP는 실제 경로/읽기/수정, Compactor는 입력 구성·실행 수명, 모델은 조사와 판단을 맡는다. suggestion·notice·가이던스가 별도 실행 제어자가 되지 않는다. |
| OCP | 반복해서 확장되는 지점이 실제로 있는가? 기존 catalog/계약 데이터와 작은 공개 결과 필드를 사용한다. 특정 게임 API명이나 엔진 예외를 prediction-loop에 누적하지 않는다. 예상만으로 플러그인 계층을 만들지 않는다. |
| LSP | 같은 인터페이스의 대체 결과도 입력·성공·실패·partial·취소 의미를 보존하는가? lexical 후보를 semantic 정의로, archive를 현재 소스로, 저장 성공을 실행 허용으로 바꾸지 않는다. 호환 필드는 소비자의 누락/unknown 처리까지 확인한다. |
| ISP | 소비자에게 필요한 후보·범위·불변 snapshot만 주는가? 다음 읽기 안내 때문에 전체 runtime·archive·권한 객체를 넘기지 않는다. 함수 읽기도 선언/부모/호출 조건이 필요하면 그 범위만 확장한다. |
| DIP | 선택·표현 로직이 기존 공개 계약과 필요한 view에 의존하는가? 정책 helper가 SDK 실행·파일 쓰기·프로젝트 선택까지 직접 수행하지 않는다. 기존 함수 경계로 충분하면 새 interface/DI container를 추가하지 않는다. |
| 계약 | 유효 입력/단위/상한 → 성공/부분/실패 → 부작용 시작점 → 중복/재시도 → 취소/종료 → 버전·무효화 → 소비자 영향을 변경 전에 적는다. 문서의 권고와 코드가 강제하는 조건을 구분한다. |
| 분리 | 데이터 보존과 모델 입력 선택, 실행 권한과 관찰 identity, 파일 적용과 결과 전달/기록 보관, 작업 목록과 실제 프로세스 상태를 분리한다. 경계 간 전달은 기존 식별자·결과 계약을 따른다. |

**정본과 파생물의 변경 위치:** 아래 정본은 서로 다른 종류의 사실을 맡는다. 하나의 거대 SSOT 파일로 합치지 않는다.

| 사실/규칙 | 정본·변경 책임 | 파생물·소비자와 보존 조건 |
|---|---|---|
| 일반 설계·작업 가이던스 | `docs/model-guidance/*.md`, `catalog.json` | [build-design-guidance.cjs:87](../lmstudio-context-compactor-plugin/scripts/build-design-guidance.cjs:87) → generated bundle → 선택/입력. 기존 문구가 충분하면 추가하지 않음. |
| 엔진 사실·프로젝트 정책 계약 | `config/<engine>_contracts.json`, 기존 검증기 | [build-design-guidance.cjs:70](../lmstudio-context-compactor-plugin/scripts/build-design-guidance.cjs:70), 222줄 이후의 로딩/생성 경로. Markdown의 contract-summary 블록은 파생물이며 engine_fact의 검증 버전과 적용 버전 정합성을 유지. |
| 도구 effect·읽기 예산 계약 | `config/stable_tool_manifest.json`; Unity effect는 Unity `catalog` | [build-tool-contract.cjs:36](../lmstudio-context-compactor-plugin/scripts/build-tool-contract.cjs:36), [build-unity-tool-contract.cjs:3](../lmstudio-context-compactor-plugin/scripts/build-unity-tool-contract.cjs:3). 공개 인자 schema까지 모두 이 manifest 하나에 있다고 가정하지 않음. |
| 읽기/수정 분류와 실행 허용 | provider 계약, [tool-capability-registry.ts:24](../lmstudio-context-compactor-plugin/src/tool-capability-registry.ts:24), 기존 ToolScope/ToolBoundary | 원격 도구의 이름·설명·readOnlyHint나 모델 메모로 권한을 새로 만들지 않음. 사용자 권한 설정 유지. |
| 목표·짧은 판단 메모 | 기존 continuity와 [note-persistence.ts:16](../lmstudio-context-compactor-plugin/src/note-persistence.ts:16) | history/window note의 복원 우선순위·동기화·종료 저장을 재사용. 두 저장 사본을 독립 writer로 만들지 않음. 판단 내용은 검증된 사실이 아님. |
| 실행 단계·복구 | [execution-state.ts:9](../lmstudio-context-compactor-plugin/src/execution-state.ts:9), [recovery-coordinator.ts:15](../lmstudio-context-compactor-plugin/src/recovery-coordinator.ts:15) | 상태 전이와 복구 계획/진행을 각 소유자에 유지. 안내 문구에서 카운터를 변경하거나 별도 FSM을 만들지 않음. |
| 실제 파일 상태와 수정 결과 | 파일 provider의 lock/hash/snapshot/receipt/transaction | [direct-edit-bundle.js:70](../lmstudio-unreal-agent-mcp/src/direct-edit-bundle.js:70). 가이던스·모델 요약·archive가 쓰기 허가나 현재 파일 hash를 대신하지 않음. |

기존 가이던스는 선택·버전·입력 예산에 따라 전달될 수 있다. [design-guidance.ts:165](../lmstudio-context-compactor-plugin/src/design-guidance.ts:165)의 후보 구성과 실제 입력 연결을 관찰해야 하며, 파일에 규칙이 존재한다는 사실을 모델이 읽고 준수했다는 증거로 삼지 않는다. 이번 사용자 요구는 실행자가 직접 지키고 검증할 조건이다. 이를 이유로 모든 설계 문서를 매 라운드 강제 주입하거나 사용자 모드를 변경하지 않는다.

### 3.2 수명·취소·부분 성공에서 보존할 경계

| 수명/자원 | 현재 근거 | 보존할 조건과 필요한 검증 |
|---|---|---|
| 엔진 후보 catalog | [engine_header_evidence.py:637](../scripts/engine_header_evidence.py:637), 645–689 | engine/module identity, TTL, cache generation과 deadline을 유지. 무효화된 세대의 늦은 결과를 새 캐시에 게시하지 않음. 새 본문 cache는 추가하지 않음. |
| 한 실행의 session·WorkingContext | [prediction-loop.ts:92](../lmstudio-context-compactor-plugin/src/prediction-loop.ts:92), [동일 파일:1376](../lmstudio-context-compactor-plugin/src/prediction-loop.ts:1376) | 획득 직후 정리 책임 등록. setup 실패·취소·seal/close 실패에도 기존 종료 경로 사용. cleanup 오류가 원래 실행 결과를 덮지 않으며 종료 중 새 모델 작업을 시작하지 않음. idempotent 해제와 호출 횟수 1회를 혼동하지 않음. |
| 한 SDK 라운드 | [round-loop.ts:75](../lmstudio-context-compactor-plugin/src/round-loop.ts:75), [동일 파일:249](../lmstudio-context-compactor-plugin/src/round-loop.ts:249) | signal/listener/closed fence와 늦은 callback 거절 유지. call ID는 실행·라운드와 결합해 비교하며 다른 라운드의 같은 ID를 동일 호출로 합치지 않음. |
| 한 읽기 배치의 reservation | [budget-broker.ts:76](../lmstudio-context-compactor-plugin/src/budget-broker.ts:76) | held/consumed/released/closed와 overrun을 구분. 성공 peer 결과 보존, 중복 정산 방지, 다음 batch로 held 누수 없음. 추정 allowance와 exact 입력 측정 구분. |
| 반환 근거와 미완성 계획 | [execution-state.ts:75](../lmstudio-context-compactor-plugin/src/execution-state.ts:75) | RoundTransaction이 성공 반환 근거를 보존해도 미완성 assistant 판단·미실행 요청을 성공으로 올리지 않음. 취소가 이미 발생한 효과를 없애지는 않음. |
| active context와 terminal seal | [working-context.js:641](../lmstudio-context-compactor-plugin/src/working-context.js:641), [동일 파일:725](../lmstudio-context-compactor-plugin/src/working-context.js:725) | 실행용 commit은 exact fit 필요. terminal seal의 unmeasured/remeasureRequired 저장은 다음 dispatch 허가가 아님. 취소 시 보존을 위해 새 모델 측정을 강제하지 않음. |
| archive·note | [working-context.js:780](../lmstudio-context-compactor-plugin/src/working-context.js:780), [evidence-archive.js:248](../lmstudio-context-compactor-plugin/src/evidence-archive.js:248) | scope/lineage/integrity/version과 보호된 ref/lease의 수명 유지. TTL만 보고 보호된 근거를 지우지 않음. note는 목표/프로젝트 변경에 맞춰 무효화하며 archive는 historical/no-mutation 경계 유지. |
| 파일 적용·잠금·복구 | [direct-edit-bundle-commit.js:123](../lmstudio-unreal-agent-mcp/src/direct-edit-bundle-commit.js:123), [direct-edit-bundle.js:158](../lmstudio-unreal-agent-mcp/src/direct-edit-bundle.js:158) | 정렬된 lock 취득, 잠금 후 hash 검사, publication 전 취소, 시작 후 기존 commit/rollback 계약과 reverse release 유지. archive 기록 실패만으로 이미 적용한 파일을 되돌리지 않음. rollbackIncomplete/외부 변경은 사실대로 보고. |

위 소스 확인은 현재 관련 경계를 확인한 수준이다. 전체 시스템의 SOLID 준수·수명 안전을 인증한 것은 아니다. 특히 기존 허용 전이인 `FINAL_DELIVERY → TOOL_REPLAN`을 문구만으로 금지하거나, `OUTPUT_RECOVERY`에 새 조사 루프를 넣지 않는다.

### 3.3 Opus가 각 변경 단위마다 남길 최소 기록

1. **변경 전:** 목적·현재 미확인 질문, 담당 정본/소유자, 수정 함수와 직접 소비자, 보존 계약, 대상 파일 수와 순서를 적는다. 선언·실제 호출·상태 변경·종료 연결을 필요한 범위만 읽는다.
2. **변경 중:** 한 계약 단위의 patch를 적용하고 diff를 확인한다. 상태 writer·권한·수명 소유자·새 의존성이 늘어나면 필요한 근거를 다시 점검한다. 파일 수/클래스 수를 목표로 늘리거나 줄이지 않는다.
3. **변경 후:** 해당 계약의 정상·부분·실패·취소/재진입 중 영향받은 경우를 검사하고, 새 snapshot/receipt 또는 diff·테스트 결과를 다음 단계에 연결한다.
4. **가설 수정:** 유지할 근거·반박한 가설·남은 질문만 짧게 갱신한다. 소스 변경과 무관한 재조사로 돌아가지 않는다.
5. **단계 종료:** 적용 완료와 검증 완료를 나누어 기록하고 §8의 다음 의존 작업으로 진행한다. 이 기록은 본 계획의 진행 표나 기존 작업 도구를 쓰며 제품에 새 ledger를 구현하지 않는다.

## 4. 구현 단계와 완료 조건

### E0. 기준 고정과 실패 사례 등록

1. 작업 트리·실행 환경·구성 요소 버전을 기록한다. 현재 사용자 설정과 실제 프로젝트를 수정하지 않는 fixture를 사용한다. §3의 정본/소유자/수명 경계와 §8의 예상 파일 목록·의존 순서를 기준으로 시작한다. baseline 실행 중 그 실행이 읽는 소스·생성물을 바꾸지 않는다.
2. 기존 테스트에서 잘못된 owner, 0건 suggestion, 구현 발췌 잘림, 부분 projection 안내, archive 복원, 연속 수정 영수증 사례를 확인한다.
3. 잘못된 owner를 제거하는 것만으로는 해결되지 않는 fixture를 회귀 테스트로 추가한다. 실제 API 이름 한 개에만 맞춘 테스트로 끝내지 않는다.
4. 실모델 기준 실행을 위한 작은 작업 세트를 고정한다. 현재 평가 runner에 필요한 도구가 없다면 실측 전 도구 연결을 추가하고, 기존 도구와 새 도구가 동일한 조건으로 제공되는지 확인한다.

5. S1–S10의 로그 원문 위치와 해시를 고정한다. 서술 기반 fixture와 실제 SDK 이벤트 replay를 구분한다. 없는 request/result·finish reason·모델 입력을 만들어 채우지 않는다.
6. Swift 1.5를 우선 비교 대상으로 삼되 실제 모델 식별자·양자화·컨텍스트·출력/추론 설정·하네스 버전은 실행 전 확인한다. 확인할 수 없으면 동일 실행 재현을 주장하지 않는다. 다른 모델로 조용히 대체하지 않는다.
7. 현재 게임의 위 네 파일은 관련 함수와 hash만 비교 기준으로 사용한다. 실제 프로젝트를 수정하지 않는 작은 fixture로 owner 혼동, 모듈 매크로 변형, 권한 분기 사례를 만든다.

완료 조건: baseline 실패/제약과 기대 변경을 분리해서 기록한다. 현재 기능의 테스트 성공을 개선 효과로 보고하지 않는다.

### E1. 엔진 0건 응답의 다음 검색을 정확하게 만든다

주 수정 소유자: `engine_header_evidence.py`, `direct_rag_engine_local.py`. 필요 시 공개 결과 계약·관련 테스트만 함께 변경한다.

1. 타입 선언을 실제로 확인했지만 요청 멤버를 못 찾은 경우, 이미 읽은 헤더에서 관찰한 타입·모듈 위치를 작고 제한된 메타데이터로 전달한다. 함수의 선언 소속(`declaredIn`)으로 위장하지 않는다.
2. 관찰된 모듈이 있으면 그 모듈 안에서 **owner를 고정하지 않은 정확한 심볼 토큰**을 검색하는 `search_files` suggestion을 만든다. 예: JSON 인자로 `"query": "\\bRestartPlayer\\s*\\(", "regex": true`를 사용한다. escaping은 실제 JSON 스키마와 테스트로 확인한다.
3. `Classes`·`Public`·`Private`·inline 선언을 배제하지 않는다. 검색 결과는 선언/정의/호출 후보이며, 반환된 `uri`와 줄 범위를 읽어 소속을 확인한다. 텍스트 일치만으로 owner나 callability를 확정하지 않는다.
4. 모듈 관찰이 없으면 엔진 안의 실제 검색 가능 경로와 검색 한계를 제시한다. 자동으로 50,000개 파일 탐색을 첫 제안으로 내보내지 않는다. 필요한 범위 확대는 허용하고, 한계 때문에 못 찾았으면 미확인으로 남긴다.
5. 검색이 잘렸다면 반환된 이유와 cursor 계약을 따른다. 같은 검색의 다음 페이지와 새로운 하위 트리 검색을 구분한다. 파일 발견 한계가 cursor만으로 해결된다고 가정하지 않는다.
6. suggestion에는 정확한 프로젝트 바인딩과 `engine://` 경로를 유지한다. 별도 MCP 서버를 RAG 서버 내부에서 호출하는 새 orchestration 계층은 만들지 않는다.
7. 기존 파일 NOT_FOUND의 모듈별 파일명 검색 복구는 그대로 재사용한다.
8. 프로젝트 호출부에서 receiver/상속 후보를 먼저 정한다. 모듈 등록 사례는 모듈 루트·Public·Private 및 `IMPLEMENT_MODULE`, `IMPLEMENT_GAME_MODULE`, `IMPLEMENT_PRIMARY_GAME_MODULE`의 질의 범위를 구분한다. 기존 filename/search 기능을 사용하며 새 모듈 분석기는 추가하지 않는다. 검색 완주 여부와 질의의 의미적 포괄성은 각각 확인한다.

완료 조건: 잘못된 owner에서 같은 잘못된 qualified query를 반복하도록 유도하지 않는다. 이름이 같은 다른 타입의 멤버를 정답으로 조용히 대체하지 않는다. unknown/partial/부재 미검증과 엔진 식별 경계를 유지한다.

### E2. 기존 선언+구현 결과에서 필요한 다음 읽기로 연결한다

주 수정 소유자: `direct_rag_engine_local.py`, 필요할 때만 `engine_definition_evidence.py`. 기존 Node 범위 읽기·snapshot 계약은 보존한다.

1. 이미 반환된 선언과 구현 발췌가 현재 판단에 충분하면 추가 읽기를 요구하지 않는다. 전체 파일 읽기를 기본 다음 행동으로 제안하지 않는다.
2. 구현 발췌가 잘렸고 정확한 후보 `line/endLine`이 있으면 해당 후보의 `read_file_range` 인자를 제공한다. 파싱이 불완전해 끝 줄이 없으면 끝을 만들지 말고 제한된 범위 읽기 또는 기존 `read_symbol`을 안내한다.
3. `read_symbol`은 첫 lexical 후보를 고르며 semantic overload resolution을 하지 않는다. 후보가 여러 개이면 `candidateLines` 및 signature 근거를 확인하고 정확한 범위를 선택하게 한다. 새로운 C++ semantic parser를 이 단계에서 만들지 않는다.
4. 40줄/3200자 발췌 한계와 최종 envelope fitting은 별개다. 마지막 fitting이 문자 중간에서 잘랐을 수 있으므로 `excerptTruncated:false`만 보고 모델에 본문 전체가 전달됐다고 선언하지 않는다.
5. 정확한 미반환 시작 줄을 증명할 수 없으면 작은 함수 범위를 일부 다시 읽는 것을 허용한다. 잘린 마지막 줄을 건너뛰거나 추정 cursor를 만드는 것보다 낫다. 새 continuation 필드를 넣는다면 실제 반환 표현을 기준으로 검증한다.
6. 후보 경로·줄·식별자·해시를 메타데이터 한계로 자를 때 현재의 전체 후보 단위 제거와 omitted 표시를 유지한다. 부분 파일 fingerprint를 전체 파일 hash로 바꾸지 않는다.
7. 관련 선언 매크로, 직접 부모 구현, 멤버 초기화/종료 경로는 현재 질문에 필요한 경우에만 추가 확인한다. 자동으로 모든 호출자와 부모 파일을 수집하지 않는다.
8. 기억으로 만든 함수명·파일 줄·바이트 위치 대신 실제 검색 결과의 locator를 사용한다. 입력/RPC 문제는 관련 입력 바인딩 → handler → 일반 함수 분기 → RPC 선언/구현 → 권한 검사 → 상태 변경까지만 우선 연결한다. 일반 함수의 클라이언트 호출 자체를 서버 상태 변경으로 간주하지 않는다. 생성 wrapper 동작이 결론의 핵심일 때만 해당 버전 소스/공식 근거를 추가 확인한다.

완료 조건: 짧은 함수는 기존 한 번의 조회로 충분할 수 있고, 큰 함수는 필요한 후보 범위로 이어진다. 후보 반환 자체를 검토 완료나 실행 동작 증명으로 표시하지 않는다. 새 필드가 응답을 비대하게 만들면 필드 수와 중복 설명부터 줄인다.

### E3. 압축 안내를 필요한 근거 선택에 맞춘다

주 수정 소유자: `resume-facts.ts`, `execution-instructions.ts`, 필요 시 `working-context.js`의 짧은 부분 본문 안내. state schema는 변경하지 않는다.

안내의 의미는 아래 선택표를 만족해야 한다.

| 상태 | 허용되는 다음 행동 |
|---|---|
| 현재 입력의 코드·근거가 질문을 답하기에 충분 | 그대로 판단을 진행한다. 생략된 다른 결과를 복원할 필요는 없다. |
| 필요한 과거 버전의 좁은 근거 항목과 handle/위치를 앎 | 해당 archive 본문 범위만 복원한다. |
| 전체 파일 archive만 있고 필요한 함수의 offset은 모름 | 기존 `read_symbol` 또는 `read_file_range`로 좁게 읽을 수 있다. archive 첫 페이지부터 전체를 순회하도록 강제하지 않는다. |
| archive 만료·손상·참조 불가 | 실패 상태를 유지하고 필요한 현재 소스 범위로 대체한다. 과거 원문을 복원했다고 주장하지 않는다. |
| 현재 소스 상태가 질문의 핵심이거나 외부 변경 관찰됨 | 현재 파일의 필요한 범위를 읽고 이전 버전과 구분한다. |
| 수정 수행 | 현재 유효한 hash/snapshot/receipt 계약을 따른다. archive의 과거 근거로 변경 권한을 얻지 않는다. 유효한 쓰기 결과 receipt의 연속 사용은 유지한다. |

추가 조건:

- `bodyComplete:false`는 현재 입력의 일부 생략을 뜻하며, 지금 질문의 모든 근거가 부족하다는 뜻은 아니다.
- 기존 `READ_ONLY_BATCH_INSTRUCTION`의 성공한 동일 읽기 반복 금지 문구도 함께 정리한다. 현재 판단에 필요한 원문이 입력에서 사라졌고 archive 복원이 불가능하거나 더 비효율적인 경우에는 동일 범위·버전의 제한적 재획득을 허용한다. 단순히 압축됐다는 이유로 예외를 적용하지 않는다.
- 위 예외 때문에 `tool-boundary.ts`의 fresh tool-planning retry 중복 차단이나 `shared-tool-core/repeat-cache.js`를 전역 해제하지 않는다. 일반 조사에서 필요한 원문 재획득과 실패한 도구 계획의 동일 호출 반복을 구분한다. `repeatReceipt`는 재읽기 허가 토큰이 아니므로, 실제 도구 계약을 그대로 따른다.
- `hasMore`는 더 읽을 수 있다는 사실이다. 필요한 판단 범위 또는 완전성 주장을 충족하기 위해 필요한 경우 이어 읽는다. 모든 파일의 EOF까지 무조건 읽으라는 규칙으로 만들지 않는다.
- 전체 부재 주장이나 전체 감사 완료를 말할 때는 실제 검색·검토 범위가 충족되어야 한다. 읽기량 감소를 위해 이 기준을 낮추지 않는다.
- 기존 note의 rejectedHypotheses와 openQuestions를 활용한다. note에 `nextTool`, `requiredSequence` 같은 금지된 실행 지시 키를 추가하거나 도구 출력의 suggestion을 영속 명령으로 저장하지 않는다.
- 기존 `inputAvailabilityMode=inject`를 기본으로 켜지 않는다. 현재 개별 projection notice와 제한된 runtime 요약의 역할은 유지하되, 새 중복 계층이나 availability 기본 주입을 추가하지 않는다.
- 필요한 근거를 찾았거나 후보가 반박됐으면 기존 note의 decisions/rejectedHypotheses/openQuestions에 근거 참조와 남은 질문을 짧게 남긴다. note는 가설 상태이며 원문 증명을 대체하지 않는다. 4항목/1500자 계약을 유지하고 장문의 분석을 복사하지 않는다.
- `held_reservations_insufficient` 후에는 이미 반환된 결과를 먼저 사용한다. 거절된 읽기가 여전히 필요하면 다음 라운드에 그 읽기만 재시도한다. 같은 단독 재시도에 추가 읽기를 묶지 않는다. 이 로그만으로 broker·예산 추정·기본 한도는 바꾸지 않는다.
- 새 원문 pinning, 함수별 archive 인덱스, 재요약 루프는 추가하지 않는다. 이 단계의 문구가 실제 모델 행동을 바꾸는지는 E5에서 판단한다.

완료 조건: 부분 원문과 과거 원문의 경계가 유지되고, 필요한 근거 선택 기준이 짧게 전달된다. deterministic 테스트는 문구·입력 연결을 증명하며 실제 재읽기 감소는 증명하지 않는다.

### E4. 공통 지침과 생성 계약을 맞춘다

1. Unreal의 소속/직접 부모/반영 매크로 관련 규칙은 기존 `docs/model-guidance/unreal.md` verification 영역에 통합한다. 일반적인 압축 복구 기준은 공통 실행 지침에 둔다.
2. `policy.claims.*`처럼 생성되는 계약 요약을 바꿀 필요가 있으면 원본 계약 소유자부터 수정하고 generator를 사용한다. 생성 파일만 수동 편집하지 않는다.
3. 공개 도구 스키마가 실제로 바뀔 때만 `direct_rag_contract.py`, tool contract 생성기 및 소비자 테스트를 함께 갱신한다. 새 도구나 옵션을 먼저 추가하고 나중에 사용처를 찾는 방식을 피한다.
4. 현재 `detailLevel`은 주로 응답 크기를 조절한다. 이를 locator-only라는 새 의미로 조용히 바꾸지 않는다. 별도 모드가 필요하다는 실측 근거가 없으면 기존 입력 계약을 유지한다.
5. [VERSIONING.md](../docs/VERSIONING.md)에 따라 공개 출력·동작이 바뀐 구성 요소의 버전을 갱신한다. 제품 전체 버전을 억지로 맞추거나 설치 정책을 바꾸지 않는다.

6. 이미 존재하는 근거 우선 규칙을 짧게 정리한다. 실제 조건과 결론의 일치, API 기억/가상 소스와 직접 소스의 구분, 반대 근거 반영을 포함한다. P0/P1을 반드시 찾아야 한다는 목표를 두지 않는다. 테스트 파일의 존재를 테스트 통과로, 근거 JSON 형식 성공을 사실 검증으로 표시하지 않는다.
7. 진행/최종 보고는 현재 질문에 충분한 근거, 파일 취득 범위, 검토 완료, 실행 검증을 구분한다. 생략된 내용은 추정해 채우지 않는다. 사용자 요청의 감사 범위가 남았다면 미확인 항목과 이유를 명시하며 완료라고 하지 않는다.
8. 현재 미해결 질문에 필요한 새 근거가 없다면 이미 반박한 결함을 다시 찾는 대신 결과 작성으로 진행한다. 예산/선택된 audit 모드의 기존 종료 조건을 따르면 partial/final 상태를 정확히 전달한다. 전체 감사를 임의의 작은 읽기 횟수로 중단시키지 않는다.
9. S8/S10은 기존 raw-tool-intent 및 reasoning recovery 계약의 fixture/replay를 먼저 보강한다. structured request → dispatch → paired result가 없는 XML은 근거 취득 성공으로 취급하지 않는다. 현재 경로가 이미 한 번의 재시도·partial delivery를 제공하므로 새 XML 실행기·추론 재시도 루프·기본 bounded 모드 변경은 하지 않는다. 실제 trace에서 결함이 재현될 때만 해당 기존 함수의 최소 수정안을 별도로 확정한다.
10. `core.md`/`design.md`의 SSOT·SOLID·수명 기준이 이미 같은 의미를 제공하면 중복 경고를 계속 붙이지 않는다. E5에서 실제 주입 여부와 모델 준수 여부를 구분한다. reasoning recovery가 전체 재분석으로 흐르는지 확인하되 추론 한도를 무조건 올리는 처방은 하지 않는다.

완료 조건: 공개 설명, 실제 응답, Compactor 관찰, 생성 계약이 일치하고 필수 검사에서 drift가 없다. 로그만으로 runtime 버그를 단정해 변경 범위를 확대하지 않는다.

### E5. 정확도와 지연을 함께 비교한다

기존 `eval-audit-pressure.cjs`, `eval-input-availability-ab.cjs`, `trace_lmstudio_run.py`의 책임을 재사용한다. 범용 benchmark framework를 새로 만들지 않는다. 기존 평가의 synthetic 도구만으로 실제 엔진 MCP 연결을 검증했다고 말하지 않는다.

최소 비교 작업:

1. 잘못된 owner → 정확한 선언 소속과 구현 도달.
2. 중간 클래스의 `Super` override 확인.
3. RPC 선언·`_Implementation`·권한/소유권 조건의 필요한 근거 확인.
4. 작은 함수와 큰 함수/overload의 범위 읽기.
5. 압축 전후 동일 조사 지속, 질문과 무관한 과거 projection 포함.
6. 파일 변경/엔진 교체/만료 archive 뒤 과거 근거와 현재 근거 구분.
7. 여러 파일의 전체 감사에서 미확인 범위를 남기고, 일부 읽기를 전체 완료로 보고하지 않음.
8. 실제 모듈 루트의 다른 등록 매크로를 찾아 P0 추정을 철회하고 재등장시키지 않음.
9. 읽기 예산 거절 뒤 필요한 요청만 단독 복구하고 기존 결과를 재사용함.
10. XML 호출 모방과 structured 실행을 구분하며 허용된 재계획/partial 상태로 종료함.
11. 추론 한도 복구 뒤 이미 확인된 분석 전체를 반복하지 않고 작은 다음 단계나 보고로 진행함.
12. 동일 질문에 필요한 근거가 충분해졌을 때 보고서 작성으로 진행하고, 압축/출력 중단 뒤에도 확인된 내용과 미확인 범위를 보존함.

각 작업은 E0에서 식별한 Swift 1.5의 같은 모델·양자화·컨텍스트·추론/출력 설정·하드웨어·도구 권한으로 baseline/new를 비교한다. 모델이 없거나 설정을 확인할 수 없으면 해당 비교는 미실시/재현 불가로 표시하고 다른 모델 결과는 별도 평가로 둔다. 가능하면 사례별 최소 3회 교차 실행하고 중앙값과 범위를 적는다. source catalog 및 모델 캐시의 cold/warm 조건을 구분한다. 모델이 바뀌면 비교를 섞지 않는다.

측정할 값:

- 첫 정확한 엔진 근거까지의 시간, 첫 유용한 사용자 보고까지의 시간, 작업 전체 시간, 최종 보고 전달 여부와 미완료 이유.
- 실제 입력 토큰과 도구 응답 크기. UTF-8 bytes, UTF-16 chars, 토큰 추정/실측을 구분한다.
- 모델에게 반복 전달된 **동일 버전·겹치는 소스 범위**의 양. 서버 내부 디스크 읽기와 분리한다.
- 실제 이벤트에 근거한 검색 범위 확대, 읽기 호출, budget denial, 압축, archive 복원, reasoning recovery 횟수. XML/읽기 의도/모델 서술은 실제 호출 횟수에서 제외한다.
- 측정 가능한 요약 시간·모델 prefill/생성 시간. 제공되지 않는 값은 unavailable로 둔다.
- 정답 근거, 잘못된 owner 재사용, 반박된 결함의 재등장, 조건과 결론의 불일치, 근거 없는 P0/P1, 누락, 근거 없는 완료/부재 주장.
- 필요한 원문 재획득과 무관한 전체 재수집을 분리한 비율, 복구 후 첫 행동, 실제 request/dispatch/result 짝과 proposed/executed 인자 차이. 기존 trace 필드를 재사용하며 없는 항목만 좁게 보완한다.
- 파일 수정 작업도 단일 함수 수정, 선언/구현 2파일 동시 변경, 4파일 이상 의존 변경, 중간 압축·외부 변경·부분 실패·취소를 포함한다. 전체 고유 파일 수/호출당 파일·patch 수/변경 줄·bytes/재읽기량/충돌·복구/검증 대기와 결과의 source identity를 따로 측정한다.
- 단계별 차이: 우선 전체 baseline/new 비교 뒤 회귀나 원인 불명 개선만 E1/E2와 E3/E4를 분리해 확인한다. 모든 지침을 한꺼번에 늘리고 모델 변경까지 섞지 않는다.

`trace_lmstudio_run.py`는 대화 로그에 없는 실제 라운드 모델 입력을 `not_recorded`로 취급한다. 원시 tool result 크기를 실제 모델 입력 크기로 대체하지 않는다. 기존 final SDK input 관측을 연결해야 하며, SDK 조립 입력과 모델 서버가 처리한 입력의 확인 수준도 구분한다.

완료 조건: 정확성/범위/변경 권한 회귀가 없어야 한다. 중복 소스량이나 호출량이 줄어도 전체 지연이 늘면 성공으로 선언하지 않는다. 효과가 없는 변경은 추가 복잡도로 덮지 않고 해당 변경만 제거하거나 보류한다. 임의의 개선 배수를 약속하지 않는다.

### E6. 회귀 검사와 인계

수정 함수의 focused test를 먼저 실행한 뒤 관련 전체 suite를 실행한다. TS 변경을 기존 dist에 대한 테스트 성공으로 대신하지 않도록 해당 소스를 빌드한 후 실행하고, 입력 source/build identity와 결과를 연결한다. 테스트 목록을 새로 하드코딩하지 않고 기존 runner를 사용한다. 제품 build가 생성 소스와 runtime identity를 갱신할 수 있으므로 diff를 확인한다.

PowerShell, 저장소 루트에서 실행할 기준 명령:

```powershell
$env:PYTHONUTF8 = '1'
python -X utf8 -m pytest -q tests/test_direct_rag_engine_local.py tests/test_engine_definition_evidence.py tests/test_engine_header_evidence_identity.py
npm.cmd --prefix lmstudio-unreal-agent-mcp test
npm.cmd --prefix lmstudio-context-compactor-plugin test
npm.cmd --prefix lmstudio-context-compactor-plugin run guidance:check
npm.cmd --prefix lmstudio-context-compactor-plugin run toolcontract:check
npm.cmd --prefix lmstudio-context-compactor-plugin run unitycontract:check
npm.cmd --prefix lmstudio-context-compactor-plugin run identity:check
python -X utf8 -m pytest -q
```

공통 schema/계약에 영향을 준 경우 Unity MCP 테스트도 실행한다. 실제 파일 삭제나 외부 프로젝트 변경이 필요한 테스트는 임시 fixture와 기존 격리 runner로 수행한다. 테스트가 사용자 설정을 건드리지 않는지 확인한다.

최종 인계에는 변경 파일과 이유, 실제 테스트 결과/skip 이유, 실모델 A/B 설정과 결과, 남은 한계, 커밋 제목·본문 초안을 포함한다. 커밋·재설치·MCP 재로드는 이 계획의 자동 마지막 단계가 아니다.

## 5. 필수 회귀 사례

| ID | 사례 | 지켜야 하는 결과 |
|---|---|---|
| R1 | 잘못된 owner와 올바른 선언이 다른 헤더에 존재 | 같은 잘못된 qualified 검색만 반복하지 않고 관찰된 모듈 검색 후보를 제공 |
| R2 | owner 없음, 함수명과 헤더 파일명이 다름 | 현재 filename heuristic 한계를 숨기지 않음; 전역 semantic 검색 성공으로 표시하지 않음 |
| R3 | 동일 이름의 다른 클래스·오버로드·호출식 | 첫 텍스트 일치를 올바른 정의로 확정하지 않음 |
| R4 | 중간 부모 override·다중 상속·inline·반영 thunk | 기존 ambiguity/상속/반영 선언 조건 보존 |
| R5 | 선언 조회 또는 최종 envelope 예산 소진 | identity/hash를 잘라 만들지 않고 partial·omitted와 유효한 다음 조회 표시 |
| R6 | 40줄/3200자 발췌 이후에 중요한 분기 존재 | 본문 전체 검토로 오인하지 않고 필요한 정확한 범위 확보 |
| R7 | 큰 모듈·plugin·Editor/Developer 소스 | Runtime 하드코딩을 만능 경로로 취급하지 않으며 scope/limit에 맞게 단계 확대 |
| R8 | 현재 질문과 무관한 과거 partial 결과 | 모든 생략 본문이나 관련 파일 전체의 복원을 요구하지 않음 |
| R9 | 작은 근거 archive·전체 파일 archive·만료 archive | 적절한 부분 복원 또는 좁은 fresh read 선택; UTF-16 offset을 줄 번호로 사용하지 않음 |
| R10 | 2회 압축, 반박 가설, note 한도/요약 실패 | 기존 상태·근거 참조 경계 유지; summary를 새 사실로 승격하지 않음 |
| R11 | 외부 파일 변경·프로젝트/엔진 교체·오래된 receipt | 과거 근거를 현재 버전/쓰기 권한으로 사용하지 않음 |
| R12 | 유효한 쓰기 결과 receipt로 연속 수정 | 불필요한 중간 재읽기를 새로 강제하지 않음 |
| R13 | archive EOF, 검색 partial, 일부 파일 검토 | 복원 완료·파일 coverage·의미적 검토 완료를 구분 |
| R14 | S1: 병렬 reservation으로 한 요청 거절 | 거절은 not_executed, 기존 결과 보존, 필요한 읽기만 다음 라운드 단독 재시도 |
| R15 | S2: 가상 RPC wrapper·조건과 결론 불일치 | 직접 소스와 추정을 구분; 확인된 조건에 반하는 판단이나 근거 없는 심각도 확정 없음 |
| R16 | S3: 정확한 owner가 이미 있었는데 잘못된 owner 재등장 | 근거 참조를 재사용하고 헤더 길이/검색 0건을 엔진 손상으로 확정하지 않음 |
| R17 | S4: Private 경로 없음, 루트에 PRIMARY_GAME_MODULE 존재 | 경로·매크로 범위를 보완하고 등록 누락/시작 불가를 성급히 확정하지 않음 |
| R18 | S5: archive 활용 후 projection 문구로 여러 파일 전체 재읽기 | 남은 질문의 필요한 함수만 복원/재획득; 무관한 과거 파일 재수집 없음 |
| R19 | S6: 필수 근거는 충분하나 무관한 꼬리가 생략됨 | 불필요한 EOF 읽기 강제 없이 partial 범위를 정직하게 보고; 생략 내용 창작 없음 |
| R20 | S7: 클라이언트에서 부른 일반 함수 안에 Server RPC 분기 | 실제 함수명·선언·분기·상태 변경을 확인하고 함수 호출 자체를 권한 버그로 오인하지 않음 |
| R21 | S8: 첨부와 같은 본문 tool_call XML | raw intent와 실행 결과 구분; 기존 재계획/partial 계약 유지; 성공 근거로 계산하지 않음 |
| R22 | S9: 보고 직전 전체 재조사 반복·보고 이어쓰기 | 기존 모드/종료 계약 아래 확인 사실·미확인을 전달; 새 근거 없는 전체 감사 재시작을 평가 |
| R23 | S10: 도구 요청 없는 reasoning output limit 뒤 재시도 | 기존 단일 복구·허용/거절 조건 보존; 다음 작은 행동/보고 여부 평가; 무한 복구 없음 |
| R24 | 정본 계약 변경과 생성물 drift/버전 적용 범위 | 원본→generator→소비자 일치, 생성물 수동 편집/검증 버전 우회 없음 |
| R25 | note 두 사본·목표/프로젝트 변경·archive 만료/보호 ref | 기존 복원 우선순위/무효화/보호 수명 유지; 별도 writer 없음 |
| R26 | 취소/timeout 후 늦은 SDK callback·동일 ID의 다른 라운드 | 종료 fence와 실행/라운드 identity 유지; 새 상태 오염 없음 |
| R27 | terminal seal·partial result·정확 측정 실패 | 저장과 dispatch/완료를 구분, 반환 근거 보존, 미완성 판단 승격 없음 |
| R28 | edit bundle 잠금 충돌·pre/post-publication 취소·archive 실패 | 실제 적용/복구 상태 보존, 잠금 해제, 성공을 미적용으로 바꾸지 않음 |
| R29 | 4파일 의존 변경을 2파일 이하 호출로 나누고 중간 압축 | 총 범위 유지, receipt 연속성, 여러 호출을 하나의 atomic transaction으로 보고하지 않음 |
| R30 | baseline/build/test와 같은 파일·생성물 동시 변경 | 입력 snapshot/source identity가 같은 결과만 해당 변경의 검증으로 인정; 생성기와 같은 산출물 writer는 순차화 |
| R31 | 계획 완료 체크·background 시작/취소·실제 test 결과 불일치 | 계획 상태/프로세스 상태/검증 상태 분리; 진행 중을 완료로 올리지 않고 재개 시 필요한 부분만 확인 |

추가 Swift·설계·작업 관리 사례의 검사 방식: R14의 예산/실행 상태, R21의 raw intent, R23의 복구 허용 조건은 기존 deterministic 테스트와 이벤트 replay로 계약을 확인한다. R15–R20의 판단 품질 및 R22의 보고 수렴은 정답 근거가 있는 실모델 평가가 필요하다. 단순 문자열 assertion 통과를 모델 행동 개선으로 표시하지 않는다. R24–R31은 바뀌는 경계에 맞춰 기존 생성물·수명·transaction·검증 provenance 사례를 재사용하고, 여러 파일 수정의 진행 품질은 실모델 결과와 함께 평가한다. 기존 `prediction-loop.test.cjs`, `report-continuation.test.cjs`, `recovery-state.test.cjs`, `execution-outcome.test.cjs` 등의 관련 사례를 재사용한다.

## 6. 이번 재검토에서 수행한 검증과 한계

제품 파일은 수정하지 않았다. 아래 테스트는 **Swift 로그 추가 분석 이전의 계획 검토에서 수행한 결과**다. Swift 추가 요청에서는 로그 1,829줄, 관련 하네스 함수와 게임 함수를 대조했다. 후속 요청에서는 현재 SSOT/가이던스 생성·note 복원·실행/라운드/예산/쓰기 수명과 수정 상한, Claude Code 공식 자료를 추가 조사해 계획·근거 JSON을 갱신했다. 제품 테스트/빌드/실모델 실행은 다시 수행하지 않았다. 문서의 근거 JSON 형식·로컬 링크·줄 번호 검사는 별도로 수행한다.

| 검사 | 결과 | 증명 범위 |
|---|---:|---|
| Python 엔진 조회/정의/헤더 identity 테스트 3개 파일 | 49 passed | 현재 source 기반 엔진 조회 계약 |
| Node engine-source-read + bounded-read | 24 passed, 1 skipped | 엔진 읽기·범위·경로 계약. symlink 생성 EPERM 1건 skip |
| Node failure-guidance + file-snapshot-flow | 25 passed | 모듈별 NOT_FOUND 복구, 검색 cursor, 현재 버전 및 연속 수정 계약 |
| Compactor working-context | 32 passed | 현재 src 직접 참조. archive·다중 압축·부분 복원 계약 |
| Compactor input-availability + continuity-model-notes | 24 passed | 일부는 기존 dist 참조. 현재 TS 소스를 새로 빌드한 검증으로 합산하지 않음 |
| owner 탐색 임시 fixture | 잘못된 owner 0 / owner 없음 0 / 정확한 owner 1 | owner 제거만으로 해결되지 않음을 작은 fixture에서 확인 |

후속 설계·파일 작업 관리 조사에서는 기존 `build-design-guidance.cjs --check`, `build-tool-contract.cjs --check`, `build-unity-tool-contract.cjs --check`, `build-runtime-identity.cjs --check`가 모두 종료 코드 0으로 통과했다. 생성물을 쓰지 않는 정합성 검사이며 실제 Swift 준수나 개선 효과를 증명하지 않는다.

위 결과는 변경 전 기존 동작의 근거다. 이번에 전체 suite, 실제 Claude Code 실행, 설치된 LM Studio에서 개선 후 성능 비교를 수행한 것은 아니다. 계획의 근거 JSON 형식 검사도 사실 검증이나 전체 작업 완료를 보장하지 않는다.

## 7. Claude Code 추가 조사: 공식 동작과 이 프로젝트 적용

조사일: 2026-10-18. 아래는 공식 문서/Anthropic 기술 글에서 확인한 공개 동작이다. Claude Code 비공개 내부 소스나 실제 설치본을 역공학·실행 검증한 결과가 아니다. 문서의 기능/도구 기본 제공 여부는 버전·모델·환경에 따라 달라질 수 있다. **SSOT/SOLID를 자동 보장한다거나 항상 함수 단위로만 읽거나 파일 N개만 고친다는 보편 계약은 확인하지 못했다.** 적용안 열은 현재 코드와 대조한 이 계획의 판단이다.

| 공식 자료에서 확인한 것 | 현재 구조에 적용할 판단·한계 |
|---|---|
| [동작 구조](https://code.claude.com/docs/en/how-claude-code-works): 모델의 판단과 하네스의 도구/컨텍스트 관리를 구분하며 수집→행동→검증을 반복. 큰 출력으로 압축이 반복되는 경우 중단하는 동작도 설명. | 함수 범위 검색과 실제 결과 검증을 한 단위로 연결. 기존 입력 예산·복구·최종화 재사용. 문서의 압축 전략을 새 Compactor로 복제하지 않음. |
| [메모리·규칙](https://code.claude.com/docs/en/memory): CLAUDE.md/auto memory는 맥락 자료이며 강제 설정이 아님. 경로별 규칙은 관련 파일 작업 때 로드되고, import로 나눈 파일은 시작 시 같이 로드될 수 있음. | 정본은 기존 guidance/catalog/contract에 유지. 긴 원칙 문서를 모든 라운드에 붙이지 않고 기존 선택/버전/토큰 경계를 확인. 문서 존재·전달·모델 준수·코드 강제를 각각 평가. |
| [Hook 계약](https://code.claude.com/docs/en/hooks#posttooluse): 실행 후 hook은 이미 발생한 효과를 되돌리지 않고 관찰 결과를 전달/변환. 같은 event의 hook은 병렬 실행될 수 있음. | 권한/hash 검사는 기존 실행 전 경계, 적용 사실과 관찰은 실행 후 경계에 둠. 검사/생성 순서가 필요한 작업은 하나의 기존 runner에서 순차 실행. LM Studio에 Claude 전용 hook 시스템을 추가하지 않음. |
| [도구 설계](https://www.anthropic.com/engineering/writing-tools-for-agents): 도구마다 분명한 목적, 관련 결과 위주의 반환, 실제 호출/결과·정확도·시간·토큰 평가를 권고. | 이미 있는 선언+구현 조회와 범위 도구를 활용. 중복 도구·숨은 광역 검색을 추가하지 않고 E5의 실패/복구 지표와 held-out 사례를 보강. |
| [컨텍스트 엔지니어링](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents): 가벼운 참조와 필요 시 검색을 조합. | 전체 엔진 선적재보다 실제 locator와 필요한 함수 근거 취득. archive의 과거 자료와 현재 쓰기 증명 분리. |
| [다중 파일 작업 권고](https://code.claude.com/docs/en/best-practices#explore-first-then-plan-then-code): 접근법·영향 파일·흐름을 조사하고 계획한 뒤 구현·검증. 작은 명확한 수정은 계획 비용을 줄임. | §8의 수정 목록과 의존 순서를 사용. 계약 단위로 읽고 고치며 관련 검사를 진행. 원문의 커밋 예시는 이번 사용자 요청에 적용하지 않음. |
| [편집 도구](https://code.claude.com/docs/en/tools-reference#edit-tool-behavior): exact 문자열 일치·중복 여부를 검사. read-before-edit와 변경된 파일 처리에는 버전/모델별 조건이 있음. | “Claude도 매번 파일 전체 재읽기”라는 전제를 버림. 이 MCP의 expectedHash/snapshot/receipt 규칙은 그대로 유지. Claude의 편집 허용 조건을 그대로 이식하지 않음. |
| [작업 도구](https://code.claude.com/docs/en/tools-reference#task-tool-availability): Task 계열은 상태·의존성 관리 기능이며 기본 제공은 모델/환경에 따라 다름. | Opus의 실제 세션에서 지원 도구부터 확인. 없는 Task 도구를 호출하거나 Swift에 같은 도구가 있다고 가정하지 않음. 지원하면 기존 기능, 아니면 이 MD의 짧은 진행 표로 구현 순서를 관리. |
| [체크리스트와 백그라운드](https://code.claude.com/docs/en/interactive-mode#task-list): 체크리스트와 실행 중 shell/agent 목록은 별도이며, background command는 ID와 출력 파일로 추적. | 계획 상태·프로세스 상태·검증 결과를 분리. 시작 ID만으로 성공 선언하지 않음. baseline이 읽는 소스를 동시에 수정하지 않고, 테스트 결과를 시작 시 source/build identity와 연결. |
| [서브에이전트](https://code.claude.com/docs/en/sub-agents): 기본 작업 디렉터리와 별도 worktree 격리를 구분하며 독립 컨텍스트 제공. | 조사/검토를 분리하는 원칙만 참고. 이번 로컬 하네스에 자동 멀티에이전트 스케줄러는 추가하지 않음. 병렬 수정 시 동일 파일 단일 writer, 격리 기준 버전과 최종 통합 검증 필요. |
| [Checkpoint 한계](https://code.claude.com/docs/en/checkpointing#limitations): shell 수정·외부 수정·일부 subagent 수정은 rewind로 모두 복구되지 않음. | checkpoint를 여러 MCP 호출의 트랜잭션 보장으로 오인하지 않음. 현재 lock/hash/journal/rollback 범위를 따르고 사용자 변경을 광역 되돌리지 않음. |
| [Managed Agents 설계](https://www.anthropic.com/engineering/managed-agents): 내구 session log와 하네스의 컨텍스트 변환을 다른 책임으로 분리. | archive 보존과 모델 입력 projection의 분리를 뒷받침하는 참고. **Claude Code의 내부 구현 설명은 아니며** 이 저장소를 분산 서비스로 재설계할 근거도 아님. |

[장기 작업 하네스 실험](https://www.anthropic.com/engineering/effective-harnesses-for-long-running-agents)의 항목별 진행/검증도 참고하되 자동 커밋은 도입하지 않는다. [프롬프트 캐시](https://code.claude.com/docs/en/prompt-caching), [MCP 지연 로딩](https://code.claude.com/docs/en/mcp), [clangd](https://clangd.llvm.org/installation)는 기존 보류 범위를 유지한다. 실제 Swift/LM Studio 환경의 효과가 확인되지 않은 기능을 성능 개선의 전제로 두지 않는다.

## 8. 파일 수정 범위와 작업 순서·진행 관리

### 8.1 전체 파일 수, 한 계약의 변경 묶음, 한 번의 도구 호출을 구분

현재 계획의 **우선 변경 검토 대상은 핵심 구현 4개 + 직접 회귀 후보 4개 + 파생 identity 1개 = 9개**다. 모두 반드시 수정한다는 확정 수가 아니다. 기존 테스트로 충분하면 테스트 파일은 수정하지 않고, 구현 후 실제 고유 변경 파일 수를 보고한다. 버전·가이던스·평가 runner의 조건부 파일까지 포함한 최종 수는 E0/E4에서 확정한다. 정확도를 희생하는 임의의 전체 파일 수 상한은 두지 않는다.

| 분류 | 우선 대상 | 이유/순서 |
|---|---|---|
| 핵심 1 | [engine_header_evidence.py](../scripts/engine_header_evidence.py) | E1의 실제 관찰 타입·모듈 메타데이터. 기존 identity/deadline/cache 책임 보존. |
| 핵심 2 | [direct_rag_engine_local.py](../scripts/direct_rag_engine_local.py) | E1 검색 제안과 E2 다음 범위 안내. 같은 파일이므로 두 단계를 한 담당자가 순차 반영. |
| 핵심 3 | [resume-facts.ts](../lmstudio-context-compactor-plugin/src/resume-facts.ts) | E3의 현재 질문 단위 복원 안내. |
| 핵심 4 | [execution-instructions.ts](../lmstudio-context-compactor-plugin/src/execution-instructions.ts) | 반복 금지 예외·필요 근거/단독 재시도/복구 안내 정합성. 기존 충분한 규칙은 유지. |
| 직접 회귀 4개 | [test_direct_rag_engine_local.py](../tests/test_direct_rag_engine_local.py), [test_engine_header_evidence_identity.py](../tests/test_engine_header_evidence_identity.py), [resume-facts.test.cjs](../lmstudio-context-compactor-plugin/test/resume-facts.test.cjs), [prediction-loop.test.cjs](../lmstudio-context-compactor-plugin/test/prediction-loop.test.cjs) | 바뀐 계약·문구의 실제 입력 연결을 확인하는 기존 소유자. TS 테스트는 새 build 후 검사. 단순 문구 검사는 실모델 준수 검증이 아님. |
| 파생물 1개 | [generated-runtime-identity.ts](../lmstudio-context-compactor-plugin/src/generated-runtime-identity.ts) | Compactor src/scripts/package 입력 변경 뒤 기존 generator가 갱신. 수동 편집하지 않음. |

조건부 파일은 다음처럼 변경 이유를 확인한 경우에만 포함한다.

- `engine_definition_evidence.py`/관련 test: 기존 후보 범위 정보만으로 E2가 해결되지 않을 때.
- `direct_rag_contract.py`, manifest/공개 계약·소비자: 실제 schema/effect/예산 의미가 바뀔 때. 단순 suggestion 개선에 불필요한 공개 옵션을 추가하지 않음.
- `working-context.js`, 관련 archive/notes/lifecycle tests: notice 또는 해당 계약을 실제로 변경할 때. 원칙 추가만을 이유로 상태 소유자를 수정하지 않음.
- `core.md`/`unreal.md`/`design.md`/catalog/engine contract와 생성 bundle: 기존 규칙에 실제 누락·충돌이 있을 때 정본부터 변경. 같은 문장을 여러 파일에 반복하지 않음.
- 기존 eval/trace runner: R14–R31의 측정에 필요한 도구/이벤트가 없을 때만 확장. 별도 scheduler/benchmark framework는 만들지 않음.
- 구성 요소 버전: `VERSIONING.md` 기준으로 Compactor package/manifest, 일치해야 하는 lock metadata 등 실제 관련 파일만 갱신. RAG 측 배포 identity 경로는 E0에서 소비자를 확인한 뒤 결정. 제품/Unity 버전을 일괄 올리지 않음.

Unreal MCP의 현재 source hard cap은 [direct-mutation-limits.js:3](../lmstudio-unreal-agent-mcp/src/direct-mutation-limits.js:3)에서 **호출당 최대 2파일·2개 patch operation, patch 새 본문 32줄, bundle 변경량 64줄**이며 문자·byte 상한도 함께 있다. 환경 설정은 이를 더 낮출 수 있다. 이는 전체 작업에서 고칠 수 있는 파일 수가 아니며 Claude Code의 Edit 도구 상한도 아니다. 설치본의 실제 설정은 실행 전에 확인하고 기존 권한·상한을 임의로 올리지 않는다.

파일 수만으로 배치 크기를 정하지 않는다. 함수 크기, 선언/구현 의존성, patch match/expectedHash, payload, 공유 읽기 예산, 복구 범위를 함께 본다. “4파일이니 2번이면 끝”이라고 계산할 수 없다. 한 파일에 여러 patch가 필요할 수 있고 생성·검증이 별도다. 이 상한 때문에 실제 지연이 발생하는지 E5에서 측정하되 이번 연구만으로 상한 완화나 우회 수정을 도입하지 않는다.

### 8.2 수정·검증 순서와 병렬 실행 기준

| 순서 | 작업과 선행 조건 | 함께 진행해도 되는 일 | 순차로 유지할 일/종료 증거 |
|---|---|---|---|
| W0 / E0 | 기준 HEAD·dirty diff·설정·실제 build identity·수정 후보/검사 목록 고정 | 공식 문서/관련 소스의 읽기, 별도 fixture 설계 | baseline이 읽는 트리 수정 금지. 동일 snapshot과 명령/결과를 남긴 뒤 baseline 완료. |
| W1 / E1–E2 | Python 엔진 후보→응답 안내 변경 | 수정 파일을 쓰지 않는 조사/독립 테스트만 | `engine_header_evidence` 계약→RAG 소비자→focused 회귀. 같은 `direct_rag_engine_local.py` 동시 수정 금지. |
| W2 / E3 | 복원 안내와 반복/복구 지침 정합성 | W1과 파일·출력·fixture가 분리된 경우 조사/검토 가능 | `resume-facts`와 instructions 수정→새 TS build→관련 입력/복원 검사. 기본은 단일 구현자가 W1 후 진행. |
| W3 / E4 | 구현 결과에 필요한 가이던스·계약·버전 반영 | 읽기 전용 리뷰 | 원본 계약/MD→generator→runtime identity→build. 동일 generated 파일에 여러 writer 금지. |
| W4 / E5 | baseline/new가 식별된 고정 build로 준비됨 | 격리된 로그 분석 | 같은 GPU/모델 자원을 쓰는 A/B는 교차 순차 실행. 측정 중 모델/설정/소스 교체 금지. |
| W5 / E6 | 관련 회귀와 범위 통합 | 독립 출력 디렉터리·읽기만 하는 suite는 동시 가능 | 공유 dist/build/install/fixture를 쓰는 검사 순차화. 최종 diff·검사 입력 identity·결과 확인 후 전달. 커밋/설치는 하지 않음. |

- 일정 단위는 **파일 하나가 아니라 검증 가능한 계약 변경 하나**다. 선언/구현/소비자가 함께 바뀌어야 하면 논리적으로 묶되 실제 MCP 호출은 기존 상한으로 나눈다. 여러 호출 전체가 자동 atomic이라고 보고하지 않는다.
- 한 파일의 writer는 한 시점에 하나로 둔다. 별도 조사자가 같은 파일을 읽는 것은 가능하지만, 미완성 중간 상태를 최종 baseline/검증으로 기록하지 않는다. 사용자/외부 변경을 발견하면 해당 함수와 hash만 다시 대조한다.
- 관련 파일 묶음의 변경을 마친 뒤 해당 계약 검사를 한다. 작은 patch마다 전체 suite를 재실행하지 않는다. 반대로 중간 테스트 통과를 그 뒤 변경까지 검증한 것으로 이월하지 않는다.
- 장기 baseline을 background로 돌리면서 같은 checkout의 소스를 고치면 어떤 버전을 검증했는지 불명확해질 수 있다. 완료 전에는 독립 연구를 하거나, uncommitted 변경까지 정확히 포함한 격리 snapshot에서 검사한다. worktree가 현재 dirty 변경을 자동 포함한다고 가정하지 않는다.
- background 시작은 process/task ID와 로그 위치만 증명한다. 완료 코드·오류·검사 대상 hash/build ID를 수집하고, timeout/취소/미수집은 검증 완료로 처리하지 않는다. 사용자가 취소하면 기존 실행 소유자가 정리하고 다음 작업의 상태로 늦은 결과를 합치지 않는다.
- 시간 약속은 실제 측정 뒤에 한다. 우선 의존 관계와 완료 조건으로 순서를 관리하고, 관측한 duration/병목만 기록한다. 주기 실행이나 알림용 스케줄러를 추가하는 요청으로 해석하지 않는다.

### 8.3 작업 상태를 잃지 않으면서 컨텍스트를 적게 쓰는 방법

Opus 실행자는 사용 가능한 기존 Task 도구가 있으면 쓰고, 없으면 이 MD의 E0–E6 진행 표를 갱신한다. 아래 한 줄 단위를 유지하며 모든 파일 원문·분석을 메모에 복사하지 않는다.

```text
작업 ID | 목적/계약 | 선행 작업 | 대상 파일·함수 | planned/in_progress/applied/verified/blocked | 근거·검사 결과 | 남은 질문
```

이 상태명은 **인계용 기록 형식**이며 현재 제품 API에 추가할 enum/schema가 아니다. `applied`는 diff/receipt, `verified`는 해당 버전에 대한 검사 결과를 요구한다. 작업 목록 완료 체크는 파일 적용·테스트 통과의 정본이 아니다. 현재 continuity의 사용자 작업 선택과 짧은 note는 전체 dependency scheduler가 아니므로 이를 강제로 확장하지 않는다.

압축·중단 후에는 현재 작업 ID, 최근 적용 파일의 identity/receipt, 마지막 검사 결과, 미해결 질문을 먼저 확인한다. 이미 완료한 모든 파일을 다시 읽지 않고, 다음 변경에 필요한 범위와 외부 변경 여부만 확인한다. 다음 patch에서 유효한 쓰기 receipt를 계속 쓸 수 있으면 재사용한다.

### 8.4 별도 Opus 조사에 전달할 질문

1. 위 9개 우선 대상 중 기존 기능으로 해결되어 실제 수정에서 제외할 수 있는 파일은 무엇인가? 조건부 파일이 필요하다면 어느 계약·소비자 때문에 추가되는가?
2. 현재 guidance가 Swift 실행의 실제 입력에 전달됐는가? 미전달·상충 문구·모델 미준수 중 어느 증거가 있는가? 새 지침의 토큰 비용과 정확도 효과를 분리할 수 있는가?
3. 두 note 저장 사본, archive/projection, 실행/관측 identity가 기존 소유자 규칙으로 갱신되는가? 실제 충돌이 없다면 SSOT 리팩터링을 하지 않아도 되는가?
4. 2파일/2patch·문자/줄 제한과 파일 재읽기가 다중 파일 작업에서 각각 얼마의 지연을 만드는가? 필요한 범위 읽기·receipt 재사용·검증 묶음만으로 줄일 수 있는가?
5. 계획 상태·background 실행·검증 결과를 어떤 기존 수단으로 연결하는가? baseline 중 소스 수정, stale dist 테스트, generator 동시 실행을 어떻게 방지하고 재현할 것인가?
6. 수정이 끝난 뒤에도 같은 문제가 남으면 모델 한계, 실제 입력, 도구 계약, 실행 복구 중 어느 근거가 있는가? 새로운 scheduler/ledger/요약 계층 없이 해결되는 대안을 먼저 비교했는가?

## 9. 최종 완료 기준

- [ ] E0의 기준 실행과 재현 사례가 남아 있다. Swift 1.5라는 사용자 지정과 실제 모델 metadata를 구분했다.
- [ ] S1–S10/R14–R23의 로그 관찰과 실행 증거를 구분하고, 서술을 실제 호출/압축/토큰 수로 집계하지 않았다.
- [ ] 엔진 조회 실패에서 잘못된 owner를 고정한 광역 검색 제안을 개선했다.
- [ ] 이미 있는 선언+구현 조회를 재사용하고 필요한 다음 읽기만 제시한다.
- [ ] 압축 자체가 전체 재읽기의 사유가 되지 않도록 안내했다.
- [ ] 새 메모리/요약/캐시 계층 없이 기존 상태와 근거 복원 경계를 유지했다.
- [ ] 현재 입력의 원문, 과거 근거, 현재 파일 버전, 변경 권한, 검토 완료를 구분한다.
- [ ] 반박된 owner/모듈 누락/권한 가설의 재등장, 근거 없는 심각도와 완료 범위 과장, 보고 전달 여부를 평가했다.
- [ ] 예산·XML·추론 복구의 기존 경로를 재사용했고 trace 없이 새 runtime 원인을 확정하지 않았다.
- [ ] 관련 회귀·생성 계약 검사가 통과했고 skip/미확인 범위를 기록했다.
- [ ] 실모델 평가를 했다면 동일 조건의 정확도·지연 결과를 함께 제시했다. 못 했다면 성능 개선 확정으로 보고하지 않았다.
- [ ] 각 변경에서 SSOT·SOLID·공개 계약·상태 writer·생성/종료 책임을 확인하고 §3의 기존 소유자·무효화·취소 경계를 보존했다.
- [ ] 예상/실제 고유 변경 파일 수, 조건부 추가 사유, 단계 의존성과 단일 writer를 기록했다. 다중 MCP 호출을 하나의 트랜잭션으로 오인하지 않았다.
- [ ] background 시작·작업 목록 완료·파일 적용·해당 버전 검증을 구분했고 baseline/build/test의 입력 버전이 섞이지 않았다.
- [ ] 사용자 설정·권한을 유지했고 커밋하지 않았다.

검토 근거 기록: [harness-targeted-engine-read-plan-20261018.json](../docs/evidence/harness-targeted-engine-read-plan-20261018.json)
