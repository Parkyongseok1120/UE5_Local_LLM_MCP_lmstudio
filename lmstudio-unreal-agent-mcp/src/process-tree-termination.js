"use strict";

const { spawn } = require("child_process");

function killProcessTree(pid, hostPlatform = process.platform) {
  return new Promise((resolve) => {
    if (!Number.isInteger(pid) || pid <= 0) {
      resolve();
      return;
    }
    if (hostPlatform === "win32") {
      const killer = spawn("taskkill", ["/PID", String(pid), "/T", "/F"], { stdio: "ignore" });
      killer.on("close", () => resolve());
      killer.on("error", () => resolve());
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
