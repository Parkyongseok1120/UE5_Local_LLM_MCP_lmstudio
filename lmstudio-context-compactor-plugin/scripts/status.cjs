#!/usr/bin/env node
"use strict";

const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");

const MAX_EVENT_FILES = 256;
const MAX_EVENT_TAIL_BYTES = 1_048_576;
const MAX_SCAN_DIRECTORIES = 4_096;
const MAX_SCAN_ENTRIES = 100_000;
const MAX_FUTURE_SKEW_MINUTES = 1;
const VALID_DECISIONS = new Set(["normal", "soft_compact", "hard_compact"]);

function parseArgs(argv) {
  const options = {
    json: false,
    requireCompaction: false,
    stateRoot: path.join(os.homedir(), ".lmstudio", "unreal-context-compactor", "sessions"),
    maxAgeMinutes: 30,
  };
  for (let index = 0; index < argv.length; index += 1) {
    const arg = argv[index];
    if (arg === "--json") options.json = true;
    else if (arg === "--require-compaction") options.requireCompaction = true;
    else if (arg === "--state-root") {
      const value = argv[++index];
      if (!value) throw new Error("--state-root requires a path");
      options.stateRoot = path.resolve(String(value));
    }
    else if (arg === "--max-age-minutes") options.maxAgeMinutes = Number(argv[++index]);
    else throw new Error(`Unknown argument: ${arg}`);
  }
  if (!Number.isFinite(options.maxAgeMinutes) || options.maxAgeMinutes <= 0) {
    throw new Error("--max-age-minutes must be a positive number");
  }
  return options;
}

function eventFiles(root) {
  const files = [];
  const pending = [root];
  let directoryCount = 0;
  let entryCount = 0;
  while (pending.length > 0) {
    const dir = pending.pop();
    directoryCount += 1;
    if (directoryCount > MAX_SCAN_DIRECTORIES) {
      throw new Error(`context telemetry scan exceeded ${MAX_SCAN_DIRECTORIES} directories`);
    }
    for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
      entryCount += 1;
      if (entryCount > MAX_SCAN_ENTRIES) {
        throw new Error(`context telemetry scan exceeded ${MAX_SCAN_ENTRIES} entries`);
      }
      const full = path.join(dir, entry.name);
      if (entry.isDirectory()) pending.push(full);
      else if (entry.isFile() && /^events(?:-\d+)?\.jsonl$/u.test(entry.name)) {
        try {
          files.push({ file: full, mtimeMs: fs.statSync(full).mtimeMs });
        } catch (error) {
          if (!error || error.code !== "ENOENT") throw error;
        }
      }
    }
  }
  return files
    .sort((left, right) => right.mtimeMs - left.mtimeMs)
    .slice(0, MAX_EVENT_FILES)
    .map((item) => item.file);
}

function readEventTail(file) {
  const descriptor = fs.openSync(file, "r");
  let buffer;
  try {
    const size = fs.fstatSync(descriptor).size;
    const start = Math.max(0, size - MAX_EVENT_TAIL_BYTES);
    const length = size - start;
    buffer = Buffer.alloc(length);
    let bytesRead = 0;
    while (bytesRead < length) {
      const chunkBytes = fs.readSync(
        descriptor,
        buffer,
        bytesRead,
        length - bytesRead,
        start + bytesRead,
      );
      if (chunkBytes <= 0) break;
      bytesRead += chunkBytes;
    }
    buffer = buffer.subarray(0, bytesRead);
    if (start > 0) {
      const separator = buffer.indexOf(0x0a);
      buffer = separator < 0 ? Buffer.alloc(0) : buffer.subarray(separator + 1);
    }
  } finally {
    fs.closeSync(descriptor);
  }
  return buffer.toString("utf8");
}

function readEvents(root) {
  const events = [];
  for (const file of eventFiles(root)) {
    let lines;
    try {
      lines = readEventTail(file).split(/\r?\n/u);
    } catch (error) {
      if (error && error.code === "ENOENT") continue;
      throw error;
    }
    for (const line of lines) {
      if (!line.trim()) continue;
      try {
        const event = JSON.parse(line);
        if (event && typeof event === "object") events.push(event);
      } catch {
        // A process may be appending the final line. Earlier complete evidence is still valid.
      }
    }
  }
  return events;
}

function latest(events, predicate) {
  return events.filter(predicate).sort((left, right) => {
    const leftAt = Date.parse(String(left.at || ""));
    const rightAt = Date.parse(String(right.at || ""));
    return rightAt - leftAt;
  })[0] || null;
}

function isProxyMeasurement(event) {
  return event?.type === "context_measurement"
    && event?.proxyActive === true
    && String(event?.targetModel || "").trim().length > 0
    && Number.isFinite(Number(event?.inputTokens))
    && Number(event.inputTokens) >= 0
    && Number.isFinite(Number(event?.contextLength))
    && Number(event.contextLength) > 0
    && VALID_DECISIONS.has(String(event?.decision?.action || ""));
}

function ageMinutes(event, now = Date.now()) {
  const at = Date.parse(String(event?.at || ""));
  return Number.isFinite(at) ? Math.max(0, (now - at) / 60_000) : Number.POSITIVE_INFINITY;
}

function futureSkewMinutes(event, now = Date.now()) {
  const at = Date.parse(String(event?.at || ""));
  return Number.isFinite(at) ? (at - now) / 60_000 : Number.POSITIVE_INFINITY;
}

function isFreshEvent(event, maxAgeMinutes, now) {
  return futureSkewMinutes(event, now) <= MAX_FUTURE_SKEW_MINUTES
    && ageMinutes(event, now) <= maxAgeMinutes;
}

function inspect(options, now = Date.now()) {
  try {
    if (!fs.existsSync(options.stateRoot) || !fs.statSync(options.stateRoot).isDirectory()) {
      return { active: false, reason: "state_root_missing", stateRoot: options.stateRoot };
    }
  } catch (error) {
    return {
      active: false,
      reason: "state_root_unreadable",
      stateRoot: options.stateRoot,
      error: String(error.message || error),
    };
  }
  let events;
  try {
    events = readEvents(options.stateRoot);
  } catch (error) {
    return {
      active: false,
      reason: "state_root_unreadable",
      stateRoot: options.stateRoot,
      error: String(error.message || error),
    };
  }
  const measurement = latest(events, isProxyMeasurement);
  if (!measurement) {
    return { active: false, reason: "no_proxy_measurement", stateRoot: options.stateRoot };
  }
  const measurementFutureSkewMinutes = futureSkewMinutes(measurement, now);
  if (measurementFutureSkewMinutes > MAX_FUTURE_SKEW_MINUTES) {
    return {
      active: false,
      reason: "future_proxy_measurement",
      stateRoot: options.stateRoot,
      measuredAt: String(measurement.at || ""),
      measurementFutureSkewMinutes,
      maxFutureSkewMinutes: MAX_FUTURE_SKEW_MINUTES,
    };
  }
  const measurementAgeMinutes = ageMinutes(measurement, now);
  if (measurementAgeMinutes > options.maxAgeMinutes) {
    return {
      active: false,
      reason: "stale_proxy_measurement",
      stateRoot: options.stateRoot,
      measuredAt: String(measurement.at || ""),
      measurementAgeMinutes,
      maxAgeMinutes: options.maxAgeMinutes,
    };
  }
  const compaction = latest(events, (event) => event.type === "compaction_decision" && event.applied === true);
  const compactionFresh = Boolean(compaction && isFreshEvent(compaction, options.maxAgeMinutes, now));
  const result = {
    active: true,
    reason: "fresh_proxy_measurement",
    stateRoot: options.stateRoot,
    targetModel: String(measurement.targetModel || ""),
    measuredAt: String(measurement.at || ""),
    measurementAgeMinutes,
    maxAgeMinutes: options.maxAgeMinutes,
    inputTokens: Number(measurement.inputTokens || 0),
    contextLength: Number(measurement.contextLength || 0),
    action: String(measurement.decision?.action || ""),
    workingDirectory: String(measurement.workingDirectory || ""),
    compactionApplied: compactionFresh,
    compactedAt: compactionFresh ? String(compaction.at || "") : null,
    postRemainingTokens: compactionFresh ? Number(compaction.postRemainingTokens || 0) : null,
  };
  if (options.requireCompaction && !compactionFresh) {
    return { ...result, active: false, reason: "no_fresh_applied_compaction" };
  }
  return result;
}

function printResult(result, options) {
  if (options.json) {
    process.stdout.write(`${JSON.stringify(result)}\n`);
    return;
  }
  if (result.active) {
    process.stdout.write(
      `[PASS] Fresh context-compactor proxy evidence found.\n`
      + `Target model: ${result.targetModel}\n`
      + `Latest measurement: ${result.measuredAt} (${result.measurementAgeMinutes.toFixed(2)} minutes ago)\n`
      + `Input/context tokens: ${result.inputTokens}/${result.contextLength}\n`,
    );
  } else {
    process.stdout.write(
      `[FAIL] No context compactor proxy activation evidence was found (${result.reason}).\n`
      + "Select unreal-context-compactor in this chat's model dropdown, then send one message.\n"
      + "Selecting the underlying Qwen/GPT model directly bypasses the installed proxy.\n",
    );
  }
}

function main(argv = process.argv.slice(2)) {
  let options;
  try {
    options = parseArgs(argv);
    const result = inspect(options);
    printResult(result, options);
    return result.active ? 0 : (options.requireCompaction ? 3 : 2);
  } catch (error) {
    const result = { active: false, reason: "status_error", error: String(error.message || error) };
    if (argv.includes("--json")) process.stdout.write(`${JSON.stringify(result)}\n`);
    else process.stderr.write(`[FAIL] ${result.error}\n`);
    return 4;
  }
}

if (require.main === module) process.exitCode = main();

module.exports = { ageMinutes, inspect, main, parseArgs, readEvents };
