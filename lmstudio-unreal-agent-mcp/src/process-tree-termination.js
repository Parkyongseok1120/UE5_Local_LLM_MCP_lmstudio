"use strict";

const { spawn } = require("child_process");

function killProcessTree(pid, hostPlatform = process.platform) {
  return new Promise((resolve) => {
    if (!Number.isInteger(pid) || pid <= 0) {
      resolve();
      return;
    }
    if (hostPlatform === "win32") {
      const killer = spawn("taskkill", ["/PID", String(pid), "/T", "/F"], { stdio: "ignore", windowsHide: true });
      const deadline = setTimeout(() => { try { killer.kill(); } catch { /* cleanup remains unconfirmed */ } killer.unref(); resolve(); }, 5000);
      const finish = () => { clearTimeout(deadline); resolve(); };
      killer.on("close", finish);
      killer.on("error", finish);
      return;
    }
    try {
      process.kill(-pid, "SIGKILL");
    } catch {
      try { process.kill(pid, "SIGKILL"); } catch { /* process already exited */ }
    }
    resolve();
  });
}

module.exports = { killProcessTree };
