"use strict";

const crypto = require("node:crypto");
const fs = require("node:fs");
const fsp = fs.promises;

const MAX_BOUNDED_READ_BYTES = 32 * 1024 * 1024;
const DEFAULT_WINDOW_BYTES = 64 * 1024;
const DEFAULT_HASH_CHUNK_BYTES = 64 * 1024;

function boundedLimit(maxBytes) {
  return Math.max(
    1024,
    Math.min(Math.trunc(Number(maxBytes) || 0), MAX_BOUNDED_READ_BYTES),
  );
}

function strictByteLimit(value, fallback = DEFAULT_WINDOW_BYTES) {
  const parsed = Number(value);
  const selected = Number.isFinite(parsed) && parsed > 0
    ? Math.trunc(parsed)
    : fallback;
  return Math.max(1, Math.min(selected, MAX_BOUNDED_READ_BYTES));
}

function positiveInteger(value, fallback, maximum = Number.MAX_SAFE_INTEGER) {
  const parsed = Number(value);
  if (!Number.isFinite(parsed) || parsed <= 0) return fallback;
  return Math.max(1, Math.min(Math.trunc(parsed), maximum));
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

function utf8CodePointWidth(lead) {
  if (lead <= 0x7f) return 1;
  if ((lead & 0xe0) === 0xc0) return 2;
  if ((lead & 0xf0) === 0xe0) return 3;
  if ((lead & 0xf8) === 0xf0) return 4;
  return 1;
}

function leadingUtf8ContinuationBytes(buffer) {
  let skipped = 0;
  while (skipped < buffer.length && (buffer[skipped] & 0xc0) === 0x80) {
    skipped += 1;
  }
  return skipped;
}

function countByte(buffer, expected) {
  let count = 0;
  for (const value of buffer) {
    if (value === expected) count += 1;
  }
  return count;
}

async function readAtMost(handle, position, length) {
  if (length <= 0) return { buffer: Buffer.alloc(0), bytesRead: 0 };
  const buffer = Buffer.alloc(length);
  let bytesRead = 0;
  while (bytesRead < length) {
    const result = await handle.read(
      buffer,
      bytesRead,
      length - bytesRead,
      position + bytesRead,
    );
    const current = Number(result.bytesRead || 0);
    if (current <= 0) break;
    bytesRead += current;
  }
  return { buffer: buffer.subarray(0, bytesRead), bytesRead };
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

/**
 * Hash a file without materializing it in memory.
 *
 * The returned byte count lets a caller bind the digest to the amount of data
 * observed. Callers that need a stable-file proof should still compare their
 * pre/post stat signatures around this operation.
 */
async function streamFileSha256(filePath, options = {}) {
  const highWaterMark = positiveInteger(
    options.highWaterMark,
    DEFAULT_HASH_CHUNK_BYTES,
    4 * 1024 * 1024,
  );
  const hash = crypto.createHash("sha256");
  let bytesHashed = 0;
  const stream = fs.createReadStream(filePath, {
    highWaterMark,
    ...(options.signal ? { signal: options.signal } : {}),
  });
  for await (const chunk of stream) {
    hash.update(chunk);
    bytesHashed += chunk.length;
  }
  return {
    algorithm: "sha256",
    digest: hash.digest("hex"),
    bytesHashed,
    highWaterMark,
  };
}

async function sha256File(filePath, options = {}) {
  return (await streamFileSha256(filePath, options)).digest;
}

/**
 * Read one strict byte-bounded UTF-8 window.
 *
 * Unlike the legacy log range API, this accepts a 4-byte minimum and exposes
 * enough boundary metadata to traverse a multi-megabyte single line without
 * returning malformed UTF-8 or silently advancing past content. A caller must
 * pass continuation.cursorByte back as startByte for the next window.
 */
async function readUtf8Window(filePath, options = {}) {
  const limit = strictByteLimit(options.maxBytes, DEFAULT_WINDOW_BYTES);
  const handle = await fsp.open(filePath, "r");
  try {
    const stat = await handle.stat();
    const sourceBytes = Number(stat.size || 0);
    const requestedStartByte = Math.max(
      0,
      Math.min(Math.trunc(Number(options.startByte) || 0), sourceBytes),
    );
    let startsAtLineBoundary = requestedStartByte === 0;
    if (requestedStartByte > 0) {
      const previous = await readAtMost(handle, requestedStartByte - 1, 1);
      startsAtLineBoundary = previous.bytesRead === 1 && previous.buffer[0] === 0x0a;
    }

    const requestedLength = Math.min(limit, sourceBytes - requestedStartByte);
    const raw = await readAtMost(handle, requestedStartByte, requestedLength);
    let payload = raw.buffer;
    let leadingUtf8BytesSkipped = 0;
    // A caller may supply an arbitrary cursor rather than one produced by this
    // module. Skip only orphan continuation bytes; generated cursors are always
    // aligned and therefore take this branch with a zero skip.
    if (requestedStartByte > 0 && options.alignUtf8Start !== false) {
      leadingUtf8BytesSkipped = leadingUtf8ContinuationBytes(payload);
      payload = payload.subarray(leadingUtf8BytesSkipped);
    }

    let leadingLineBytesDropped = 0;
    if (
      requestedStartByte > 0
      && !startsAtLineBoundary
      && options.dropPartialLeadingLine === true
    ) {
      const separator = payload.indexOf(0x0a);
      leadingLineBytesDropped = separator < 0 ? payload.length : separator + 1;
      payload = separator < 0 ? Buffer.alloc(0) : payload.subarray(separator + 1);
    }

    const beforeUtf8Trim = payload.length;
    let safeLength = utf8CompletePrefixLength(payload);
    const utf8SafeLength = safeLength;
    let minimumBytesRequired = (
      payload.length > 0
      && safeLength === 0
      && leadingUtf8BytesSkipped === 0
    )
      ? utf8CodePointWidth(payload[0])
      : 0;
    const contentStartByte = requestedStartByte
      + leadingUtf8BytesSkipped
      + leadingLineBytesDropped;
    // Do not split CRLF. Re-reading the CR with its LF on the next window makes
    // byte cursors deterministic on files produced by Windows tools.
    let crlfBoundaryHeldBytes = 0;
    if (
      options.keepCrlfTogether !== false
      && contentStartByte + safeLength < sourceBytes
      && safeLength > 0
      && payload[safeLength - 1] === 0x0d
    ) {
      const following = await readAtMost(handle, contentStartByte + safeLength, 1);
      if (following.bytesRead === 1 && following.buffer[0] === 0x0a) {
        safeLength -= 1;
        crlfBoundaryHeldBytes = 1;
        if (safeLength === 0) minimumBytesRequired = Math.max(minimumBytesRequired, 2);
      }
    }
    payload = payload.subarray(0, safeLength);
    const beforeLineLimit = payload.length;
    const limited = limitPayloadByLines(payload, options.maxLines);
    payload = limited.payload;

    const contentEndByte = contentStartByte + payload.length;
    const newlineCount = countByte(payload, 0x0a);
    const endsWithNewline = payload.length > 0 && payload[payload.length - 1] === 0x0a;
    const sourceHasMore = contentEndByte < sourceBytes;
    const contentStartsMidLine = contentStartByte > 0 && !startsAtLineBoundary
      && leadingLineBytesDropped === 0;
    const contentEndsMidLine = payload.length > 0 && sourceHasMore && !endsWithNewline;
    const startLineNumber = Number.isFinite(Number(options.startLineNumber))
      ? Math.max(1, Math.trunc(Number(options.startLineNumber)))
      : null;
    const nextLineNumber = startLineNumber == null
      ? null
      : startLineNumber + newlineCount;
    const continuation = sourceHasMore
      ? {
        cursorByte: contentEndByte,
        ...(nextLineNumber == null ? {} : { line: nextLineNumber }),
        startsMidLine: contentEndsMidLine,
      }
      : null;

    return {
      content: payload.toString("utf8"),
      sourceBytes,
      maxBytes: limit,
      requestedStartByte,
      contentStartByte,
      contentEndByte,
      nextCursorByte: contentEndByte,
      ioBytesRead: raw.bytesRead,
      bytesReturned: payload.length,
      startsAtLineBoundary,
      endsAtLineBoundary: !sourceHasMore || endsWithNewline,
      contentStartsMidLine,
      contentEndsMidLine,
      newlineCount,
      lineLimited: limited.lineLimited,
      byteLimited: sourceHasMore && !limited.lineLimited,
      hasMore: sourceHasMore,
      sourceTruncated: requestedStartByte > 0 || sourceHasMore,
      leadingUtf8BytesSkipped,
      leadingLineBytesDropped,
      utf8BoundaryHeldBytes: beforeUtf8Trim - utf8SafeLength,
      crlfBoundaryHeldBytes,
      lineLimitHeldBytes: beforeLineLimit - payload.length,
      minimumBytesRequired,
      progressBlocked: sourceHasMore && contentEndByte === requestedStartByte,
      continuation,
    };
  } finally {
    await handle.close();
  }
}

/** Locate a 1-based line start using fixed-size reads. */
async function locateUtf8LineStart(filePath, targetLine, options = {}) {
  const requestedLine = positiveInteger(targetLine, 1);
  const scanBytes = strictByteLimit(options.scanBytes, DEFAULT_HASH_CHUNK_BYTES);
  const handle = await fsp.open(filePath, "r");
  try {
    const stat = await handle.stat();
    const sourceBytes = Number(stat.size || 0);
    if (requestedLine === 1) {
      return {
        requestedLine,
        lineFound: true,
        byteOffset: 0,
        actualLine: 1,
        bytesScanned: 0,
        sourceBytes,
      };
    }
    let position = 0;
    let currentLine = 1;
    while (position < sourceBytes) {
      const raw = await readAtMost(
        handle,
        position,
        Math.min(scanBytes, sourceBytes - position),
      );
      if (raw.bytesRead <= 0) break;
      for (let index = 0; index < raw.buffer.length; index += 1) {
        if (raw.buffer[index] !== 0x0a) continue;
        currentLine += 1;
        if (currentLine === requestedLine) {
          return {
            requestedLine,
            lineFound: true,
            byteOffset: position + index + 1,
            actualLine: currentLine,
            bytesScanned: position + index + 1,
            sourceBytes,
          };
        }
      }
      position += raw.bytesRead;
    }
    return {
      requestedLine,
      lineFound: false,
      byteOffset: sourceBytes,
      actualLine: currentLine,
      bytesScanned: position,
      sourceBytes,
    };
  } finally {
    await handle.close();
  }
}

/**
 * Read a 1-based line range through a strict byte window.
 *
 * For a line larger than maxBytes, rangeComplete is false and nextStartLine is
 * intentionally unchanged. Supply continuation.cursorByte and
 * continuation.line to resume the same line without rescanning the prefix.
 */
async function readUtf8LineWindow(filePath, options = {}) {
  const requestedStartLine = positiveInteger(options.startLine, 1);
  const requestedEndLine = Math.max(
    requestedStartLine,
    positiveInteger(options.endLine, requestedStartLine),
  );
  const suppliedCursor = Number.isFinite(Number(options.cursorByte))
    ? Math.max(0, Math.trunc(Number(options.cursorByte)))
    : null;
  const suppliedCursorLine = Number.isFinite(Number(options.cursorLine))
    ? Math.max(1, Math.trunc(Number(options.cursorLine)))
    : requestedStartLine;
  const location = suppliedCursor == null
    ? await locateUtf8LineStart(filePath, requestedStartLine, options)
    : null;
  if (location && !location.lineFound) {
    return {
      content: "",
      sourceBytes: location.sourceBytes,
      maxBytes: strictByteLimit(options.maxBytes, DEFAULT_WINDOW_BYTES),
      requestedStartLine,
      requestedEndLine,
      contentStartLine: location.actualLine,
      contentEndLine: location.actualLine,
      nextStartLine: location.actualLine,
      requestedStartByte: location.byteOffset,
      contentStartByte: location.byteOffset,
      contentEndByte: location.byteOffset,
      nextCursorByte: location.byteOffset,
      bytesReturned: 0,
      lineFound: false,
      rangeComplete: true,
      hasMore: false,
      sourceHasMore: false,
      continuation: null,
      bytesScannedToStart: location.bytesScanned,
    };
  }

  const contentStartLine = suppliedCursor == null
    ? requestedStartLine
    : suppliedCursorLine;
  const startByte = suppliedCursor == null ? location.byteOffset : suppliedCursor;
  const remainingLineCount = Math.max(1, requestedEndLine - contentStartLine + 1);
  const window = await readUtf8Window(filePath, {
    startByte,
    startLineNumber: contentStartLine,
    maxBytes: options.maxBytes,
    maxLines: remainingLineCount,
    alignUtf8Start: options.alignUtf8Start,
    keepCrlfTogether: options.keepCrlfTogether,
  });
  const endsWithNewline = window.content.endsWith("\n");
  const contentEndLine = window.content.length === 0
    ? contentStartLine
    : contentStartLine + window.newlineCount - (endsWithNewline ? 1 : 0);
  const nextStartLine = contentStartLine + window.newlineCount;
  const rangeComplete = window.lineLimited
    || !window.hasMore
    || nextStartLine > requestedEndLine;
  const continuation = !rangeComplete
    ? {
      cursorByte: window.nextCursorByte,
      line: nextStartLine,
      startLine: nextStartLine,
      endLine: requestedEndLine,
      startsMidLine: window.contentEndsMidLine,
    }
    : null;

  return {
    ...window,
    requestedStartLine,
    requestedEndLine,
    contentStartLine,
    contentEndLine,
    nextStartLine,
    lineFound: true,
    rangeComplete,
    hasMore: !rangeComplete,
    sourceHasMore: window.hasMore,
    byteLimited: !rangeComplete && window.byteLimited,
    continuation,
    bytesScannedToStart: location?.bytesScanned || 0,
  };
}

module.exports = {
  locateUtf8LineStart,
  readUtf8LineWindow,
  readUtf8Range,
  readUtf8Tail,
  readUtf8Window,
  sha256File,
  streamFileSha256,
};
