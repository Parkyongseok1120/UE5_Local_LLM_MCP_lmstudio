# Mid-campaign MCP / Stage 3–7 audit (runtime verified)

Session `821b0f` · Stage 7 paused · **Do not advance to Stage 8**.

## Hypotheses (runtime)

| ID | Claim | Result | Evidence |
|----|--------|--------|----------|
| H1 | Shared `~/.lmstudio/state/unreal-agent` blocked by stale `running` tasks | **CONFIRMED → FIXED** | 61 running tasks incl. Stage2 `43baa300ded2456a`; after quarantine `remaining_running=0`; shared smoke write/patch/read OK (`mcp_shared_state_smoke_report.json`) |
| H2 | Isolated smoke false-failed on nested MCP JSON escaping | **CONFIRMED → FIXED** | Pre-fix writeOk/patchOk false while snippet had `"ok": true`; post-parse-fix all true (`mcp_midpoint_smoke_report.json`) |
| H3 | Stage 3 never had MCP mutations / Automation | **CONFIRMED** | No `local_ai_stage3_*.out.log`; no `GomokuStage3*.spec.cpp` |
| H4 | Stage 6 Automation regressed after Stage 7 item registry | **CONFIRMED** | `omock_automation_Stage6_postStage7_check.report.json`: Fail `ValidateTarget on empty cell` with ItemId 101 |
| H5 | History trim dropped user / broke tool pairing | **REJECTED** | Aggregate `hasUserFalseΣ=0` across Stages 4–7; trim `user_preserving_tail` |

## MCP execution path

### Supervisor campaign (isolated `AGENT_STATE_ROOT`)
- **Healthy** for Stages 4–7 turns that ran.
- `activeProject` always `...\O-Mock\O_Mock.uproject`.
- Mutations only under `Source/O_Mock/...` (`outsideOMock: []` all sessions).
- Bounded-patch / evidence-stagnation strings: **0**.
- LM 400 / `maxPredictedTokensReached`: Stage4 `tests_mcp` only (mut=0 abort), not silent PASS.
- Read-after-write freshness: **PASS** (isolated + shared smoke).

### Mutation totals (`mcp_midpoint_audit_aggregate.json`, UTF-16 logs)
| Stage | Sessions | mutationCount Σ | mutOk=true Σ | hasUser=false | Note |
|------|----------|-----------------|--------------|---------------|------|
| 3 | 0 | — | — | — | **RE-RUN REQUIRED** |
| 4 | 10 | 23 | 23 | 0 | PASSED (watch regression) |
| 5 | 3 | 9 | 9 | 0 | PASSED 4/4 Automation |
| 6 | 5 | 19 | 19 | 0 | **REGRESSED** Automation 2/3 |
| 7 | 5 | 11 | 11 | 0 | **PAUSED incomplete**; no Stage7 test file |

### Shared LM Studio MCP (pre-fix)
- `routeContextStatus=blocked` / `TASK_ROUTE_BLOCKED` when stale tasks present.
- Recovery applied: `scripts/mcp_quarantine_stale_tasks.js` cancelled 61 tasks (backups under `~/.lmstudio/state/unreal-agent/quarantine/`).
- Post-fix shared smoke: write/read/patch/read-back all **true**.

## Stage scorecard

### Stage 3 — RE_RUN_REQUIRED
- Local AI files: none found
- MCP mutations: none
- Grok O-Mock edits: 0
- Build/Automation: no `Gomoku.Stage3.*`
- Static verify only historically → invalid completion

### Stage 4 — PASSED (re-verified earlier)
- Files: `Tests/GomokuStage4Behavior.spec.cpp`, Build.cs include path
- MCP mut paths include that test + game sources
- Automation 11/11
- Grok O-Mock edits: 0

### Stage 5 — PASSED
- Board template / blocked cells + `GomokuStage5BoardTemplate.spec.cpp`
- Automation 4/4
- Grok O-Mock edits: 0

### Stage 6 — REGRESSED (must re-run via local AI)
- Was 3/3; now Fail on `CanUseValidateExecuteSeparated`
- Cause: tests still use **ItemId=101**; Stage7 registry only **1–5**
- File: `Source/O_Mock/Tests/GomokuStage6ItemFramework.spec.cpp` lines ~39,63
- **Do not Grok-patch** — hand to local AI via MCP

### Stage 7 — PAUSED_INCOMPLETE
- Saved: `local_ai_stage7_p5.out.log` (interrupted; `write_file` Stage7 tests `mutOk=false`), session json
- Missing: `GomokuStage7Items.spec.cpp`
- Code quality to feed local AI (symptoms only):
  1. `ExecuteItem` Pull stub returns false **after** energy consume (leak)
  2. Steal ownership change: no win re-check
  3. SkipTurn `TargetPlayerIndex+1` vs PlayerId contract
  4. Guardian set not cleared on match init
  5. `InitialEnergyPerPlayer=10` vs `MaxEnergy=5`
  6. Inventory max-2 / gained-this-turn lock edge cases
  7. Items not wired into click → Execute path

## Grok O-Mock edits this campaign
**0** (infra scripts only under MCP repo).

## Recovery status vs required order

1. Failed Stage + tool state saved → `stage_campaign_state.json`, Stage7 p5 log  
2. Root cause reproduced → shared blocked by 61 running tasks; smoke parser false negative  
3. Minimal infra fix → quarantine stale tasks + smoke ok-parser  
4. read/write/patch smoke → isolated **PASS**, shared **PASS**  
5. LM Studio / MCP restart → **user action still required** (reconnect chats to clean state)  
6. active project / tool inventory reconfirm → after restart  
7. Re-run affected Stages via local AI → **not started** (blocked until user confirms restart)  
8. Full mutation/diff/build/behavior re-verify → pending  

## Next (only after user confirms LM Studio reconnected)

1. Local AI: fix Stage6 ItemId → registry 1–5; re-run Automation Stage6  
2. Local AI: finish Stage7 ExecuteItem + tests; build + Automation Stage7  
3. Re-run Stage4+5 Automation regression  
4. Local AI: Stage3 turn/time + Automation (first real MCP pass)  
5. Only then Stage 8
