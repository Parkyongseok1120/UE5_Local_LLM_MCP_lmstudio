"use strict";

const assert = require("assert");
const test = require("node:test");
const { allowedCommandBase, parseAllowedCommand } = require("../src/command-policy.js");

test("POSIX allows the host python3 version probe without enabling Python execution", () => {
  for (const hostPlatform of ["linux", "darwin"]) {
    assert.strictEqual(allowedCommandBase("python3 --version", hostPlatform), true);
    assert.deepStrictEqual(parseAllowedCommand("python3 --version", hostPlatform), {
      file: "python3",
      args: ["--version"],
      shell: false,
    });
    assert.strictEqual(allowedCommandBase("python3 script.py", hostPlatform), false);
    assert.strictEqual(allowedCommandBase("python3 -c \"print(1)\"", hostPlatform), false);
    assert.strictEqual(allowedCommandBase("python3 --version; whoami", hostPlatform), false);
  }
});

test("python3 is not added to the Windows command policy", () => {
  assert.strictEqual(allowedCommandBase("python3 --version", "win32"), false);
  assert.strictEqual(parseAllowedCommand("python3 --version", "win32"), null);
  assert.strictEqual(allowedCommandBase("python --version", "win32"), true);
});

test("shell metacharacter blocking still precedes the POSIX python3 allowlist", () => {
  for (const command of [
    "python3 --version & whoami",
    "python3 --version | sh",
    "python3 --version > output.txt",
    "python3 --version < input.txt",
  ]) {
    assert.strictEqual(allowedCommandBase(command, "linux"), false);
    assert.strictEqual(parseAllowedCommand(command, "linux"), null);
  }
});

test("generic commands with path operands are not exposed by the diagnostic allowlist", () => {
  for (const command of [
    "dir %CD%",
    "type !MCP_FILE!",
    "where node^&whoami",
    "findstr value\r\nwhoami",
    "dir C:\\Outside",
    "type C:\\Outside\\secret.txt",
    "findstr secret C:\\Outside\\secret.txt",
    "where node",
    "cl Source\\Demo.cpp",
    "msbuild C:\\Outside\\Project.sln",
    "dotnet build C:\\Outside\\Project.csproj",
  ]) {
    assert.strictEqual(allowedCommandBase(command, "win32"), false);
    assert.strictEqual(parseAllowedCommand(command, "win32"), null);
  }
});

test("Git allowlist rejects options and branch forms with side effects", () => {
  for (const command of [
    "git diff --output=outside.patch",
    "git diff --output outside.patch",
    "git diff --ext-diff",
    "git diff --no-index C:\\Outside\\one.txt C:\\Outside\\two.txt",
    "git log -- ..\\Outside\\secret.txt",
    "git status --git-dir=C:\\Outside\\.git",
    "git status --work-tree=/outside",
    "git show --textconv HEAD:file.bin",
    "git branch --edit-description",
    "git branch topic",
    "git branch -d topic",
  ]) {
    assert.strictEqual(allowedCommandBase(command, "linux"), false, command);
    assert.strictEqual(parseAllowedCommand(command, "linux"), null, command);
  }
});

test("Git allowlist preserves bounded read-only inspection forms", () => {
  for (const command of [
    "git status --short",
    "git diff --check",
    "git log -5",
    "git show HEAD",
    "git rev-parse HEAD",
    "git branch",
    "git branch --show-current",
    "git branch --list feature/*",
    "git branch -a",
  ]) {
    assert.strictEqual(allowedCommandBase(command, "linux"), true, command);
    assert.ok(parseAllowedCommand(command, "linux"), command);
  }
});
