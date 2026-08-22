"use strict";

const assert = require("assert");
const fs = require("fs");
const os = require("os");
const path = require("path");
const test = require("node:test");

const facade = require("../src/automation-executor");
const commandContract = require("../src/automation-command-contract");
const outputParser = require("../src/automation-output-parser");
const processRunner = require("../src/automation-process-runner");
const sourceDiscovery = require("../src/automation-source-discovery");
const sourceParser = require("../src/automation-source-parser");

const SOURCE_ROOT = path.join(__dirname, "..", "src");
const OWNER_CAPS = new Map([
  ["automation-executor.js", 160],
  ["automation-command-contract.js", 250],
  ["automation-output-parser.js", 250],
  ["automation-process-runner.js", 250],
  ["automation-source-discovery.js", 250],
  ["automation-source-parser.js", 250],
]);

test("Automation facade preserves public exports while owners stay bounded", () => {
  assert.deepStrictEqual(Object.keys(facade).sort(), [
    "MAX_AUTOMATION_FILTERS",
    "automationArgs",
    "automationFilterMatches",
    "discoverAutomationTests",
    "moduleRootForTarget",
    "parseAutomationOutput",
    "resolveAutomationScopeRoots",
    "resolveEditorCmd",
    "resolveEditorCmdPaths",
    "runAutomationTests",
    "validateAutomationFilter",
  ].sort());
  assert.strictEqual(facade.automationArgs, commandContract.automationArgs);
  assert.strictEqual(facade.parseAutomationOutput, outputParser.parseAutomationOutput);
  assert.strictEqual(facade.discoverAutomationTests, sourceDiscovery.discoverAutomationTests);
  const coordinatorSource = fs.readFileSync(
    path.join(SOURCE_ROOT, "automation-executor.js"),
    "utf8"
  );
  assert.doesNotMatch(coordinatorSource, /require\("(?:fs|child_process|\.\/build-executor)"\)/);
  assert.doesNotMatch(coordinatorSource, /AUTOMATION_DECLARATION_PATTERNS|Test Completed\\\./);
  for (const [fileName, cap] of OWNER_CAPS) {
    const lines = fs.readFileSync(path.join(SOURCE_ROOT, fileName), "utf8").split(/\r?\n/).length;
    assert.ok(lines <= cap, `${fileName} grew to ${lines} lines (cap ${cap})`);
  }
});

test("Automation process owner reuses build process decoding and tree termination", () => {
  const source = fs.readFileSync(path.join(SOURCE_ROOT, "automation-process-runner.js"), "utf8");
  assert.match(source, /require\("\.\/build-executor"\)/);
  assert.match(source, /decodeBuildOutput/);
  assert.match(source, /killProcessTree/);
  assert.doesNotMatch(source, /function\s+(?:decodeBuildOutput|killProcessTree|assertEngineContainment)\b/);

  const commandSource = fs.readFileSync(path.join(SOURCE_ROOT, "automation-command-contract.js"), "utf8");
  assert.match(commandSource, /assertEngineContainment/);
  assert.doesNotMatch(commandSource, /function\s+assertEngineContainment\b/);
});

test("Source parser and log owner are directly usable without the coordinator", async () => {
  assert.deepStrictEqual(sourceParser.parseAutomationDeclarations([
    '// IMPLEMENT_SIMPLE_AUTOMATION_TEST(FComment, "Hidden.Comment", Flags)',
    'IMPLEMENT_SIMPLE_AUTOMATION_TEST(FReal, TEXT("Visible.Real"), Flags)',
  ].join("\n")), ["Visible.Real"]);

  const root = fs.mkdtempSync(path.join(os.tmpdir(), "automation-owner-log-"));
  try {
    const logPath = path.join(root, "logs", "automation.log");
    assert.strictEqual(await processRunner.persistAutomationLog(logPath, "bounded output"), "");
    assert.strictEqual(fs.readFileSync(logPath, "utf8"), "bounded output");
  } finally {
    fs.rmSync(root, { recursive: true, force: true });
  }
});
