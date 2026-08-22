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
  selected by the user. It is not a model, proxy model, tool router, or authority
  source.

## Safety that remains authoritative

Direct mode removes workflow authority, not containment or concurrency safety.
The runtime retains:

- exact selected-project containment and normalized path resolution;
- source/config/plugin-source mutation allowlists and protected-directory denial;
- bounded reads, responses, process output, and mutation size/file limits;
- read-before-write SHA-256 compare-and-swap for existing files;
- create-only new-file writes, per-path locks, atomic replacement, and recoverable
  multi-file transaction journals;
- explicit source-delete proposal, current-hash confirmation, environment opt-in,
  and LM Studio's own tool confirmation;
- command allowlists, process-tree timeout/output termination, and explicit
  build/Automation authority flags.

Static and semantic code findings are advisory. They may inform the model but do
not approve or block a permitted edit or build. Real UBT/UHT/compiler output is
the authoritative build diagnostic.

## Multi-project and multi-version behavior

Pass an exact `.uproject` path when ambiguity is possible. Project discovery and
the shared active-project controller support multiple roots, while per-call
selectors allow one Direct process to work across projects without silently
retargeting later calls. Engine resolution uses each project's
`EngineAssociation`, registered installations, or an explicit engine root and
verifies the resolved engine version before a build.

## LM Studio setup

1. Load and select the real instruction/tool-calling model in LM Studio.
2. Enable `codex/unreal-context-compactor` in the chat's plugin panel; keep the
   real model selected in the model dropdown.
3. Start Local Server and enable the `unreal-rag` and `unreal-agent` MCP entries.
4. Keep SAFE/read-only authority unless writes, commands, or builds are actually
   required. Enable AGENT authority explicitly when they are.
5. Select or pass the exact target `.uproject`; refresh `project_source` when the
   index reports that project source is newer than indexed evidence.

Installing or pinning the compactor proves source availability, not that a
particular chat's plugin toggle is enabled. Confirm that toggle in the LM Studio
UI. Editor metadata refresh starts Unreal Editor only when the caller explicitly
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
탐색·읽기·CAS 편집·빌드·테스트·로그 기능을 제공합니다. 필요한 경우 매 호출에
정확한 `.uproject`를 넘길 수 있으므로 active project를 몰래 바꾸지 않고 여러
프로젝트를 다룰 수 있습니다. Strict는 별도 Node 진입점이며 다른 대화의 상태로
일반 읽기를 막지 않습니다.

Direct가 제거한 것은 서버의 작업 판단권입니다. 프로젝트 경로 제한, 보호 폴더
차단, 크기 제한, SHA-256 CAS, 원자적 쓰기/rollback, 삭제 확인, 명령 allowlist,
프로세스 제한은 계속 hard gate입니다. 정적·semantic finding은 advisory이고 실제
UBT/UHT/compiler 결과가 빌드 판단의 근거입니다.
