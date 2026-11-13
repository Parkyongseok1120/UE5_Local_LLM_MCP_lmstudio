"use strict";

// Conversation 1794590083146: the build failed on a line that began with a
// literal backslash-t, and the diagnostic showed its source echo as
// "src: /tif (...)". Path separators are normalized; echoed code is not.
const test = require("node:test");
const assert = require("node:assert/strict");

const { compactDiagnostic, extractBuildDiagnostics } = require("../src/direct-build-response.js");

test("the echoed source line keeps its backslashes while the path is normalized", () => {
  const log = [
    "C:\\Users\\me\\Game\\Source\\Game\\Enemy.cpp(5,1): error C2017: illegal escape sequence",
    "\\tif (UCapsuleComponent* Cap = GetCapsuleComponent())",
    "^",
    "C:\\Users\\me\\Game\\Source\\Game\\Enemy.cpp(9,20): error C2001: newline in constant",
    "    FString S = TEXT(\"a\\nb\");",
    "                   ^",
  ].join("\r\n");
  assert.deepEqual(extractBuildDiagnostics(log, ""), [
    "Source/Game/Enemy.cpp(5,1): error C2017: illegal escape sequence | src: \\tif (UCapsuleComponent* Cap = GetCapsuleComponent())",
    "Source/Game/Enemy.cpp(9,20): error C2001: newline in constant | src: FString S = TEXT(\"a\\nb\");",
  ]);
});

test("a diagnostic without a source echo is normalized as before", () => {
  assert.equal(compactDiagnostic("D:\\P\\Source\\Game\\A.cpp(3): error C1083: Cannot open include file: 'Sub\\Missing.h'"),
    "Source/Game/A.cpp(3): error C1083: Cannot open include file: 'Sub/Missing.h'");
  assert.equal(compactDiagnostic("  LINK : fatal error LNK1181: cannot open input file 'x.lib'  "),
    "LINK : fatal error LNK1181: cannot open input file 'x.lib'");
});
