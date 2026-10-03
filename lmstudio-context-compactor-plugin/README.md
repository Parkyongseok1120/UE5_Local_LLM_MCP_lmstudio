# LM Studio 대화 압축기

긴 채팅에서는 코드·로그·도구 결과가 쌓여 모델이 한 번에 읽을 수 있는 양을 넘길 수 있습니다. 이 플러그인은 최신 사용자 요청과 최근 대화를 남기고 오래된 내용을 줄여 주는 보조 기능입니다.

모델 선택, 생성 설정, 파일 수정·빌드 권한은 바꾸지 않습니다. 선택된 프로젝트 엔진에 맞춰 모델에 보이는 도구 목록만 줄입니다.

## 채팅 압축 활성화 설정

1. LM Studio에서 실제 AI 모델을 불러오고 선택합니다.
2. 설치·업데이트 시 당시 저장된 기존 채팅의 `codex/unreal-context-compactor`를 켭니다(`ON`).
3. 압축, Unity/Unreal 도구 범위 차단, 첨부 문서 범위 읽기가 필요 없는 짧은 채팅에서는 끌 수 있습니다.
4. 대화는 그대로 두고 사용량만 보고 싶으면 `Observe only`를 사용합니다.

설치기는 파일을 설치하고 목록에 고정한 뒤, 정상적인 `*.conversation.json`에 플러그인 ID를 추가합니다. 손상되거나 알 수 없는 형식의 채팅 파일은 건너뜁니다. 적용 후 LM Studio를 재시작하면 GUI의 단일 스위치가 `ON`으로 표시됩니다.

## Unity/Unreal 도구 범위

`Project engine`은 모델 호출 전에 도구 목록을 결정적으로 줄입니다. `Unity`에서는 `mcp/unity-tools`, `Unreal Engine`에서는 `mcp/unreal-agent`와 `mcp/unreal-rag`만 노출합니다. 필터를 우회해 숨겨진 도구 이름을 호출해도 실행 직전에 거부합니다. 공통 도구는 유지합니다.

`Auto`는 GUI에 지정한 경로, 사용자가 대화에서 가장 최근에 언급한 실제 절대 경로, 현재 작업 폴더 순서로 프로젝트 표식을 확인합니다. Unity는 `Assets`, `Packages/manifest.json`, `ProjectSettings/ProjectVersion.txt`가 모두 있어야 하며, Unreal은 같은 폴더의 `.uproject`가 정확히 하나여야 합니다. 존재하지 않는 경로나 표식이 없는 폴더는 신뢰하지 않습니다. Unity와 Unreal 도구가 함께 연결돼 있는데 범위를 확정할 수 없으면 두 엔진 도구를 모두 숨깁니다. 양쪽을 실제로 함께 써야 하는 작업만 `Both`를 명시적으로 선택합니다.

`Project identity`를 지정하면 Unity·Unreal 도구 스키마에 `project` 인자가 있는 호출을 그 값으로 고정합니다. Unreal의 `set_active_project`도 같은 값으로 고정합니다. Unity MCP는 설치 시 `UNITY_PROJECT_ROOT` 하나에 결합되며 다른 root가 전달되면 명시적으로 거부합니다.

공통 Git 도구, 호환 경로와 기본 비활성 실험 옵션은 [Workspace 기능과 호환 경계](../docs/Workspace_Capabilities.md)를 참고하세요.

엔진 범위 선택은 문자열 규칙, 파일 표식, 도구 스키마만 사용합니다. 범위 선택 자체가 모델을 호출하거나 도구 순서를 정하지 않습니다. 생성 실패의 복구는 아래의 별도 정책을 따르며 `read_file` 등의 고정 호출 횟수 제한은 추가하지 않습니다.

## 선택적 설계 참고 자료

`Design references`는 기본 **Off**입니다. Common core, SSOT/SOLID, Debugging, Formatting, Engine lifetime, Multiplayer 중 하나를 선택하면 공통 기준과 관련 문서를 모델 입력에 제공합니다. 엔진 문서는 명시적 설정 또는 확인된 프로젝트 범위를 사용하며, 연결된 도구 목록만으로 엔진을 추정해 넣지 않습니다.

**Auto**를 선택하면 출처와 완전성이 확인된 사용자 목적에서 주요 주제를 고릅니다. 명시적으로 함께 요청한 주제가 둘이면 하나를 보조로 제공하며, 현재 유효한 진단·대기 관찰이 있으면 debugging을 보조로 우선합니다. 세 주제 이상, 불명확하거나 잘린 목적은 짧은 공통 기준으로 축소합니다. API 조회·빌드·추가 모델 호출을 자동 실행하지 않습니다. Auto는 항상 Focused로 전달하며 저장된 delivery 설정은 유지합니다. 기존 문서 수·실제 토큰 상한을 적용하고 예산 부족·복구·최종화에서는 기존 규칙대로 생략합니다. 이전 버전에서 Auto 값을 읽으면 Off로 처리됩니다.

활성 참고의 공통 기준에는 적용 버전의 API 선언, 소유자·종료·늦은 결과의 수명, 실제 수정 반환과 변경 내용의 대조가 포함됩니다. 참고를 명확히 제외한 요청은 Auto에서도 생략합니다. 코드 블록·인용·경로는 주제 선택 근거로 사용하지 않습니다.

`Design reference token allowance`는 기본 **2048**이며, 참고 자료로 증가한 실제 입력 토큰의 상한입니다. 0이면 제공하지 않습니다. 기존 출력 예약과 작업 창·도구 결과의 공유 예산도 함께 지킵니다. 문서를 위한 추가 압축·출력 한도 축소·정적 검사 의무·도구 실행 차단은 없습니다. 예산이 부족하면 엔진/주제 문서부터 생략하고 core도 맞지 않으면 원래 입력을 사용합니다.

자료는 각 입력에 한 번 합성하고 대화 이력·영속 메모리에 저장하지 않습니다. Observe only와 강제 최종 보고에서는 생략합니다. `Show debug info`의 `design_guidance_input`에 선택/생략 문서와 실제 증가 토큰이 기록됩니다.

문서의 정본은 저장소의 `docs/model-guidance/catalog.json`과 해당 Markdown입니다. `npm run guidance:build`와 일반 build가 배포용 데이터를 생성합니다. 프로젝트별 현황·조사 예시는 번들에 넣지 않으며 실제 모델 품질 개선 효과는 아직 측정하지 않았습니다.

## 적용 API와 실제 수정 근거

`mcp/unreal-rag`의 `unreal_symbol_lookup`은 기존 index 조회 외에 명시적인 `sourceMode: "engine_local"`을 지원합니다. 예를 들어 `query: "AActor::SetLifeSpan"`, `project: "C:/Projects/Game/Game.uproject"`와 함께 요청합니다. `owner`와 단일 symbol을 따로 줄 수도 있습니다. 기본값은 기존 index이며 Auto가 조회를 대신 실행하지 않습니다.

로컬 조회는 기존 프로젝트·엔진 resolver와 설치된 `Build.version`으로 출처를 확인합니다. Core/CoreUObject/Engine 및 Input 관련 EnhancedInput의 파일명 후보를 제한하여 최대 4개 헤더, 파일당 256 KiB, 총 1 MiB를 읽습니다. 헤더 단계에는 3초 deadline 확인이 있으며 프로젝트 이름 검색·OS I/O·transport 전체의 즉시 취소나 최대 지연을 보장하지는 않습니다. 잘린 파일의 SHA는 byte range로 표시합니다. 결과가 없다는 사실은 API 부재의 증명이 아니며 상속·매크로·overload의 실제 호출 가능성은 별도 확인이 필요합니다.

Unreal Agent의 성공한 `write_file`/`replace_in_file`/`apply_edit_bundle`은 실제 적용 전후 내용에서 작은 `changeEvidence`를 선택적으로 반환합니다. 현재 실행에서 provider·호출 대응·프로젝트·hash가 확인된 경우만 기존 파일 관찰에 연결합니다. 압축된 시스템 사실에는 출처·hash만 남고 변경 본문은 별도 assistant 데이터에 최대 8개/2048자로 보존합니다. 본문은 예산에 따라 통째로 생략할 수 있으며 재읽기·수정 권한·기능 완료 증명이 아닙니다. 실패·충돌·다른 hash·순서가 불명확한 같은 파일의 동시 결과는 이전 변경 근거를 무효화합니다.


## 출력 표시

선택한 모델이 보내는 생성 조각을 기다리지 않고 바로 표시합니다. SDK가 reasoning으로 표시한 조각은 LM Studio의 접을 수 있는 `생각` 블록에 넣고, 최종 답변 조각은 일반 메시지에 넣습니다. reasoning 경계용 내부 문자열과 비공개 `continuity-note` 꼬리말은 일반 답변에 표시하지 않습니다.

도구 호출을 생성하는 동안에는 도구 이름과 인수 생성량을 상태 블록에 갱신합니다. 확정된 도구 요청과 완료된 도구 결과는 전체 라운드가 끝날 때까지 보관하지 않고 각각 도착 즉시 GUI에 추가합니다. 도구 실행 뒤의 최종 답변도 같은 조각 단위 출력 경로를 사용합니다.

라운드별 컨텍스트 측정값과 압축 결과는 기본적으로 디버그 정보에 표시합니다. 디버그 값은 후보 메모리가 아니라 실제 직렬화된 체크포인트에서 다시 읽으며, 압축 세대·스키마·전후 메시지/토큰 예산·파일 관찰 및 읽은 범위 수·예산으로 빠진 관찰 수·판단 노트 상태를 포함합니다. 파일 본문, 모델 reasoning, 승인 정보, 파일 수정 receipt는 표시하지 않습니다. 필요하면 `Show debug info`를 끌 수 있습니다.

`Current input availability`의 기본값 `Observe`는 프롬프트를 바꾸지 않고, 각 prediction의 최종 SDK `Chat`에 실제 남은 도구 결과 본문만 계측합니다. `executionId`와 `modelInputId`, 도구 요청을 생성한 입력, 호출별 생성·승인·실행 상태, 프로젝트 식별자 digest, 자료 종류, 보고된 파일 버전, 실제 반환 범위와 현재 입력 범위를 연결합니다. 이전 체크포인트의 파일 범위, 경로·hash 문자열, assistant의 “읽었다”는 문장, 메타데이터 자체는 현재 원문으로 세지 않습니다. 호스트가 SDK 호출 뒤에 수행하는 템플릿 처리를 직접 관측하지 못하면 `hostInputVerification: unknown`을 유지합니다.

`Inject facts (experimental)`은 같은 계측 결과를 짧은 system 사실 블록으로 추가하는 A/B 실험용입니다. `full`은 과거에 실제 반환된 비교 범위가 이번 SDK 입력에 같은 버전의 원문으로 모두 있다는 뜻일 뿐, 모델이 이해했거나 검토를 완료했다는 뜻이 아닙니다. 이 블록은 재조회 필요성·다음 도구·완료 여부를 결정하지 않고, 도구 호출을 차단하거나 캐시 결과로 대체하지 않습니다. 추가 후 전체 프롬프트를 다시 측정하며, 예산을 넘으면 한 번의 기존 긴급 압축과 재계산 뒤 명시적 예산 실패 경로를 사용합니다. 목록 상한으로 빠진 항목 수는 `omittedEntryCount`로 구분하며 목록 밖 자료를 현재 부재라고 단정하지 않습니다.

## 재조회 가능한 working context (실험적)

새 설치의 `Working context` 기본값은 `Hybrid`입니다. `Legacy`는 기존 동작을 유지하는 명시적 호환 선택이고, `Deterministic archive/window`는 semantic handoff 없이 archive/window만 사용합니다. Hybrid와 Deterministic는 완료된 read-only 도구 결과를 현재 대화·fork·workspace·repository 범위의 로컬 archive에 먼저 보관하고 hash를 다시 검증한 뒤, 큰 결과만 bounded projection으로 바꿉니다. 직접 JSON, MCP text block, `structuredContent`처럼 SDK가 반환할 수 있는 envelope은 공통 decoder로 의미 payload를 얻되, archive에는 원래 provider envelope을 보관합니다. 아직 완료된 모델 입력에 제공하지 않은 정상 관찰은 크기만으로 미리 축소하지 않습니다. 최초 입력은 원문으로 측정하고, 전체 입력 예산이 맞지 않을 때 기존 bounded projection과 압축 경로를 적용해 다시 측정합니다. projection은 원래 call ID, tool 이름, status/error, source identity/hash/version, 실제 반환 범위, 모델에 제공한 발췌 범위, 생략 범위와 `evidenceId`/version을 유지합니다. 본문 발췌·생략 범위는 입력 노출 기록에도 남기며, 전체 raw를 제공했다고 표시하지 않습니다. `evidence_first_read_context`는 정확한 ID와 version, 제한된 문자 범위만 받고 과거 반환 자료를 다시 읽습니다. 응답 전체는 metadata를 포함해 byte budget 안에 맞추며 archive의 `archiveHasMore`/`archiveReachedEnd`와 원래 Git 결과의 `sourcePageHasMore`/`sourceResultComplete`를 별도로 표시합니다. archive EOF는 Git source 목록 완료를 뜻하지 않습니다. 파일 경로를 받지 않으며 현재 파일의 수정 권한이나 fresh-read receipt를 만들지 않습니다.

Unreal 소스 읽기의 공개 스키마에 `maxBytes`가 있으면, 값이 생략된 요청은 기본 4 KiB 이하의 창으로 공유 배치 예산을 예약합니다. 명시한 큰 창은 공개 한계와 남은 예산 안에서 허용합니다. 예약은 반환 envelope을 위한 추정치이며 실제 반환량도 반영합니다. 과도한 반환 뒤에는 같은 배치의 다음 호출을 막고, 다음 모델 입력은 기존 정확한 토큰 측정으로 확인합니다. 승인된 모든 읽기의 실행이나 전체 파일 수집을 보장하는 설정은 아닙니다.

pending/중복 call ID, 실행 효과가 불명확한 결과, write/build 결과는 projection하지 않습니다. archive 저장·hash·TTL·quota·scope 검증이 실패하면 원문을 유지합니다. receipt, approval capability, API token, Git cursor 같은 임시 값은 archive에서 제거하고 `redacted`와 원본/보관 hash를 구분합니다. Git cursor는 archive ID가 아니며 추측하거나 재구성하지 않습니다.

서명된 숨은 user-turn marker가 대화와 parent/child lineage를 연결합니다. 다음 턴에는 이전 compacted prefix와 새 delta만 모델 입력으로 재조립합니다. 유효하게 서명된 marker라도 사용자 메시지 수정·새 fork·stale prefix이면 저장 window를 적용하지 않고 현재 전체 history로 안전하게 계속합니다. 서명 위조와 대화·workspace·repository scope 불일치는 fail-closed입니다. 원래 GUI transcript를 삭제하지 않습니다. archive와 window는 `~/.lmstudio/unreal-context-compactor/hybrid-v1` 아래에 저장되며 `Legacy`로 되돌리면 즉시 사용을 중단합니다. 데이터를 없애려면 LM Studio를 종료한 뒤 이 `hybrid-v1` 디렉터리만 삭제합니다. 기존 `notes-v1`과 채팅 파일은 migration 대상이 아닙니다.

압축 시작값 `Working input trigger`와 압축 후 `Working input target`은 별도이며 38,912 context 실험의 설치 기본값은 각각 22,000/18,000 tokens입니다. soft/hard remaining은 실제 output cap과 safety를 제외하고 남은 공간을 기준으로 6,000/3,000이며, output reserve 8,192와 safety 2,048을 사용합니다. system, 현재 요청, 필요한 protocol, 도구 정의를 포함한 최종 template를 선택 모델 tokenizer로 측정합니다. 필수 입력이 target을 넘으면 `mandatoryFloorTokens`를 기록하고 필수 내용을 유지합니다. 38,912는 모델이 loaded context를 보고하지 않을 때만 쓰는 fallback이며 보고된 context를 덮어쓰지 않습니다. Luna의 generation reserve/cap/safety 계산은 그대로 적용합니다.

`Hybrid semantic handoff`는 deterministic 경로에 더해 실제 checkpoint를 만든 accepted compaction 이벤트마다 최대 한 번, 같은 로컬 모델을 `tools=[]`로 호출합니다. 단순 archive projection만 일어난 prediction에서는 semantic 호출을 만들지 않습니다. 입력에는 archive ID만 주지 않고 해당 ref에서 검증한 bounded evidence excerpt와 deterministic facts를 함께 넣습니다. 출력은 그 근거를 인용한 결정·배제 가설·열린 질문 JSON만 받을 수 있고 assistant claim으로 주입됩니다. length/timeout/cancel/invalid JSON/unknown refs는 저장하지 않으며 이전 note와 deterministic facts를 사용합니다. 이 호출의 prompt/output/time은 `semanticCost`에, 일반·semantic 모델 호출의 누적 횟수/prompt/predicted tokens와 모델 대기·전체 실행 경과 시간은 `executionCost`에 포함되고 BOUNDED 중에는 semantic handoff를 실행하지 않습니다. 전체 설계·rollback·검증 gate는 [ADR-hybrid-context.md](docs/ADR-hybrid-context.md)에 있습니다.

archive projection이 다음 압축에서 모델 입력 밖으로 나가더라도 체크포인트의 bounded `historicalEvidence` 색인이 evidence ID/version, source identity와 범위를 유지합니다. 색인은 원문을 복제하지 않으며 다음 턴에서 정확한 ID와 범위로 재조회하기 위한 근거만 제공합니다. image 등 지원하지 않는 typed content가 있으면 durable window commit만 `unsupported_typed_content`로 건너뛰고 해당 prediction과 원래 typed history는 그대로 진행합니다. `Observe only`는 projection, archive 치환, window restore/commit, semantic handoff와 target-triggered compaction을 모두 우회합니다.

Deterministic/Hybrid 조사 중 한 prediction의 최종 assistant 출력이 raw `<tool_call>` 문자열만 남기고 structured request·dispatch·result를 하나도 만들지 못했을 때, 이름이 확인된 모든 도구가 read-only이고 취소·timeout·명시적 pause·BOUNDED·예산 소진 상태가 아니면 fresh tool-planning round를 최대 한 번 허용합니다. 스트림 fragment를 가로질러 raw marker를 계측하되 최종 출력이 정상 코드 예제·인용인 경우에는 retry 조건으로 확정하지 않습니다. 일반 모드 retry는 audit용 100초 deadline을 숨은 gate로 사용하지 않으며 BOUNDED의 명시적 시간 제한은 그대로 유지합니다. 잘린 XML/JSON 인수를 파싱하거나 실행하지 않고, 원래 사용자 목표와 이미 확보한 근거로 새 structured request를 생성하게 합니다. retry에는 해당 read-only 도구만 노출하고, provider call ID의 전역 중복이 아니라 같은 causal request/result 묶음에서 성공이 확인된 동일 read만 재실행하지 않습니다. 오류 결과, 과거 archive index와 정당한 archive 재조회는 성공한 현재 read로 오인하지 않습니다. write/build/mutation, 불명확한 도구명, 재귀 retry는 대상이 아닙니다. 대형 다중 파일 조사는 고정된 작은 호출 수로 자르지 않고 합리적인 batch를 실행한 뒤 결과를 소비하고 다음 batch를 요청하도록 모델 지침을 제공합니다.

원래 system 지침은 체크포인트와 내부적으로 구분해 보존합니다. 압축을 반복해도 Qwen 계열 입력에는 하나의 선행 system 메시지만 만들며, 원래 지침과 최신 체크포인트가 각각 한 번만 들어갑니다. 사용자·도구 본문에 체크포인트 표식이 문자 그대로 있어도 생성 체크포인트로 분류하지 않습니다.

파일 관찰은 최근 도구 결과 표시 제한을 적용하기 전에 먼저 집계합니다. 같은 파일·같은 해시의 읽기 범위는 합치고, 해시·프로젝트·clone/worktree가 다르면 섞지 않습니다. 줄 범위 응답과 Unreal의 UTF-8 byte-window 응답은 서로 다른 단위로 검증하며, 보고된 범위만 있고 본문이 없는 결과는 같은 key의 정상 원문과 합쳐져도 검증된 원문 범위를 넓히지 않습니다. 현재 체크포인트는 최대 64개의 파일 관찰을 유지하며, 문자 예산 때문에 실제 직렬화에서 빠진 항목 수는 디버그 omission 메타데이터에 명시합니다. 검색 결과에는 실제 요청의 query, literal/regex 모드, root, 확장자/종류 필터, 결과 수, 스캔 수와 partial/complete/unknown 상태를 남깁니다. 불완전한 0건은 저장소 전체 부재로 해석하지 않습니다.

## 첨부 문서 추가 읽기

PDF·Word·텍스트 첨부가 있으면 모델에 파일 이름, 첨부 ID, 형식, 크기만 담은 작은 manifest와 `read_attached_document` 도구를 제공합니다. 모델이 기존 발췌만으로 부족할 때 시작 문자 위치와 최대 길이를 지정해 추가 범위를 읽습니다. 한 번에 최대 12,000자만 모델 문맥에 들어가며, 호출 횟수 제한은 두지 않습니다.

도구 결과는 파일 ID·이름·파서·시작/끝 위치·전체 문자 수를 함께 반환합니다. 압축 메모에는 이 출처와 범위만 남기고 문서 본문은 복제하지 않습니다. 첨부가 있다는 사실만으로 문서 전체를 읽었다고 취급하지 않으며, 채팅 첨부를 Unity나 Unreal 프로젝트에서 검색하지 말라는 안내도 함께 주입합니다. 문서 파싱은 LM Studio의 파일 API가 수행하며 별도 모델은 사용하지 않습니다.

`Separate document input`은 기본적으로 꺼져 있습니다. 서명된 첨부 경계·범위 읽기·변경된 이력 거부는 테스트했지만, LM Studio 문서 RAG가 모든 채팅에서 이 전처리기보다 뒤에 실행된다는 런타임 순서와 실제 retrieval 재현율은 이 저장소만으로 증명할 수 없습니다. 따라서 일반 기본값으로 켜지 말고, 대상 LM Studio 버전에서 전처리 순서와 대표 문서 질의의 인용/누락률을 별도로 확인한 채팅에서만 실험적으로 켭니다.

## 압축 후 보존 정보와 파일 상태 처리

현재 요청·목표, 이어서 하는 작업의 대상, 활성 프로젝트, 미해결 항목, 최근 파일·도구·빌드 결과를 정해진 크기로 남깁니다. 토큰 수 측정이 안 되면 메시지 수를 기준으로 판단합니다.

파일 관찰은 실제 프로젝트와 경로·해시·시간을 기록하되 `mutationSnapshotState: fresh_read_required`로 표시합니다. 오래된 기억만으로 수정하지 말고 다시 읽어야 한다는 뜻입니다. `fileVersionReceipt` 같은 실행 중 임시 표식은 오래 보관할 기억에서 제외합니다.

사용자가 작성한 결제 영수증이나 `ReceiptActor`, `FPaymentReceipt` 같은 코드 이름까지 지우지는 않습니다. 파일 수정 표식을 재사용하라는 실행 지시와 일반 단어를 구분합니다.

모델은 결론이 달라졌을 때만 짧은 `continuity-note` 꼬리말을 생성할 수 있습니다. 플러그인은 정해진 JSON 형식만 읽고 꼬리말을 사용자에게 보이는 답변에서 제거합니다. 결정·배제한 가설·열린 질문을 합쳐 최대 4개, 범위 정보를 포함해 최대 1,500자로 제한합니다. 각 판단에는 안정 ID와 `open` / `resolved` / `superseded` 상태가 붙으며, 상태 변경은 모델이 기존 ID를 명시적으로 갱신할 때만 반영합니다. 일반 답변의 “다음에 확인” 같은 문장은 영구 미해결 상태로 승격하지 않습니다. 다음 모델 입력에는 이전 모델의 판단이라는 표시와 함께 `assistant` 역할로 전달하며, 파일 관찰을 담은 `system` 체크포인트에는 합치지 않습니다. Legacy/Deterministic는 이 note를 위해 추가 모델을 호출하지 않습니다. Hybrid만 위의 제한된 semantic handoff 호출을 사용합니다.

기존 압축 기록에 있던 `lastAssistantUpdate`, assistant 진행 항목, assistant 대화 꼬리도 system 체크포인트에서 분리해 별도 assistant 기록으로 전달합니다. 이전 형식의 체크포인트는 읽되 새로 만들 때 역할을 분리합니다. 체크포인트 예산이 작으면 도구·파일 관찰을 우선하고 assistant 기록은 생략할 수 있습니다.

메모의 프로젝트 식별자는 도구 결과에서 한 프로젝트만 명확히 확인될 때 harness가 붙입니다. 여러 프로젝트가 섞이거나 확인되지 않으면 단일 프로젝트로 단정하지 않습니다. 프로젝트가 지정된 저장 메모는 이후 이력에서 같은 프로젝트를 확인할 수 없으면 복원하지 않습니다. `refs`는 대화 이력에 고유한 요청과 완료 결과가 함께 있는 `tool-call:<id>`만 남기며, 확인되지 않거나 중복된 ID는 제거합니다. 참조가 제거돼도 메모 본문은 검증되지 않은 과거 모델 판단으로만 남습니다.

메모는 `~/.lmstudio/unreal-context-compactor/notes-v1`에 작은 로컬 JSON 파일로 저장합니다. 이전에 사용자에게 표시된 대화 이력과 작업 디렉터리가 정확히 이어질 때만 복원합니다. 이력이 편집되거나 다른 목표로 바뀌면 이전 메모를 적용하지 않습니다. 작업 디렉터리를 확인할 수 없거나 대화에 첨부 파일이 있으면 대화 간 메모 저장을 사용하지 않습니다. `Observe only`에서는 메모를 생성하거나 저장하지 않습니다. SDK에서 대화 ID를 받지 못하므로, 같은 작업 디렉터리에서 가시 이력이 완전히 동일한 두 대화는 메모 키를 공유할 수 있습니다.

## 반복 감지와 최종 입력 예산

### 도구 인수 JSON 오류

도구 인수 JSON 자체가 잘린 경우에는 SDK의 `handleInvalidToolRequest` 경계에서 파싱 실패를 기록하고 생성 종료 통계와 이미 진행 중인 다른 호출의 결과를 수집한 뒤 중단합니다. 기존 결과 전달기가 구조화된 반환 관찰과 미완료 범위를 짧은 부분 보고로 남깁니다. 잘린 인수·미완료 assistant 계획은 근거로 사용하지 않고 전체 작업 완료로 표시하지 않습니다. 종료 사유가 `maxPredictedTokensReached`일 때만 출력 한도 소진으로 표시하며, 일반 JSON 오류를 출력 한도 문제로 추정하지 않습니다. 잘린 JSON을 자동 완성하거나 삭제·수정 요청을 자동 재실행하지 않습니다. 스키마 검증에 실패한 완전한 요청의 기존 SDK 오류 피드백은 유지합니다. 사용자 취소와 통신 오류는 파싱 오류보다 우선하며 Observe only에서는 SDK 기본 동작을 유지합니다.

scope·읽기 프로필·중복·공유 예산으로 거부한 요청은 생성 상태를 즉시 종료합니다. 라운드 종료 시 남은 생성·실행 상태도 닫으며, 결과가 없는 호출은 성공으로 표시하지 않습니다. 이 종료 표시의 소유자는 기존 도구 상태 추적기입니다.

### 생각 단계의 출력 한도 복구

`Reasoning-limit recovery`는 일반 실행에서 모델이 생각만 하다가 생성 상한에 도달하고 도구 요청·인수·승인·결과가 전혀 없을 때, 확인된 근거와 사용자 조건을 유지하여 다음 작은 작업에 집중하는 생성을 한 번 더 허용합니다. 도구 사용을 강제하지 않으며 필요한 확인 질문도 허용합니다. 별도 설정의 기본값은 `On`이며, 기존 저장 설정에 이 항목이 없어도 `On`으로 해석합니다. `Off`를 선택하면 추가 생성 없이 중단합니다. `Output-limit recovery`의 도구 없는 최종 답변 재작성 설정과는 독립적입니다.

Legacy/Deterministic/Hybrid의 일반 `RESEARCH`에서만 진입하며, Observe only·bounded audit·읽기 복구·도구 재계획·최종 보고 중에는 새 복구를 시작하지 않습니다. 기존 도구 범위, 승인 검사와 `Output reserve`를 재사용합니다. 출력 한도를 자동으로 늘리거나 최종 답변 재작성의 시간 제한을 도구 실행에 적용하지 않습니다. 재시도 입력은 기존 입력 준비 경로에서 다시 측정하고 선택적 semantic handoff 호출은 생략합니다. 입력이 맞지 않거나 재시도가 실패하면 다른 복구로 연쇄 전환하지 않습니다.

횟수는 실행 상태가 소유하며 도구 성공이나 컴팩션으로 초기화하지 않습니다. 도구 결과를 받으면 정상 진행으로 돌아가되 같은 사용자 요청에서는 생각 단계 복구를 다시 사용하지 않습니다. 잘린 생각은 GUI에만 남고 확정 이력에는 반영하지 않으며 이전 도구 결과는 보존합니다. 실행 여부가 불명확한 도구를 재실행하는 기능은 아닙니다. `reasoning_recovery_scheduled`, `reasoning_recovery_blocked`, `callPurpose=reasoning_recovery`로 복구 여부를 확인할 수 있습니다.

### 복구의 책임과 수명

| 담당 | 소유하는 판단·상태 | 경계 |
| --- | --- | --- |
| `ExecutionState` | 실행 단계와 생각 복구 횟수 | 핸들러 실행마다 생성하며 중복 진입·횟수 초과·종료 후 진입을 차단 |
| `decideReasoningRecovery` | 복구 허용 여부와 사유 | 상태 변경·모델 호출·도구 실행 없이 관찰된 결과만 판단 |
| `prepareRoundInput` / `ContextManager` / `BudgetBroker` | 실제 입력 구성·측정·공유 결과 예산 | 기존 경로를 재사용하며 복구용 별도 예산 계산기를 만들지 않음 |
| `runOneToolRound` | SDK 콜백, 생성 통계, 해당 라운드의 실패 | JSON 오류를 자동 보정하지 않으며 부모 취소 연결을 종료 시 해제 |
| `RoundTransaction` / `EvidenceManager` | 반환된 근거와 완료된 계획의 반영 | 실패한 생각은 확정하지 않고 반환된 도구 결과는 보존 |
| 기존 tool guard / SDK | 프로젝트 범위·승인·실제 도구 실행 | 복구에서도 동일 경로를 통과하며 승인만으로 실행 성공을 주장하지 않음 |

입력 ID는 오케스트레이터가 한 번 정하고 근거 보관·측정·노출·호출 관찰에 전달합니다. 읽기 복구 에피소드 횟수, 실행 전체의 재시도 횟수, 최종 보고 횟수는 서로 다른 수명을 유지합니다. 도구 세션은 실행 전체에서 하나를 소유하며 정상 종료·실패·취소 시 해제하고, 종료 처리에서 추가 모델 호출을 만들지 않습니다.

### 반복 감지와 예산 검사

`Repeated tool rounds`와 `Within-generation repetition`은 서로 독립적입니다. 기본값은 둘 다 `Warn`이고, `Pause` 또는 `Off`를 선택할 수 있습니다. 도구 라운드는 기본 3회, 생성 텍스트는 80자 이상의 동일 블록이 기본 3회 연속될 때 감지합니다. 페이지 cursor가 전진하거나 파일 해시·결과 의미가 달라지면 도구 반복 횟수를 초기화합니다. `Observe only`에서는 두 감지기가 경고·중지 동작을 하지 않습니다.

최종 모델 입력은 system/assistant 주입, 노출된 도구 스키마, 출력 reserve와 safety margin까지 합친 뒤 다시 측정합니다. 정확한 양수 적합성은 `true`, 양수 추정치는 `unknown`, 음수는 `false`로 보고합니다. 일반 조사 입력이 최소 보존 후보에서도 음수면 도구 정의를 제거한 최종 보고 입력으로 한 번 전환합니다. 이 컨텍스트 예산 구조 호출은 일반 호출의 출력 reserve를 유지하고 감사 모드의 별도 최종 시간 제한을 적용하지 않습니다. reserve가 모두 들어가지 않아도 safety margin 뒤에 256토큰 이상 남으면 정확한 가용량으로 낮춥니다. 최종 보고 입력조차 최소 출력 공간을 확보하지 못할 때만 `CONTEXT_BUDGET_EXCEEDED`로 모델 호출 전에 실패합니다. 최종 보고 시도는 최대 한 번이며 사용자 취소 뒤에는 새 호출을 만들지 않습니다. 선택형 bounded audit의 최종 출력 상한과 시간 제한은 그대로 해당 모드에만 적용됩니다. `Observe only`는 측정만 하고 기존 입력과 중지 동작을 바꾸지 않습니다.

## 플러그인 설치와 개발 검증

설치는 저장소 맨 위의 `INSTALL.bat` 또는 `install.sh`로 진행합니다. Windows에서 기존 Unity MCP와 이 플러그인을 함께 갱신할 때는 `UPDATE.bat`를 실행합니다. 플러그인만 복구할 때는 사용자 지정 구성의 `context_compactor`를 선택할 수 있습니다.

이 폴더에서 실행합니다.

```text
npm run status
npm ci
npm test
npm run eval:availability-ab -- --model <loaded-model-id> --pilot-pairs 1 --pairs 5 --output <report.json>
npm run eval:audit-pressure -- --model <loaded-model-id> --main-pairs 5 --contract-pairs 1 --output <report.json>
npm run eval:hybrid-context -- --model <loaded-model-id> --pairs 5 --output <report.json>
npm run dev
```

`npm run status`는 소스와 빌드 연결을 검사합니다. 실제 채팅 활성화를 증명하지는 않습니다. `npm run test:active`는 현재 연결 방식으로 지속적인 활성화 증거를 얻을 수 없어 `UNPROVEN`과 비영(현재 1) 종료 코드를 반환합니다.

`eval:availability-ab`는 설치 상태를 바꾸지 않고 현재 저장소의 빌드와 이미 로드된 모델을 사용합니다. A는 비주입 계측, B는 검증된 현재 입력 메타데이터 주입입니다. 합성 두 줄 fixture의 실제 도구 호출·중첩 반환량·메타데이터 비용·최종 답변을 모두 기록합니다. 원래 사용자 세션의 raw 실행 기록을 재구성하거나 모든 모델의 행동 개선을 증명하지는 않습니다.

`eval:audit-pressure`는 8개 파일 fixture에서 실제 압축 뒤 과거 원문 퇴출과 현재 원문 잔존을 독립 oracle로 먼저 확인합니다. 파일럿이 이 조건을 충족할 때만 A/B 본 반복을 실행합니다. A/B는 `Observe`와 `Inject`의 메타데이터 효과만 비교하고, A/C는 사용자가 선택한 `eval/AUDIT_TASK_CONTRACT.md`의 감사 지시 효과를 별도의 탐색 실험으로 비교합니다. 계약은 제품 하네스가 자동 주입하지 않으며, 실패·timeout·압축 미발생 실행도 보고서에서 삭제하지 않습니다. 재조회 반환량은 요청을 만든 `modelInputId`의 실제 입력 원문과 과거 원문 ledger를 각각 대조해 현재 입력 중첩·과거 근거 재획득·새 근거로 나눕니다.

설치기는 잠금 파일대로 패키지를 준비하고 검사·빌드 후 `lms dev --install -y`로 등록합니다. 이름·소유자·revision과 `.lmstudio/production.js`가 있는지 확인합니다. 현재 버전은 0.4.73 / revision 120입니다.

도구 승인 시 LM Studio의 저장된 `Allow all` 또는 개별 도구 허용 설정을 매번 확인합니다. SDK 1.5.0의 직접 승인 요청은 이 설정을 적용하지 않으므로, 컴팩터가 `LMSTUDIO_HOME/settings.json`(기본값 `~/.lmstudio/settings.json`)의 `chat` 승인 설정을 읽습니다. 설정을 읽을 수 없거나 허용 항목이 없으면 같은 `callId`의 기본 도구 카드에 승인·거절 버튼을 표시합니다. 승인 뒤 인수 편집, 거절, 취소와 결과 반환을 카드에 반영하며, 이 설정은 프로젝트 범위나 MCP 제공자의 쓰기·실행 권한을 변경하지 않습니다.

SDK 1.5.0의 정상 예측 종료 후 cancel 전송 문제는 버전 검증 패치로 보정합니다. 통합 설치기와 `UPDATE.bat`은 LM Studio 설치 대상의 의존성에도 이 패치를 적용합니다. `lms dev --install -y`만 직접 실행한 경우에는 설치 대상 플러그인 디렉터리에서 `npm run patch:sdk`를 실행한 뒤 플러그인을 Force Restart해야 합니다. SDK 버전이나 패치 대상이 달라지면 설치를 실패시켜 재검토하도록 합니다.
