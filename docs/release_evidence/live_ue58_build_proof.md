# Live UE 5.8 build proof gate (manual)

Status: NOT RUN

## Prerequisites

- UE 5.8 installed locally
- `ALLOW_UNREAL_BUILD=1` on unreal-agent MCP
- Valid `.uproject` under `WORKSPACE_ROOT`

## Steps

1. Run installer verify: `installer\Verify-UnrealMcp.ps1`
2. In LM Studio, call unreal-agent `build_unreal_project` against a UE 5.8 project
3. Save full MCP response and build log to:
   - `docs/release_evidence/live_ue58_build.log`
4. Confirm response fields:
   - `payload.ok=true` on success
   - compiler/UHT/linker failure: `payload.ok=false`, `buildOutcome=compile_failed`, `recoverable=true`, and MCP `isError` absent/false
   - infrastructure failure (timeout, spawn error, stale build): `buildOutcome=tool_failed` and MCP `isError=true`
   - `resolvedEngineVersion=5.8`
5. Update `docs/release_evidence/e6396af_final_gate_summary.txt` Gates 10-12 to PASS

## Failure criteria

- `BUILD_TIMEOUT` without log artifact
- Engine mismatch without explicit `allowEngineFallback`
- Infrastructure failure returned without MCP `isError=true`, or compiler failure incorrectly returned with MCP `isError=true`
