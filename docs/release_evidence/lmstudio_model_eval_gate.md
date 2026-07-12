# LM Studio model reliability gate (manual)

Status: NOT RUN

## Models

- Qwen 3.5 9B
- Qwen 3.6 27B

## Metrics per model

- routing marker accuracy
- tool selection validity
- argument validity
- recovery after malformed tool call
- 100-call tools/call success rate >= 95%

## Runbook

1. Ensure control-plane blockers (cancel/PID, mutation, build envelope) are PASS
2. Start LM Studio with unreal-rag + unreal-agent MCP servers
3. Execute 100 tool-call prompts per model using harness cases in `config/rag_eval_agent_harness_cases.json`
4. Record results:

```powershell
python scripts/generate_lmstudio_eval_artifact.py `
  --model qwen3.5-9b `
  --pass-rate 0.97 `
  --routing-accuracy 0.98 `
  --recovery-ok

python scripts/generate_lmstudio_eval_artifact.py `
  --model qwen3.6-27b `
  --pass-rate 0.96 `
  --routing-accuracy 0.97 `
  --recovery-ok
```

5. Artifacts:
   - `docs/release_evidence/qwen3_5-9b_lmstudio_eval.json`
   - `docs/release_evidence/qwen3_6-27b_lmstudio_eval.json`

## Gate rule

Do not update release scorecard model reliability claims until both artifacts show `"status": "PASS"`.
