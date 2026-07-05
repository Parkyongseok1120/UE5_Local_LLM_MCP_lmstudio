# Refactor Performance Rails

This project treats compile-fix and refactor as different capability bands.

## Scope Bands

- `small_single_surface_refactor`: one symbol or one file surface; UBT plus static checks are usually enough.
- `small_multifile_refactor`: declaration, definition, callsite, delegate binding, or override changes across a few files.
- `medium_system_local_refactor`: one subsystem/component/API boundary changes across a local system; requires impact analysis and explicit approval before writes.
- `large_migration`: module, architecture, Blueprint/asset, SaveGame, network, map, cook, or package-impacting migration; the agent should act as a refactor manager, not an autonomous patcher.

## Required Flow

```text
request
-> classify refactor scope
-> impact analysis
-> symbol graph expansion
-> architecture rule check
-> staged patch plan
-> human approval for medium/large
-> staged patch
-> UBT/static validation
-> Blueprint/asset/map validation when relevant
-> report
```

`scripts/refactor_plan.py` backs the MCP tools:

- `unreal_refactor_manager_plan`
- `unreal_refactor_plan_validate`
- `unreal_refactor_impact_scan`

`unreal_refactor_manager_plan` is the control surface for refactor mode. It returns the scope band, approval state, write policy, missing impact roles, required gates, validation steps, and the R0-R4 stage contracts. The wrapper/orchestrator should consult this manager plan before stage-specific refactor tools or edits.

The impact scan classifies symbol hits as declaration, definition, callsite, delegate binding, override/virtual, Blueprint event, include owner, or module owner evidence. Medium and large refactors must not be treated as compile-fix-only tasks.
