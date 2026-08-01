"use strict";

const fsp = require("fs").promises;

async function readUtf8Tail(filePath, maxBytes = 4 * 1024 * 1024) {
  const limit = Math.max(1024, Math.min(Math.trunc(Number(maxBytes) || 0), 32 * 1024 * 1024));
  const handle = await fsp.open(filePath, "r");
  try {
    const stat = await handle.stat();
    const sourceBytes = Number(stat.size || 0);
    const start = Math.max(0, sourceBytes - limit);
    const length = sourceBytes - start;
    const buffer = Buffer.alloc(length);
    let bytesRead = 0;
    while (bytesRead < length) {
      const readResult = await handle.read(
        buffer,
        bytesRead,
        length - bytesRead,
        start + bytesRead,
      );
      const chunkBytes = Number(readResult.bytesRead || 0);
      if (chunkBytes <= 0) break;
      bytesRead += chunkBytes;
    }
    let payload = buffer.subarray(0, bytesRead);
    if (start > 0) {
      const separator = payload.indexOf(0x0a);
      payload = separator < 0 ? Buffer.alloc(0) : payload.subarray(separator + 1);
    }
    return {
      content: payload.toString("utf8"),
      sourceBytes,
      bytesRead,
      sourceTruncated: start > 0,
    };
  } finally {
    await handle.close();
  }
}

module.exports = { readUtf8Tail };
