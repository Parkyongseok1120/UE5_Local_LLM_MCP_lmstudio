"use strict";

const fsp = require("fs").promises;

function boundedLimit(maxBytes) {
  return Math.max(
    1024,
    Math.min(Math.trunc(Number(maxBytes) || 0), 32 * 1024 * 1024),
  );
}

async function readUtf8Range(
  filePath,
  startByte = 0,
  maxBytes = 4 * 1024 * 1024,
  options = {},
) {
  const limit = boundedLimit(maxBytes);
  const handle = await fsp.open(filePath, "r");
  try {
    const stat = await handle.stat();
    const sourceBytes = Number(stat.size || 0);
    const requestedStart = Math.max(0, Math.min(Math.trunc(Number(startByte) || 0), sourceBytes));
    let startsAtLineBoundary = requestedStart === 0;
    if (requestedStart > 0) {
      const previousByte = Buffer.alloc(1);
      const previousRead = await handle.read(previousByte, 0, 1, requestedStart - 1);
      startsAtLineBoundary = previousRead.bytesRead === 1 && previousByte[0] === 0x0a;
    }
    const length = Math.min(limit, sourceBytes - requestedStart);
    const buffer = Buffer.alloc(length);
    let bytesRead = 0;
    while (bytesRead < length) {
      const readResult = await handle.read(
        buffer,
        bytesRead,
        length - bytesRead,
        requestedStart + bytesRead,
      );
      const chunkBytes = Number(readResult.bytesRead || 0);
      if (chunkBytes <= 0) break;
      bytesRead += chunkBytes;
    }
    let payload = buffer.subarray(0, bytesRead);
    let droppedLeadingBytes = 0;
    if (
      requestedStart > 0
      && !startsAtLineBoundary
      && options.preservePartialLeading !== true
    ) {
      const separator = payload.indexOf(0x0a);
      droppedLeadingBytes = separator < 0 ? payload.length : separator + 1;
      payload = separator < 0 ? Buffer.alloc(0) : payload.subarray(separator + 1);
    }
    const nextCursorByte = requestedStart + bytesRead;
    return {
      content: payload.toString("utf8"),
      sourceBytes,
      bytesRead,
      requestedStartByte: requestedStart,
      contentStartByte: requestedStart + droppedLeadingBytes,
      startsAtLineBoundary,
      nextCursorByte,
      hasMore: nextCursorByte < sourceBytes,
      sourceTruncated: requestedStart > 0 || nextCursorByte < sourceBytes,
    };
  } finally {
    await handle.close();
  }
}

async function readUtf8Tail(filePath, maxBytes = 4 * 1024 * 1024) {
  const limit = boundedLimit(maxBytes);
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

module.exports = { readUtf8Range, readUtf8Tail };
