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
    signal,
    shutdownTimeoutMs = 5000,
    outputLimitBytes = 0,
    persistLog = persistProcessLog,
    logTimeoutMs = 5000,
  } = options;
  return new Promise((resolve) => {
    const stdoutOwner = new BoundedProcessOutput(maxOutputBytes);
    const stderrOwner = new BoundedProcessOutput(maxOutputBytes);
    let child;
    let settled = false;
    let timer;
    let shutdownTimer;
    let terminationReason = "";
    let terminationError = "";
    let processExited = false;
    let processError = "";
    const finish = async (exitCode, spawnError = processError) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      clearTimeout(shutdownTimer);
      signal?.removeEventListener("abort", abort);
      child?.stdout?.removeListener("data", captureStdout);
      child?.stderr?.removeListener("data", captureStderr);
      // A stuck cleanup must not retain the runtime forever. This is not proof
      // that the OS process (or its descendants) has exited.
      if (terminationReason && !processExited) {
        child?.unref?.(); child?.stdout?.unref?.(); child?.stderr?.unref?.();
      }
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
      let logTimer;
      const logPersistenceError = child && logPath ? await Promise.race([
        Promise.resolve().then(() => persistLog(logPath, fullOutput)).catch(error => String(error?.message || error)),
        new Promise(done => { logTimer = setTimeout(() => done("Process log persistence deadline exceeded"), Math.max(1, Number(logTimeoutMs) || 5000)); }),
      ]).finally(() => clearTimeout(logTimer)) : "";
      resolve({
        exitCode: exitCode ?? 1,
        timedOut: terminationReason === "timeout",
        cancelled: terminationReason === "abort",
        outputLimited: terminationReason === "output",
        processStarted: Boolean(child?.pid),
        processExited,
        terminationStatus: !child?.pid ? "not_started" : processExited ? "process_exited" : "unconfirmed",
        terminationError,
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

    const requestTermination = (reason) => {
      if (settled || terminationReason) return;
      terminationReason = reason;
      clearTimeout(timer);
      // Arm before invoking the terminator: it can throw, hang, or emit close.
      shutdownTimer = setTimeout(() => { void finish(1); }, Math.max(1, Math.min(30000, Number(shutdownTimeoutMs) || 5000)));
      Promise.resolve().then(() => { if (!processExited) return terminate(child.pid, hostPlatform); }).catch(error => {
        terminationError = String(error?.message || error);
      });
    };
    const abort = () => requestTermination("abort");
    const capture = (owner, chunk) => {
      if (settled) return;
      owner.push(chunk);
      if (outputLimitBytes > 0 && stdoutOwner.totalBytes + stderrOwner.totalBytes > outputLimitBytes) requestTermination("output");
    };
    const captureStdout = chunk => capture(stdoutOwner, chunk);
    const captureStderr = chunk => capture(stderrOwner, chunk);
    if (signal?.aborted) { terminationReason = "abort"; void finish(1); return; }
    try { child = start(); }
    catch (error) { void finish(1, String(error?.message || error)); return; }
    child.stdout?.on("data", captureStdout);
    child.stderr?.on("data", captureStderr);
    child.once("close", (code) => { processExited = true; void finish(terminationReason ? 1 : code ?? 1); });
    child.on("error", (error) => {
      if (settled) return;
      processError = String(error?.message || error);
      if (child.pid) requestTermination("process_error");
      else void finish(1);
    });
    timer = setTimeout(() => requestTermination("timeout"), Math.max(1, Number(timeoutMs) || 1));
    signal?.addEventListener("abort", abort, { once: true });
    if (signal?.aborted) abort();
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
