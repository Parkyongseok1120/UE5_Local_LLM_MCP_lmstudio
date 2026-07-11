# Release Readiness — UX Journey Notes

Friction reduction without new features (Phase 5).

## Improvements in this release

| Journey | Change |
|---------|--------|
| First install | Post-install summary: index path, safe/agent mode, restart LM Studio, verify command |
| Distribution choice | README clarifies OSS BYOI vs Portable ZIP prebuilt index |
| Error recovery | `BUILD_PLAN_RESOLUTION_FAILED` → `unreal_set_active_project` hint |
| Project status | `unreal_project_status` adds `blockingReasons`, RAG index presence/mtime |
| Blocked tools | `TOOL_NOT_CALLABLE` includes `userMessage` + `agentInstruction` |

## Remaining friction (acceptable for RC)

| Journey | Friction | Mitigation |
|---------|----------|------------|
| First question | Bootstrap prompt paste | Single workflow doc: `docs/Project_Overview.md` |
| Two MCP servers | User must understand RAG vs agent roles | Install summary lists both |
| Safe vs agent mode | Separate installer / Enable-AgentMode | Install output states current mode |
| Long session handoff | Manual `.agent/handoff` | Existing `formatSessionHandoff` tool |

## Metrics (qualitative targets for stable)

- First successful project switch: ≤2 tool calls after install
- Build failure recovery: error message alone sufficient in ≥80% of cases (envelope + nextSteps)
- Install without reading secondary docs: post-install summary sufficient for next 3 steps
