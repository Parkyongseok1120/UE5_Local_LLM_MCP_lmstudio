"use strict";

const fsp = require("fs").promises;

function boundedLimit(maxBytes) {
  return Math.max(
    1024,
    Math.min(Math.trunc(Number(maxBytes) || 0), 32 * 1024 * 1024),
  );
}

function utf8CompletePrefixLength(buffer) {
  if (buffer.length === 0) return 0;
  let leadIndex = buffer.length - 1;
  while (leadIndex >= 0 && (buffer[leadIndex] & 0xc0) === 0x80) {
    leadIndex -= 1;
  }
  if (leadIndex < 0) return 0;
  const lead = buffer[leadIndex];
  const width = lead <= 0x7f
    ? 1
    : (lead & 0xe0) === 0xc0
      ? 2
      : (lead & 0xf0) === 0xe0
        ? 3
        : (lead & 0xf8) === 0xf0
          ? 4
          : 1;
  return buffer.length - leadIndex < width ? leadIndex : buffer.length;
}

function limitPayloadByLines(payload, maxLines) {
  if (!Number.isFinite(Number(maxLines)) || Number(maxLines) <= 0) {
    return { payload, lineLimited: false };
  }
  const limit = Math.max(1, Math.trunc(Number(maxLines)));
  let newline = -1;
  for (let count = 0; count < limit; count += 1) {
    newline = payload.indexOf(0x0a, newline + 1);
    if (newline < 0) return { payload, lineLimited: false };
  }
  const end = newline + 1;
  return {
    payload: payload.subarray(0, end),
    lineLimited: end < payload.length,
  };
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
    const originalPayloadBytes = payload.length;
    let safeLength = utf8CompletePrefixLength(payload);
    // Keep CRLF together when a range ends between the delimiter bytes. The
    // returned cursor will reread the CR with the following LF.
    if (
      requestedStart + droppedLeadingBytes + safeLength < sourceBytes
      && safeLength > 0
      && payload[safeLength - 1] === 0x0d
    ) {
      safeLength -= 1;
    }
    payload = payload.subarray(0, safeLength);
    const limited = limitPayloadByLines(payload, options.maxLines);
    payload = limited.payload;
    const consumedBytes = droppedLeadingBytes + payload.length;
    const nextCursorByte = requestedStart + consumedBytes;
    return {
      content: payload.toString("utf8"),
      sourceBytes,
      bytesRead: consumedBytes,
      ioBytesRead: bytesRead,
      bytesReturned: payload.length,
      requestedStartByte: requestedStart,
      contentStartByte: requestedStart + droppedLeadingBytes,
      contentEndByte: nextCursorByte,
      startsAtLineBoundary,
      nextCursorByte,
      hasMore: nextCursorByte < sourceBytes,
      sourceTruncated: requestedStart > 0 || nextCursorByte < sourceBytes,
      lineLimited: limited.lineLimited,
      utf8BoundaryHeldBytes: originalPayloadBytes - safeLength,
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
