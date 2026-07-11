"use strict";

const fs = require("fs");
const path = require("path");

const STALE_TEMP_AGE_MS = 60_000;

function uniqueTempPath(resolved) {
  return `${resolved}.${process.pid}.${Date.now()}.tmp`;
}

function cleanupStaleTempFiles(resolved) {
  const dir = path.dirname(resolved);
  const base = path.basename(resolved);
  const myPid = String(process.pid);
  const now = Date.now();
  try {
    for (const entry of fs.readdirSync(dir)) {
      if (!entry.startsWith(`${base}.`) || !entry.endsWith(".tmp")) {
        continue;
      }
      const middle = entry.slice(base.length + 1, -4);
      const parts = middle.split(".");
      const ownerPid = parts[0];
      const createdAt = Number(parts[1] || 0);
      const ownedByProcess = ownerPid === myPid;
      const stale = createdAt > 0 && now - createdAt > STALE_TEMP_AGE_MS;
      if (!ownedByProcess && !stale) {
        continue;
      }
      try {
        fs.unlinkSync(path.join(dir, entry));
      } catch {
        // Best-effort cleanup.
      }
    }
  } catch {
    // Ignore unreadable directories.
  }
}

function atomicWriteText(targetPath, content, encoding = "utf8") {
  const resolved = path.resolve(String(targetPath));
  cleanupStaleTempFiles(resolved);
  fs.mkdirSync(path.dirname(resolved), { recursive: true });
  const tempPath = uniqueTempPath(resolved);
  const fd = fs.openSync(tempPath, "w");
  try {
    fs.writeFileSync(fd, content, encoding);
    fs.fsyncSync(fd);
  } finally {
    fs.closeSync(fd);
  }
  fs.renameSync(tempPath, resolved);
}

function atomicWriteTextExclusive(targetPath, content, encoding = "utf8") {
  const resolved = path.resolve(String(targetPath));
  fs.mkdirSync(path.dirname(resolved), { recursive: true });
  const fd = fs.openSync(resolved, "wx");
  try {
    fs.writeFileSync(fd, content, encoding);
    fs.fsyncSync(fd);
  } finally {
    fs.closeSync(fd);
  }
}

function atomicWriteJson(targetPath, value) {
  atomicWriteText(targetPath, `${JSON.stringify(value, null, 2)}\n`, "utf8");
}

module.exports = {
  atomicWriteText,
  atomicWriteTextExclusive,
  atomicWriteJson,
  uniqueTempPath,
  cleanupStaleTempFiles,
};
