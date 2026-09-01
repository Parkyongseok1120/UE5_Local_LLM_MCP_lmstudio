# 1.3.3 — Scoped result identity and bounded Direct evidence

[English](#english) | [한국어](#korean)

## Component versions

| Component | Version |
|---|---|
| Product | 1.3.3 (`v1.3.3`) |
| Portable manifest | 2.1.17 |
| Node agent MCP | 0.3.22 |
| Evidence-First MCP server | 1.1.1 |
| Context compactor | 0.4.51 / revision 98 |

## English

1.3.3 hardens Direct Model Mode evidence delivery and project identity. It makes file-search results reusable without path guessing, keeps RAG evidence and metadata inside the serialized transport budget, and clarifies the Evidence-First helper's advisory role. Existing file-version receipt, CAS, atomic-write, bounded process, and context-continuity authority boundaries are unchanged.

### Highlights

- `search_files` retains each search-root-relative `path` and adds a directly reusable scoped `uri`. Project results also return the exact active-project identity that must accompany a later `project://` call, keeping same-name clones isolated.
- Direct RAG reserves space for the complete serialized response envelope before allocating evidence. Mixed project/engine retrieval respects the effective `top_k`, and match-reference metadata is independently bounded.
- Search and symbol responses report evidence or match-metadata trimming and may return `nextDetailLevel`. The generic retryable `OUTPUT_LIMIT_EXCEEDED` guard remains for pathological producer output that still cannot fit.
- Query, project-selector, and match-metadata inputs that cannot fit the selected transport budget fail with a retryable argument error rather than creating malformed or partial evidence.
- `scope=auto` continues to classify API-looking queries as engine evidence when appropriate. Callers that require current-project source can pair an exact project selector with `scope=project`.
- `evidence_first_contract` is an optional exact-schema lookup when obligations are absent or uncertain. It is not a routine preflight, permission check, or RAG/read/write/build sequencer. Validation remains required before causal P0/P1 findings or multi-file implementation plans.
- Agent mutation descriptions now surface the existing `ALLOW_WRITE=1` requirement, and workspace status remains a useful advisory authority check without becoming a mandatory tool order.
- Windows stale-lock recovery now allows the process-start identity probe up to the same bounded 10-second subprocess budget used by reclaim coordination. A probe that still cannot inspect a live process continues to fail safe and never steals its lock.

### Validation boundary

Release publication is gated by the complete Node MCP and Python Direct/release suites, syntax and encoding checks, deterministic repetition tests, clean portable-package verification, and cross-platform GitHub CI. These automated gates exercise scoped URI identity, same-name project isolation, response budgeting, Evidence-First contract behavior, package inventory, and installer metadata.

The v1.3.2 Qwen 3.8 27B live workflow remains the latest operator-provided qualitative model evidence. No new live-model benchmark or universal host, Unreal project, engine, plugin, or editor-runtime certification is claimed for v1.3.3.

The GitHub release assets include the clean portable ZIP and its SHA-256 digest. Verify the downloaded ZIP against the published digest.

## Korean

1.3.3은 Direct Model Mode의 evidence 전달과 프로젝트 identity를 강화합니다. File search 결과를 path 추측 없이 재사용할 수 있게 하고, RAG evidence와 metadata를 serialized transport budget 안에 유지하며, Evidence-First helper의 advisory 역할을 명확히 했습니다. 기존 file-version receipt, CAS, atomic-write, bounded process, context-continuity 권한 경계는 변경하지 않았습니다.

### 핵심 변경

- `search_files`는 각 search-root-relative `path`를 유지하면서 바로 재사용할 수 있는 scoped `uri`를 추가합니다. Project 결과에는 다음 `project://` 호출과 함께 전달할 정확한 active-project identity도 포함되어 같은 이름의 clone을 분리합니다.
- Direct RAG는 evidence를 할당하기 전에 완전한 serialized response envelope 공간을 예약합니다. Mixed project/engine 검색은 effective `top_k`를 지키고 match-reference metadata도 별도 한도를 적용합니다.
- Search와 symbol 응답은 evidence 또는 match metadata가 잘렸는지 알리고 필요하면 `nextDetailLevel`을 반환합니다. 그래도 담을 수 없는 비정상적으로 큰 producer 출력에는 기존 retryable `OUTPUT_LIMIT_EXCEEDED` 방어선을 유지합니다.
- 선택한 transport budget에 담을 수 없는 query, project selector, match metadata 입력은 malformed 또는 partial evidence를 만들지 않고 retryable argument error로 실패합니다.
- `scope=auto`는 API 형태의 query를 필요에 따라 engine evidence로 계속 분류합니다. 현재 project source가 필요한 호출자는 정확한 project selector와 `scope=project`를 함께 사용할 수 있습니다.
- `evidence_first_contract`는 obligation이 없거나 불확실할 때 쓰는 선택적 exact-schema 조회입니다. Routine preflight, 권한 확인, RAG/read/write/build 순서 결정 주체가 아닙니다. Causal P0/P1 finding이나 multi-file implementation plan을 제시하기 전 validation 의무는 유지됩니다.
- Agent mutation 설명에 기존 `ALLOW_WRITE=1` 요구사항을 명시하고, workspace status는 유용한 advisory authority check로 유지하되 필수 tool order로 만들지 않습니다.
- Windows stale-lock 복구의 process-start identity probe는 reclaim coordination과 같은 10초 bounded subprocess budget을 사용합니다. 그래도 live process를 확인하지 못하면 기존 fail-safe 정책대로 lock을 회수하지 않습니다.

### 검증 경계

릴리스 게시는 Node MCP 및 Python Direct/release 전체 suite, syntax와 encoding 검사, deterministic repetition test, clean portable package 검증, cross-platform GitHub CI 통과를 요구합니다. 이 자동 gate는 scoped URI identity, 같은 이름 프로젝트 격리, response budget, Evidence-First contract 동작, package inventory, installer metadata를 검사합니다.

v1.3.2 Qwen 3.8 27B 라이브 workflow가 현재 최신 운영자 제공 정성 모델 근거입니다. v1.3.3에 대해 새로운 live-model benchmark나 모든 host, Unreal project, engine, plugin, editor-runtime 조합의 보편적 인증을 주장하지 않습니다.

GitHub Release asset에는 clean portable ZIP과 SHA-256 digest를 함께 게시합니다. 다운로드한 ZIP은 공개된 digest와 대조하세요.
