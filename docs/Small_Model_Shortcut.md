# Compact Model Setup

Compact local models need smaller load settings and tighter prompts, but they
do not need a second agent framework. The user selects the model in LM Studio;
the selected model remains the sole owner of reasoning, tool choice and order,
stopping, and the final answer.

## Starting profiles

```powershell
$env:UNREAL_RAG_MODEL_PROFILE = "qwen3_5_9b"
python scripts/load_sampling_preset.py --show-profile
```

Other compact starting points are `qwen3_8b`,
`qwen3_5_9b_deepseek_v4_flash`, `gpt_oss_small`, and `gpt_oss_20b`.

| Profile | Context | Quant | Parallel requests |
|---|---:|---|---:|
| `qwen3_8b` | 24576 | Q4_K_M | 1 |
| `qwen3_5_9b` | 24576 | Q4_K_M | 1 |
| `qwen3_5_9b_deepseek_v4_flash` | 140032 | Q4_K_M | 1 |
| `gpt_oss_small` | 32768 | Q4_K_M | 1 |
| `gpt_oss_20b` | 32768 | Q4_K_M | 1 |

The Flash profile also lists 65536 as a portable alternative. Choose the
largest context that leaves reliable memory headroom on the actual machine.

## Keep the chat usable

These are operator recommendations, not enforced task stages:

- ask for one concrete outcome at a time when a compact model loses focus;
- keep irrelevant logs and generated files out of the prompt;
- use exact project selectors when multiple Unreal projects share the MCP;
- verify the active project's Unreal version before relying on engine-source
  evidence;
- prefer current file reads and concise diagnostics over pasting entire source
  trees into chat;
- use the checked-in Direct system prompt, not the historical turn prompts.

The model can call any available Direct tool whenever it judges the call useful.
There is no mandatory plan call, task activation, route gate, fixed tool order,
or profile-controlled retry loop.

## Safety is server-owned

Smaller models do not weaken the MCP safety boundary. Direct writes still use
path scope, exact-read/CAS checks, atomic replacement, locks, recoverable
deletion approval, size limits, and bounded responses. Semantic denylist results
are non-blocking advisories, including when the analyzer is unavailable. Builds
are immediate diagnostic operations when enabled; they are not a completion gate.

The profile's `writeSafety` fields are bounded compatibility inputs for the
legacy compile wrapper. They do not grant write permission and do not change
Direct MCP behavior.

## Optional compaction

Enable the LM Studio context compactor independently when long chats need it.
Compaction retains factual context but does not select a profile, alter static
sampling, create a plan, choose tools, or decide that the user's request is
complete. A compact model works with or without the compactor.

For model-specific caveats and alias lookup, see
[`Model_Profiles.md`](Model_Profiles.md) and
[`Community_Finetune_Model_Optimization.md`](Community_Finetune_Model_Optimization.md).
