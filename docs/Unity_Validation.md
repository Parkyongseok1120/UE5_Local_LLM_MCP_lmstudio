# Unity v1.4.0 beta 1 — validation record

Date: 2026-09-15. Implementation target: Unity 2022.3+ common APIs. Actual Editor available for execution: Unity 6000.3.14f1, Intel macOS. Other Editor versions and Windows/Linux are not integration-tested.

## Latest: installer fixes, Prefab, approvals and Test Framework

These requested primitives are implemented, not registration-only stubs. The older checkpoint records below describe their then-unavailable state, not the current capability table.

- Actual Unity 6000.3.14f1: base and optional-package smoke **16 assertions each**. The base has no Input/uGUI/physics/Test Framework package and loads successfully.
- `ReleaseSmoke`: **20 assertions**, including isolated source create/edit, duplicate-name child identity, stale source receipt, exact apply, approved revert, variant/nested source isolation, independent approval required/denied/changed/expired/consumed, journal replay, component removal, actual fixture asset moved to OS Trash, old-Editor pending history becoming unknown.
- `PrefabDestinations`: **3 scenarios**: variant and nested overrides apply to their explicit selected source while leaving the inner/base asset unchanged; wrong source rejected; an open target Prefab Stage refuses isolated mutation.
- `test_unity_framework.js`: **8 scenarios** with Test Framework **1.6.0**. Actual EditMode pass and intentional assertion failure, exact empty selection returning not_run, PlayMode coroutine observing a frame advance, cooperative explicit/time-bound cancellation with owned job termination evidence, operation lookup, paged stored results, release and MCP permission checks. Cancellation is not reported as test success or side-effect rollback. Intermediate failures revealed reconstructed PlayMode root IDs, cleanup-state timing and fixture-history capacity; these were corrected/rechecked rather than counted as passes.
- `test_unity_regressions.js`: **3 scenarios**: 1,024-byte capture retains a stored ID; Edit→Play editable metadata alone does not create a value diff; UI and 3D physics DontSave probes each stop at 16 and expire while paused.
- `test_unity_debug.js`: **8 integrated scenarios** passed again. External semantic lookup → code/script/serialized/runtime references → real FSM query → immutable A → explicit damage → B → diff → explicit config edit → separately selected revalidation. Real Input, uGUI and collision observations, bounded recording, addition/removal scope and expired handles included. No model was loaded.
- `test_unity_snapshot.js`: **4 scenarios** passed again with packages absent, distinctions for stored values/references/null/arrays/uncollected/incomparable fields, 64-entry refusal without eviction and release.
- `test_unity_rpc.js`: **9 scenarios** passed again, including lost reply/idempotency, two distinct Play sessions, actual compile/reload, stored snapshot/record survival and expired receipts.
- Unity Node suite: **12 passed, 0 skipped** with real worker configuration. Enum members/local functions, explicit constructor/operator/indexer calls and property override relationships now resolve. Valid input has no synthetic no-source/no-output compiler errors; a genuinely broken compilation still returns partial evidence.
- Installer: actual isolated `install.py --components unity` completed with initialized stdio, **23 tools**, configured worker and connected Unity. Existing dependency/worker paths were explicitly supplied for this check. Model connection remains `not_verified`. Unit tests cover no-write dry-run, replacing bindings only explicitly, rollback after failed final checks, false/plain-text health refusal, reused Python path and data-only launcher config. Mocked Lima tests validate the generated custom VM/model/endpoint launcher; **no actual VM/login/model mutation was performed**.
- Final full Python regression: **1,013 passed, 20 skipped** (90.02 s), including the additional Unity dry-run and mocked generated-launcher selection tests. Installer/portable focused selection: **32 passed**. Earlier loaded-host runs had timeout/process-start-sensitive Node failures; standalone full Unreal Node rerun passed **240 + 5 platform skips**. Only the log-I/O test's process-start allowance changed; production timeout policies were not relaxed.
- Compactor: dependencies reused from the isolated audit cache, current source compiled, **120 passed**. No plugin activation or external model invocation.

Generic GUI-profile installer fixtures explicitly simulate a supported host; dedicated Intel refusal/headless tests still exercise x86_64. This does not claim Intel GUI support. The Unicode-distinct-path fixture skips on filesystems that physically alias the spellings. Portable packages include the new approval/prefab/test adapter/module and exclude Tests~, node_modules, discovery tokens and Editor state.

Approval integration exercised the trusted decision method called by the Editor window; human GUI button clicking/rendering was not automated. The tests used only disposable generated Unity projects, never the user's game. Temporary fixture deletion used OS Trash; retained failed-attempt test history was copied to a diagnostic checkpoint before explicit release. Owned test Editors were stopped after verification.

Remaining boundaries: other engine/OS/package versions, .NET 8 publish path, hostile project callbacks/concurrent external writers, every crash/disk-full boundary, UI Toolkit/Physics2D, nonserialized adapter-state snapshots, managed-reference type replacement, image capture and broader original alpha gaps are not newly claimed complete. Current source-to-loaded-assembly identity remains unknown where not provable. **This completes the requested Prefab/approval/Test Framework implementation and audited fixes, not an assertion that every original v1.4 feature or release matrix is finished.**

Reproduce in a disposable optional-package fixture:

```sh
UNITY_TEST_VERSION=<installed-version> UNITY_TEST_DEBUG=1 UNITY_TEST_RELEASE=1 \
  UNITY_TEST_FRAMEWORK_VERSION=<installed-package-version> \
  node scripts/test_unity_bridge.js /absolute/path/to/Unity /existing/work-parent
node scripts/test_unity_framework.js /generated/unity-bridge-test-project
node scripts/test_unity_regressions.js /generated/unity-bridge-test-project
# Set UNITY_DOTNET and UNITY_SYMBOL_WORKER for semantic integration:
node scripts/test_unity_debug.js /generated/unity-bridge-test-project
```

Use a separate minimal fixture for snapshot-capacity and baseline RPC tests. The harness prints its own Editor PID; stop only that process when done. The predetermined harness is never loaded by a product server.

## Earlier checkpoints (historical)

### Mandatory symbols/references/debug/snapshots expansion

These four areas remain mandatory v1.4 release criteria. The following are executed core-path tests, not a claim that the entire original v1.4 specification is complete.

- External Roslyn worker built and ran using the explicitly supplied local .NET 6.0.21 runtime/Roslyn 4.3.0 toolchain. The published .NET 8/Roslyn 4.11 project configuration has not separately been built on this host.
- Node test suite: **11 passed** with worker variables supplied. Includes real semantic binding under a define, interface implementation/inheritance, source hash/GUID, partial results with an actual missing-type compiler error, and the asynchronous single-worker reservation guard. Without worker variables the real-worker test is explicitly skipped.
- Fresh minimal and optional-package Unity fixtures: **16 smoke assertions passed in each** on 6000.3.14f1 Intel macOS.
- `scripts/test_unity_debug.js`: final **8 integrated scenarios passed** (earlier 7-scenario runs also passed). Semantic field → usage → script GUID → compiled config binding → serialized controller config reference → FSM query → stored snapshot A → explicit damage action → B → diff → Receipt-based config patch → caller-selected re-test. Observed health 100→80 before the scale edit; re-test after scale 2→1 returned 90. Also observed real Input System changed-control events, uGUI pointer callbacks and physics CollisionEnter, explicit action-correlated runtime references, actual spawned/destroyed children within a caller-selected root/depth, bounded recording and stale handles. Source/metadata hash pagination was exercised; an intermediate test was corrected to follow nextOffset instead of assuming all source rows were on the first metadata page. No model was loaded.
- `scripts/test_unity_snapshot.js`: **4 scenarios passed**. Optional packages absent but base works; Observe-only denies Execute; null/reference/value/array structure/inaccessible/not-collected distinctions; duplicate scope refusal; 64-item capacity rejects new captures without eviction, then explicit release.
- Updated `scripts/test_unity_rpc.js`: **9 scenarios passed**, including stored snapshot values and stopped recording metadata surviving an actual compiler-triggered domain reload, alongside the earlier RPC/receipt/idempotency cases.
- Portable runtime selection: **50 passed, 2 deselected** after adding worker/runtime/optional adapter sources and stable .meta files. One intermediate package-test failure was its broad glob mistakenly requiring macOS `.DS_Store`; the test now checks runtime extensions only. Runtime packaging never included that Finder state.

Test optional dependencies were supplied only to generated validation projects: Input System 1.19.0, uGUI 2.0.0, physics/ui/imgui built-in modules. Production adapters do not install them. Generated `debug-result.json`, `snapshot-result.json`, `rpc-result.json`, smoke reports and Editor logs retain the evidence in the isolated fixtures; no existing user game project was modified.

Limitations still requiring work/verification: nonserialized adapter-state snapshot fields, UI Toolkit/Physics2D, broad engine/OS/package-version matrix, malicious or long-running project callbacks, loss/recovery at every filesystem boundary, source-generator-derived symbols, exact source-to-loaded-assembly identity. Snapshot discovery intentionally stays within the exact selected objects or explicit descendant root/depth, never a server-selected project-wide scan. The available serialized-field and uGUI/3D paths must not be advertised as broader capabilities. Existing Prefab source/apply/revert, trusted destructive approvals and general Test Framework runner release gates are still unavailable. **Overall status: partial v1.4 implementation, not a final release.**

### Earlier alpha baseline

- `node --test lmstudio-unity-mcp/test/*.test.js`: 9 tests passed. Path policy, stale/forged/cross-project receipts, file creation preconditions, JSON schema and formatting, CSV byte preservation, query budgets/cursors, TCP handshake binding, and real SDK stdio initialize/list/call with Edit denial.
- Isolated Editor `-batchmode -nographics -executeMethod EvidenceFirst.Tests.BridgeSmoke.Run`: 16 assertions passed. Compile/assembly load, permissions, idempotent scene creation, user edit conflict, integer/array/object reference edits, managed-reference cycle retention, dirty/save scope, Prefab scope refusal and temporary handle invalidation.
- `node lmstudio-unreal-agent-mcp/test/run-tests.js`: 245 tests; 239 passed, 5 skipped, 1 timeout-sensitive failure. `Automation log persistence failure resolves as a bounded failure` passed when rerun alone (1/1). The first complete run is not recorded as fully passing.
- Test projects are disposable fixtures produced by `scripts/test_unity_bridge.js`; no existing game project is modified. The first fixture lacked a ProjectVersion marker and Unity populated default packages. The harness was corrected to require explicit `UNITY_TEST_VERSION` and a minimal manifest. Base Bridge load was also exercised with no Test Framework package.

## Fixes found while testing

- Fixed a C# local variable shadowing compilation error before claiming Editor support.
- A test SO initially had its class in a differently named script file. The direct in-memory checks passed, but indexed `t:SampleData` lookup exposed the missing MonoScript binding. The fixture now uses `SampleData.cs`; SO creation rejects types without a resolvable MonoScript asset.

## Live RPC and packaging

`node scripts/test_unity_rpc.js <isolated-test-project>`: all 8 predetermined scenarios passed against Unity 6000.3.14f1:

1. Authenticated project/session handshake.
2. Serialized patch through RPC; repeated operation returns its retained result.
3. SO creation using a project-discovered type with a real MonoScript binding.
4. TCP client intentionally disconnects after sending a create request; operation lookup and same-ID replay observe exactly one object.
5. Play/Pause/Step/Stop; old runtime handle is rejected.
6. Two Play sessions with Domain Reload disabled; distinct Play IDs and unchanged domain generation.
7. Create code → explicit path import → compiler warning → domain reload → same Editor session reconnect → matching compilation diagnostics. The API still reports source-to-assembly verification as unknown.
8. Old domain receipt is rejected after reconnection.

The minimal test project's manifest contains only the Bridge, Newtonsoft 3.2.1 and the built-in jsonserialize module. No Test Framework or game-specific package is installed in that fixture.

Packaging commands:

```sh
python -m pytest -q tests/test_unity_package.py tests/test_integrated_package.py tests/test_package_forbidden_filters.py tests/test_public_path_hygiene.py -k 'not test_package_has_all_platform_launchers_and_no_local_state and not test_packaged_unreal_skip_deps_fails_before_writing_mcp_config'
```

Result: **50 passed, 2 deselected**. The full initial selection produced **49 passed, 2 failed** before the Unity-specific packaging test was added. Those two existing installer tests expect GUI-capable LM Studio installation and hit the existing Intel macOS `--headless-lmlink` platform guard. They were not changed or reported as passing. The new Unity package test verifies all JS/C# runtime dependencies and excludes Editor state/test fixtures/node_modules. It passed again after the final portable README change.

`git diff --check` passed. Runtime source inspection found no fixed project/Editor installation path, current test Editor version, model endpoint, model loader or planner. No commit, push, release tag, or deployment was performed.

## Explicitly unverified or unavailable

- Other Unity releases/OS versions, junction races on Windows, hostile concurrent external writers.
- Nested Prefab/Variant source edit/apply/revert, delete approvals, managed-reference type replacement, long-running Test Framework execution and image capture: capabilities disabled, not stubbed as successes. Registered debug extensions and stored serialized snapshots now have the actual tests listed above.
- Current source-to-loaded-assembly identity: unknown. A compilation event alone is not proof that every current source file was loaded.
- Abrupt process kill at every journal/rename boundary, disk-full recovery, delayed OnValidate/ExecuteAlways side effects and generalized atomic rollback: not comprehensively tested.
- GUI-only flows and arbitrary third-party Custom Inspectors: not tested.
