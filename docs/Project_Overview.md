# Project Overview

This repository provides local Unreal Engine evidence, project I/O, diagnostics,
and bounded mutations to the model selected in LM Studio. It is designed for
multiple Unreal versions and multiple projects; no project name, engine version,
or installation path is a product constant.

## Current runtime boundary

The default runtime is **Direct Model Mode**. The selected model owns
interpretation, tool selection, call order, retries, stopping, and the final
answer. The MCP servers expose stable capabilities and factual results. They do
not create a task, plan, route, required-next-tool obligation, or synthesis gate.

- `unreal-rag` runs `scripts/unreal_rag_direct.py` and exposes exactly eight
  factual project/RAG capabilities: active-project get/set, search, symbol
  lookup, health, rebuild status, refresh, and capability discovery.
- `unreal-agent` runs `lmstudio-unreal-agent-mcp/src/direct-server.js` and
  exposes a stable project/read/mutation/diagnostic catalog. An exact project
  selector can be supplied per call without changing the shared active project.
- `strict-server.js` is a separate, explicit Node entry for callers that need a
  small conversation lifecycle. It does not restore the historical Python
  planner/controller and never makes another conversation's state a prerequisite
  for ordinary reads.
- `codex/unreal-context-compactor` is an LM Studio chat plugin. It compacts older
  model-facing history under token pressure while calling the actual model
  selected by the user. Its bounded factual continuity can retain the active
  objective, continuation antecedent, active project/current work, unresolved
  items, and relevant file/tool/build facts. Compacted file observations retain
  canonical project/path identity and the observed SHA, require a fresh read before
  mutation, and never retain runtime-local file-version receipts. It is not a model, proxy model,
  tool router, planner, or authority source. It is installed for availability without
  activating the host-owned chat toggle, which must be verified OFF per chat by
  default. Enabling that single toggle invokes the compaction handler.

## Safety that remains authoritative

Direct mode removes workflow authority, not containment or concurrency safety.
The runtime retains:

- exact selected-project containment and normalized path resolution;
- source/config/plugin-source mutation allowlists and protected-directory denial;
- bounded reads, responses, process output, and mutation size/file limits;
- explicit receipt-first scoped snapshot/CAS for every existing-file mutation,
  with compatible valid raw `expectedHash` and no automatic session fallback;
- create-only new-file writes, per-path locks, atomic replacement, and recoverable
  multi-file transaction journals;
- explicit source-delete proposal, proposal receipt/hash confirmation,
  environment opt-in, and LM Studio's own tool confirmation;
- command allowlists, one shared bounded Build/Automation process owner,
  process-tree timeout termination, bounded head/tail log projection, and
  explicit build/Automation authority flags.

Static and semantic code findings are advisory. They may inform the model but do
not approve or block a permitted edit or build. Real UBT/UHT/compiler output is
the authoritative build diagnostic.

## Multi-project and multi-version behavior

Pass an exact `.uproject` path when ambiguity is possible. Project discovery and
the shared active-project controller support multiple roots, while per-call
selectors allow one Direct process to work across projects without silently
retargeting later calls. Engine resolution uses each project's
`EngineAssociation`, registered installations, or an explicit engine root and
verifies the resolved engine version before a build. RAG indexes are
engine-bound sibling shards: an exact project selector chooses the compatible
shard, and one call never merges projects owned by different engines. For a
build, `target=Editor` resolves the selected project's canonical, configured
preferred, or sole discovered custom Editor target; explicit non-Editor targets
are unchanged.

## LM Studio setup

1. Load and select the real instruction/tool-calling model in LM Studio.
2. Leave the top-level `codex/unreal-context-compactor` switch OFF in the chat's
   plugin panel by default. Existing chats can retain an old opt-in, so turn it off
   manually per chat. Enable this single switch only when a long chat needs compaction.
3. Start Local Server and enable the `unreal-rag` and `unreal-agent` MCP entries.
4. Keep SAFE/read-only authority unless writes, commands, or builds are actually
   required. Enable AGENT authority explicitly when they are.
5. Select or pass the exact target `.uproject`; refresh `project_source` when the
   index reports that project source is newer than indexed evidence.

Installing or pinning the compactor proves source availability, not that a
particular chat's plugin toggle is OFF. Confirm the OFF state in the LM Studio UI
for every new or existing chat. Editor metadata refresh starts Unreal Editor only when the caller explicitly
sets `allowEditorLaunch=true` for the relevant refresh scope.

See [Architecture](ARCHITECTURE.md),
[LM Studio setup](LMStudio_Unreal_Agent_Setup.md),
[tool discipline](LMStudio_MCP_Tool_Discipline.md),
[RAG setup](RAG_Setup.md), and [SAFE/AGENT authority](Safe_Agent_Mode.md).

## 현재 구조 요약

기본 경로는 **Direct Model Mode**입니다. 선택한 모델이 해석·도구 선택·호출
순서·재시도·종료·최종 답변을 소유하고, MCP는 여러 UE 버전과 여러 프로젝트에
사용할 수 있는 제한된 capability만 제공합니다. 기본 경로에는 task, planner,
route, required-next-tool, synthesis gate가 없습니다.

`unreal-rag`는 8개의 사실 조회/갱신 도구만 제공하고, `unreal-agent`는 프로젝트
탐색·읽기·receipt/CAS 편집·빌드·테스트·로그 기능을 제공합니다. 필요한 경우 매
호출에 정확한 `.uproject`를 넘길 수 있으므로 active project를 몰래 바꾸지 않고
여러 프로젝트를 다룰 수 있습니다. 정확한 프로젝트 선택자는 해당 엔진에 묶인
sibling RAG shard를 선택하며 한 호출에서 서로 다른 엔진 shard를 합치지 않습니다.
Strict는 별도 Node 진입점이며 다른 대화의 상태로 일반 읽기를 막지 않습니다.

context compactor는 active objective, continuation antecedent, 현재 프로젝트/작업,
미해결 항목, 관련 파일·도구·빌드 사실을 제한된 continuity state로 보존할 수
있습니다. 압축된 파일 관찰은 canonical 프로젝트/경로와 관찰 SHA만 보존하고
수정 전 fresh read가 필요한 상태가 되며, runtime-local file receipt는 보존하지
않습니다. plan, route, 권한, 다음 도구, 완료 판단도 소유하지 않습니다. 기본
운용에서는 단일 상단 채팅 플러그인 토글을 OFF로 유지하며, 기존 채팅의 활성화
상태는 채팅별로 직접 끕니다. 긴 채팅에 압축이 필요할 때만 그 토글을 켭니다.

Direct가 제거한 것은 서버의 작업 판단권입니다. 프로젝트 경로 제한, 보호 폴더
차단, 크기 제한, `fileVersionReceipt` 우선 snapshot/CAS(raw `expectedHash` 호환),
원자적 쓰기/rollback, 삭제 확인, 명령 allowlist, 공유 bounded Build/Automation
process owner는 계속 hard gate입니다. `target=Editor`는 선택 프로젝트의 실제
Editor target으로 해석되고 명시한 non-Editor target은 유지됩니다. 정적·semantic
finding은 advisory이고 실제 UBT/UHT/compiler 결과가 빌드 판단의 근거입니다.
