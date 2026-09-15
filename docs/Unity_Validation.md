# Unity v1.4.0 alpha — validation record

Date: 2026-09-15. Implementation target: Unity 2022.3+ common APIs. Actual Editor available for execution: Unity 6000.3.14f1, Intel macOS. Other Editor versions and Windows/Linux are not integration-tested.

## Executed

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
- Nested Prefab/Variant source edit/apply/revert, delete approvals, managed-reference type replacement, long-running Test Framework execution, registered debug extensions and image capture: capabilities disabled, not stubbed as successes.
- Current source-to-loaded-assembly identity: unknown. A compilation event alone is not proof that every current source file was loaded.
- Abrupt process kill at every journal/rename boundary, disk-full recovery, delayed OnValidate/ExecuteAlways side effects and generalized atomic rollback: not comprehensively tested.
- GUI-only flows and arbitrary third-party Custom Inspectors: not tested.
