# LM Studio integration

Install three independent surfaces:

1. the `Evidence-First Code Audit` config preset under `~/.lmstudio/config-presets`;
2. the read-only `evidence-first` MCP entry in `~/.lmstudio/mcp.json`;
3. any project-specific file, build, or runtime MCP separately.

The portable MCP does not read or write project files. It supplies the reasoning contract and validates structured packets. This keeps SAFE mode independent from project adapters.

In LM Studio, select the installed preset for the chat and enable the `evidence-first` MCP. For API use on LM Studio 0.4+, pass `system_prompt` and the `mcp/evidence-first` integration. A preset installation does not retroactively alter an existing chat.

Run `scripts/smoke_evidence_first_mcp.py` after installation. A live model benchmark must compare the same model and cases with the contract disabled and enabled; the stdio smoke alone does not prove model-quality improvement.
