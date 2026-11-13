"use strict";

// Conversation 1794469194476: the static precheck skipped UBT for two includes
// a file had compiled with (one relative to the file, one with "//"), and the
// model then broke two correct includes. A missing include now blocks only
// when this runtime introduced it, and the Python resolver finds both forms.
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { spawnSync } = require("node:child_process");

const { BuildChangeLedger, includeSet } = require("../src/direct-build-change-ledger");
const { runBuildStaticPrecheck } = require("../src/direct-build-precheck");

function project(t) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "include-baseline-"));
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  const file = path.join(root, "Game.uproject");
  fs.writeFileSync(file, "{}");
  const write = (relative, text) => {
    fs.mkdirSync(path.dirname(path.join(root, relative)), { recursive: true });
    fs.writeFileSync(path.join(root, relative), text);
  };
  write("Source/Game/Game.Build.cs", "public class Game : ModuleRules { public Game(ReadOnlyTargetRules T) : base(T) {\n"
    + "PublicIncludePaths.Add(ModuleDirectory);\n} }\n");
  return { root, file, write };
}

test("the ledger keeps each pending file's includes from before its first change", (t) => {
  const { file } = project(t);
  const ledger = new BuildChangeLedger();
  ledger.record(file, ["Source/Game/A.cpp"], { "Source/Game/A.cpp": '#include "A.h"\n#include "Dir//B.h"\n' });
  ledger.record(file, ["Source/Game/A.cpp"], { "Source/Game/A.cpp": '#include "Changed.h"\n' });
  assert.deepEqual([...ledger.baselineIncludes(file, "Source/Game/A.cpp")], ["A.h", "Dir/B.h"], "the first change sets it");
  ledger.record(file, ["Source/Game/New.cpp"], { "Source/Game/New.cpp": "" });
  assert.deepEqual([...ledger.baselineIncludes(file, "Source/Game/New.cpp")], [], "a created file had none");
  ledger.record(file, ["Source/Game/Bundle.cpp"]);
  assert.equal(ledger.baselineIncludes(file, "Source/Game/Bundle.cpp"), null, "unrecorded content is unknown");
  ledger.settle(file, ledger.pending(file).sequence);
  assert.equal(ledger.baselineIncludes(file, "Source/Game/A.cpp"), null, "a good build clears it");
  assert.deepEqual([...includeSet(Buffer.from('#include <Engine/X.h>\n  #  include "a\\\\b.h"\n'))], ["Engine/X.h", "a/b.h"]);
});

const finding = (include, line = 2) => ({ severity: "error", code: "INCLUDE_PATH_NOT_FOUND", path: "Source/Game/A.cpp", line,
  message: `Include "${include}" does not resolve to a local project/plugin header. Known header location(s): Source/Game/Item/X.h.` });

async function precheck(root, findings, { baseline, head = null, pendingPaths = ["Source/Game/A.cpp"] } = {}) {
  return runBuildStaticPrecheck({ projectRoot: root, pending: { paths: pendingPaths, overflowed: false },
    runValidation: async () => ({ findings, findingCount: findings.length }), validationOptions: {},
    baselineFor: () => baseline === undefined ? null : new Set(baseline), headIncludes: async () => head && new Set(head) });
}

test("a missing include blocks only when this runtime introduced it", async (t) => {
  const { root, write } = project(t);
  write("Source/Game/A.cpp", '#include "A.h"\n#include "Wrong/X.h"\n');
  const introduced = await precheck(root, [finding("Wrong/X.h")], { baseline: ["A.h"] });
  assert.equal(introduced.definite.length, 1);
  assert.equal(introduced.staticPrecheck.findings[0].introduced, true);
  const existing = await precheck(root, [finding("Wrong//X.h")], { baseline: ["A.h", "Wrong/X.h"] });
  assert.equal(existing.definite.length, 0, "a line the file already had cannot be a definite error");
  assert.equal(existing.staticPrecheck.status, "advisory_errors");
  assert.deepEqual([existing.staticPrecheck.findings[0].definite, existing.staticPrecheck.findings[0].introduced], [false, false]);
  const fromHead = await precheck(root, [finding("Wrong/X.h")], { head: ["Wrong/X.h"] });
  assert.equal(fromHead.definite.length, 0, "without a recorded baseline, git HEAD decides");
  const unknown = await precheck(root, [finding("Wrong/X.h")]);
  assert.equal(unknown.definite.length, 0, "no baseline at all: UBT decides");
  assert.equal(unknown.staticPrecheck.findings[0].introduced, null);
  const removed = await precheck(root, [finding("Item/X.h")], { baseline: ["Item/X.h"],
    pendingPaths: ["Source/Game/A.cpp", "Source/Game/Item/X.h"] });
  assert.equal(removed.definite.length, 1, "an existing include whose header this runtime removed still blocks");
  const other = await precheck(root, [{ severity: "error", code: "GENERATED_H_NOT_LAST", path: "Source/Game/A.cpp", line: 1, message: "x" }]);
  assert.equal(other.definite.length, 1, "other definite codes are unchanged");
});

test("the real Python resolver feeds the precheck: compiling includes pass, a broken edit blocks", (t) => {
  const python = process.env.PYTHON_EXE || process.env.PYTHON || "python";
  const probe = spawnSync(python, ["-c", "import sys; print(sys.version_info[0])"], { encoding: "utf8" });
  if (probe.status !== 0 || probe.stdout.trim() !== "3") return t.skip("python 3 not available");
  const { root, file, write } = project(t);
  write("Source/Game/Enemy/AI/Controller.h", "#pragma once\n");
  write("Source/Game/Sound/Data.h", "#pragma once\n");
  write("Source/Game/Item/Orb.h", "#pragma once\n");
  const before = '#include "AI/Controller.h"\n#include "Sound//Data.h"\n#include "Item/Orb.h"\n';
  const after = before.replace('#include "Item/Orb.h"', '#include "Enemy/Item/Orb.h"');
  write("Source/Game/Enemy/Enemy.cpp", after);
  const scripts = path.resolve(__dirname, "..", "..", "scripts");
  const code = [
    "import json, sys", `sys.path.insert(0, ${JSON.stringify(scripts)})`,
    "from pathlib import Path",
    "from unreal_static_include import validate_include_paths_exist",
    "from unreal_static_scan import build_source_include_index",
    `root = Path(${JSON.stringify(root)})`, "target = root / 'Source/Game/Enemy/Enemy.cpp'",
    "found = validate_include_paths_exist(target, target.read_text(encoding='utf-8'), root, build_source_include_index(root))",
    "print(json.dumps([dict(severity=f.severity, code=f.code, path=f.path, line=f.line, message=f.message) for f in found]))",
  ].join("\n");
  const run = spawnSync(python, ["-I", "-c", code], { encoding: "utf8", env: { ...process.env, PYTHONUTF8: "1" } });
  assert.equal(run.status, 0, run.stderr);
  const findings = JSON.parse(run.stdout);
  assert.deepEqual(findings.map(item => [item.line, item.code]), [[3, "INCLUDE_PATH_NOT_FOUND"]],
    "the file-relative and '//' includes resolve; only the broken path is reported");
  const ledger = new BuildChangeLedger();
  ledger.record(file, ["Source/Game/Enemy/Enemy.cpp"], { "Source/Game/Enemy/Enemy.cpp": before });
  return runBuildStaticPrecheck({ projectRoot: root, pending: ledger.pending(file),
    runValidation: async () => ({ findings, findingCount: findings.length }), validationOptions: {},
    baselineFor: relative => ledger.baselineIncludes(file, relative), headIncludes: async () => null })
    .then((result) => {
      assert.equal(result.definite.length, 1, "the include this edit broke blocks the build");
      assert.equal(result.staticPrecheck.findings[0].introduced, true);
    });
});
