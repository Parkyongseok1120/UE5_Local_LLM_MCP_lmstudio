#!/usr/bin/env python3
"""Generate the proxy-safe subset of the canonical control-state registry."""

from __future__ import annotations

import json
from pathlib import Path

from atomic_io import atomic_write_text
from control_state_registry import CONTROL_STATE_REGISTRY


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "lmstudio-context-compactor-plugin" / "src" / "control-state-registry.generated.js"


def generated_payload() -> dict[str, object]:
    return {
        "source": "config/control_state_machine.json",
        "version": CONTROL_STATE_REGISTRY.version,
        "events": sorted(CONTROL_STATE_REGISTRY.events),
        "synthesisLifecycle": sorted(CONTROL_STATE_REGISTRY.synthesis_lifecycle),
        "proxyLifecycleStates": sorted(CONTROL_STATE_REGISTRY.proxy_lifecycle_states),
    }


def main() -> int:
    atomic_write_text(
        OUTPUT,
        '"use strict";\n\nmodule.exports = '
        + json.dumps(generated_payload(), ensure_ascii=False, indent=2)
        + ";\n",
        encoding="utf-8",
    )
    print(OUTPUT.relative_to(ROOT).as_posix())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
