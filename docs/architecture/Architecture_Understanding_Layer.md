# Architecture Understanding Layer

Generated architecture hints. Review before treating as project truth.

## Purpose

The architecture understanding layer scans Unreal project source text and produces compact, structured context for review and refactor planning. It is designed to help local models reason about module ownership, reflected surfaces, header/cpp pairs, component and subsystem boundaries, Blueprint-facing risk, and editor/runtime boundary risk.

It does not call LM Studio, Unreal Editor, UnrealBuildTool, or network services.

## What It Does

- Detects project, plugin, module, Build.cs, target, source, and header/cpp pair surfaces.
- Parses Build.cs public/private dependencies with the existing parser.
- Detects UCLASS, USTRUCT, UINTERFACE, and simple regular C++ classes.
- Extracts UPROPERTY and UFUNCTION names/specifiers from source text.
- Extracts conservative non-reflected C++ member and method evidence into `memberEvidence`.
- Labels responsibility hints from deterministic name/base-type heuristics.
- Flags conservative risk hints such as Blueprint-facing surface, Blueprint event surface, serialized reflected surface, possible asset reference, missing cpp pair, and runtime/editor boundary risk.
- Validates structured architecture claims against the generated map.

## What It Does Not Do

- It does not execute refactors.
- It does not prove ownership.
- It does not inspect Blueprint graphs, Material graphs, or loaded assets.
- It does not prove that assets are unused.
- It does not replace compile-fix routing, UBT validation, or live project review.

## Generate A Map

```powershell
python scripts/architecture_map.py --project "<PROJECT_ROOT_OR_UPROJECT>" --out data/architecture/architecture_map.json --markdown data/architecture/PROJECT_MAP.generated.md
```

If `--project` is omitted, the script falls back to the configured active project when available.

Generated files under `data/architecture/` are local artifacts and should not be committed.

## Validate Claims

```powershell
python scripts/architecture_claim_validate.py --architecture data/architecture/architecture_map.json --claims claims.json
```

Claim validation is structured and conservative. A claim should name a subject and list required evidence, for example:

```json
{
  "claims": [
    {
      "claim": "UCombatComponent owns combo state",
      "type": "ownership",
      "subject": "UCombatComponent",
      "requiredEvidence": ["reflected property", "function", "cpp pair"],
      "riskIfChanged": ["Blueprint-facing function may break"]
    }
  ]
}
```

## How This Differs From Compile-Fix

Compile-fix workflows answer "what change makes this build pass?" Architecture understanding answers "what structure and risk context should a reviewer consider before planning changes?" It is inspect-only context. Risky changes still need UBT, Blueprint, asset, and editor/runtime validation.

## Safety Notes

- Treat responsibility hints as hints, not facts.
- Prefer false negatives over false positives.
- Do not rename reflected Blueprint-facing names without migration and reference validation.
- Do not claim an asset is unused from source text alone.
- Do not add editor modules to runtime code without boundary review.
