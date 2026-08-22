# Community Model Profile Compatibility

Community GGUF files can differ materially from their base family. Qwen 3.8 27B
is the current primary validated recommendation. Muse Glimmer is under testing
and is not yet a validated recommendation. Qwen 3.5, community fine-tuned Qwen
3.6 27B, and GPT-OSS profiles remain only for historical reproduction and
existing-install compatibility; this guide does not recommend them for current
Direct use.

Treat profiles in
[`config/lmstudio_sampling.json`](../config/lmstudio_sampling.json) as static
load/chat compatibility data for the exact model file selected by the user, not
as runtime agent policy or an endorsement.

Historical compatibility aliases include:

- `qwen3_6_27b`;
- `qwen3_5_9b`;
- `qwen3_5_9b_deepseek_v4_flash`;
- `gpt_oss_20b`;
- `gpt_oss_20b_claude_opus_sonnet_reasoning_i1`.

## Inspect a profile

Select a profile explicitly for the helper process:

```powershell
$env:UNREAL_RAG_MODEL_PROFILE = "qwen3_8_27b"
python scripts/load_sampling_preset.py --show-profile
```

Or resolve an installed model id without changing the loaded LM Studio model:

```powershell
python scripts/load_sampling_preset.py `
  --model "lmstudio-community/Qwen3.8-27B-GGUF/Qwen3.8-27B-Q4_K_M.gguf" `
  --show-profile
```

Apply context length, quantization, parallel-request count, and optional static
sampling manually when loading or configuring the model. The helper only prints
the stored profile values.

## Direct behavior

Every profile points to
[`lmstudio_direct_model_system.md`](../prompts/lmstudio_direct_model_system.md).
The selected model decides how to reason, which available MCP tool to call,
whether calls can run in parallel, when enough evidence exists, and when to
answer. There are no model-profile plan/critique/execute turns, compile-fix
stages, tool-order rules, or per-retry sampling switches.

The optional LM Studio context compactor is independent. It compacts history
only after an explicit two-switch, per-chat opt-in. The installer does not enable
the host-owned top-level chat toggle, which must be verified OFF per chat; the
nested `Enable transparent compaction` switch defaults OFF. It does not
consume a model profile, choose a model, set sampling, or decide task completion.

## Hardware and model validation

For every exact GGUF and machine:

1. For a compatibility test, start with the profile's stored quantization,
   context, and one parallel request; this does not make the model a product
   recommendation.
2. Confirm the model loads without memory pressure or repeated KV-cache
   failures.
3. Run representative read, search, write-safety, and tool-call cases across
   the Unreal versions and projects you intend to support.
4. Compare larger context or a different quantization one setting at a time.
5. Require UBT or Editor evidence before reporting Unreal changes as verified.

Stored context values are not a promise that a particular machine can serve that
context. Historical profiles retain their original alternatives for
reproducibility, not as current model recommendations.

Historical benchmark results remain in
[`Model_Measurement_Results.md`](Model_Measurement_Results.md). They do not
authorize writes or become control policy merely because a profile alias
matches the measured model family.
