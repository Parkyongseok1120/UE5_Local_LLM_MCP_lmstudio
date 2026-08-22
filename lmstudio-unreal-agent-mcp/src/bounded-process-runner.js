"use strict";

const fs = require("fs");
const path = require("path");
const { decodeProcessOutput } = require("./process-output-decoder");
const { killProcessTree } = require("./process-tree-termination");

const DEFAULT_PROCESS_OUTPUT_BYTES = 4 * 1024 * 1024;
const MIN_PROCESS_OUTPUT_BYTES = 1024;
const MAX_PROCESS_OUTPUT_BYTES = 32 * 1024 * 1024;

function boundedOutputBytes(value = process.env.MCP_PROCESS_OUTPUT_MAX_BYTES) {
  const parsed = Number(value);
  if (!Number.isFinite(parsed)) return DEFAULT_PROCESS_OUTPUT_BYTES;
  return Math.max(
    MIN_PROCESS_OUTPUT_BYTES,
    Math.min(MAX_PROCESS_OUTPUT_BYTES, Math.trunc(parsed))
  );
}

function suffix(existing, incoming, limit) {
  if (limit <= 0) return Buffer.alloc(0);
  if (incoming.length >= limit) return Buffer.from(incoming.subarray(incoming.length - limit));
  const needed = limit - incoming.length;
  const prefix = existing.length > needed
    ? existing.subarray(existing.length - needed)
    : existing;
  return Buffer.concat([prefix, incoming], prefix.length + incoming.length);
}

class BoundedProcessOutput {
  constructor(maxBytes = boundedOutputBytes()) {
    this.maxBytes = boundedOutputBytes(maxBytes);
    this.headLimit = Math.floor(this.maxBytes / 2);
    this.tailLimit = this.maxBytes - this.headLimit;
    this.totalBytes = 0;
    this.head = Buffer.alloc(0);
    this.tail = Buffer.alloc(0);
  }

  push(value) {
    const chunk = Buffer.from(value || "");
    if (!chunk.length) return;
    const wasTruncated = this.totalBytes > this.maxBytes;
    this.totalBytes += chunk.length;
    if (this.head.length < this.headLimit) {
      const remaining = this.headLimit - this.head.length;
      const addition = chunk.subarray(0, Math.min(remaining, chunk.length));
      this.head = Buffer.concat([this.head, addition], this.head.length + addition.length);
    }
    const tailLimit = this.totalBytes > this.maxBytes ? this.tailLimit : this.maxBytes;
    this.tail = suffix(this.tail, chunk, tailLimit);
    if (!wasTruncated && this.totalBytes > this.maxBytes && this.tail.length > this.tailLimit) {
      this.tail = Buffer.from(this.tail.subarray(this.tail.length - this.tailLimit));
    }
  }

  get truncated() {
    return this.totalBytes > this.maxBytes;
  }

  chunks() {
    if (!this.truncated) return [this.tail];
    const omitted = this.totalBytes - this.head.length - this.tail.length;
    return [
      this.head,
      Buffer.from(`\n[... ${omitted} process-output bytes omitted ...]\n`, "utf8"),
      this.tail,
    ];
  }

  summary() {
    const capturedBytes = this.truncated
      ? this.head.length + this.tail.length
      : this.tail.length;
    return {
      totalBytes: this.totalBytes,
      capturedBytes,
      omittedBytes: Math.max(0, this.totalBytes - capturedBytes),
      truncated: this.truncated,
      maxBytes: this.maxBytes,
    };
  }
}

async function persistProcessLog(logPath, output) {
  if (!logPath) return "";
  try {
    await fs.promises.mkdir(path.dirname(logPath), { recursive: true });
    await fs.promises.writeFile(logPath, output, "utf8");
    return "";
  } catch (error) {
    return String(error?.message || error);
  }
}

function runBoundedProcess(options) {
  const {
    start,
    timeoutMs,
    logPath = "",
    hostPlatform = process.platform,
    maxOutputBytes = boundedOutputBytes(),
    terminate = killProcessTree,
    decode = decodeProcessOutput,
  } = options;
  return new Promise((resolve) => {
    const stdoutOwner = new BoundedProcessOutput(maxOutputBytes);
    const stderrOwner = new BoundedProcessOutput(maxOutputBytes);
    let child;
    try {
      child = start();
    } catch (error) {
      resolve({
        exitCode: 1,
        timedOut: false,
        spawnError: String(error?.message || error),
        outputDecodeError: "",
        logPersistenceError: "",
        stdout: "",
        stderr: "",
        stdoutCapture: stdoutOwner.summary(),
        stderrCapture: stderrOwner.summary(),
        fullLogPath: logPath || null,
      });
      return;
    }

    let settled = false;
    let timer;
    const finish = async (exitCode, timedOut = false, spawnError = "") => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      let stdout = "";
      let stderr = "";
      let outputDecodeError = "";
      try {
        stdout = decode(stdoutOwner.chunks(), { hostPlatform });
        stderr = decode(stderrOwner.chunks(), { hostPlatform });
      } catch (error) {
        outputDecodeError = String(error?.message || error);
      }
      const fullOutput = `${stdout}\n${stderr}`.trim();
      const logPersistenceError = await persistProcessLog(logPath, fullOutput);
      resolve({
        exitCode: exitCode ?? 1,
        timedOut,
        spawnError,
        outputDecodeError,
        logPersistenceError,
        stdout,
        stderr,
        stdoutCapture: stdoutOwner.summary(),
        stderrCapture: stderrOwner.summary(),
        fullLogPath: logPath || null,
      });
    };

    child.stdout?.on("data", (chunk) => stdoutOwner.push(chunk));
    child.stderr?.on("data", (chunk) => stderrOwner.push(chunk));
    timer = setTimeout(() => {
      settled = true;
      clearTimeout(timer);
      Promise.resolve(terminate(child.pid, hostPlatform))
        .catch(() => undefined)
        .then(async () => {
          // The timeout owns settlement; a concurrent close event observes settled.
          settled = false;
          await finish(1, true);
        });
    }, Math.max(1, Number(timeoutMs) || 1));
    child.once("close", (code) => { void finish(code ?? 1); });
    child.once("error", (error) => {
      void finish(1, false, String(error?.message || error));
    });
  });
}

module.exports = {
  BoundedProcessOutput,
  DEFAULT_PROCESS_OUTPUT_BYTES,
  MAX_PROCESS_OUTPUT_BYTES,
  MIN_PROCESS_OUTPUT_BYTES,
  boundedOutputBytes,
  persistProcessLog,
  runBoundedProcess,
};
