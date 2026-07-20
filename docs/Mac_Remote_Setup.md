# Using a Mac mini / Mac Studio as the LLM Server

You don't need a high-end Windows GPU to run this stack. If you have a **Mac mini or Mac Studio with Apple Silicon**, you can run LM Studio and the model there, while the **Windows PC handles Unreal Engine, UBT, and this project's tools**.

## Setup — Mac Side

1. Install **LM Studio** on the Mac and load your model.
2. Go to **Developer** tab in LM Studio → enable **Local Server**.
3. Enable **LM Link** (under the server settings) — this exposes the API over your local network, not just `localhost`.
   - You do **not** need to install this project's MCP tools on the Mac.
4. Note the Mac's local IP address (e.g., `192.168.1.x`) — visible in **System Settings → Network**.

## Setup — Windows PC Side

1. Clone and install this project on the Windows PC as normal:
   ```powershell
   git clone https://github.com/Parkyongseok1120/UE5_Local_LLM_MCP_lmstudio.git
   cd UE5_Local_LLM_MCP_lmstudio
   python install.py --profile standard --yes --enable-agent-mode --accept-agent-risk
   ```

2. Verify LM Link is reachable from Windows before running evals:
   ```powershell
   Invoke-RestMethod -Uri "http://<MAC_IP>:1234/v1/models"
   ```
   You should see your loaded model in the response.

3. When running eval or wrapper scripts, pass the Mac's IP instead of `localhost`:
   ```powershell
   python scripts\eval_pass_at_k.py --live --require-live `
     --config "config\rag_eval_real_project_holdout_cases.local.json" `
     --url "http://<MAC_IP>:1234/v1" `
     --model "your-model-id" `
     --ubt-path "<UE_ROOT>\Engine\Binaries\DotNET\UnrealBuildTool\UnrealBuildTool.exe"
   ```

## Notes

- **MCP chat in LM Studio (Windows)** also works with LM Link — in LM Studio on Windows, set the server URL to `http://<MAC_IP>:1234` in the Developer settings.
- UBT always runs locally on the **Windows PC** — the Mac only serves the model.
- Make sure both machines are on the **same local network** (Wi-Fi or Ethernet).
- If LM Link is not reachable, check the Mac's firewall settings and confirm LM Studio's server is running.
