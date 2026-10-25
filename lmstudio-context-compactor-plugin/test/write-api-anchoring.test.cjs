"use strict";

// A recorded write session created files that included "Engine/InputMappingContext.h"
// and used FCollisionObjectTypes::AllStatic without reading any engine source;
// both later failed the build, one build at a time. The check names such
// additions before the build, and stays quiet for names the execution has seen.

const assert = require("node:assert/strict");
const test = require("node:test");
const { ChatMessage } = require("@lmstudio/sdk");
const { unverifiedWriteApis, writeApiFact } = require("../dist/write-api-anchoring");

function exchange(messages, id, name, args, result) {
  messages.push(ChatMessage.from({ role: "assistant", content: [{ type: "toolCallRequest",
    toolCallRequest: { type: "function", id, name, arguments: args } }] }));
  messages.push(ChatMessage.from({ role: "tool", content: [{ type: "toolCallResult", toolCallId: id,
    content: JSON.stringify([{ type: "text", text: JSON.stringify(result) }]) }] }));
  return messages;
}

const NEW_FILE = [
  "#include \"GameCharacter.h\"",
  "#include \"Engine/InputMappingContext.h\"",
  "#include \"GameCharacter.generated.h\"",
  "void AGameCharacter::Setup()",
  "{",
  "  const float Clamped = FMath::Max(0.f, Speed);",
  "  Params.AddObjectTypesToQuery(FCollisionObjectTypes::AllStatic);",
  "  Super::Setup();",
  "}",
].join("\n");

function session() {
  const messages = [];
  exchange(messages, "w1", "write_file", { path: "Source/Game/Private/GameCharacter.cpp", content: NEW_FILE },
    { ok: true, path: "project://Source/Game/Private/GameCharacter.cpp" });
  return messages;
}

test("engine headers and members an edit adds without any returned source are named", () => {
  const names = unverifiedWriteApis(session()).map(api => api.name);
  assert.deepEqual(names, ["#include \"Engine/InputMappingContext.h\"", "FCollisionObjectTypes::AllStatic"]);
  const fact = writeApiFact(unverifiedWriteApis(session()));
  assert.match(fact, /unreal_symbol_lookup/);
  assert.match(fact, /not a verdict/);
});

test("names a lookup, an engine read or the project already shows are not flagged", () => {
  const messages = session();
  exchange(messages, "l1", "unreal_symbol_lookup", { query: "FCollisionObjectQueryParams" },
    { ok: true, matches: [{ symbol: "FCollisionObjectTypes", text: "AllStatic" }] });
  exchange(messages, "s1", "search_files", { query: "InputMappingContext.h" },
    { ok: true, results: [{ path: "Plugins/EnhancedInput/Source/EnhancedInput/Public/InputMappingContext.h" }] });
  assert.deepEqual(unverifiedWriteApis(messages).map(api => api.name), ["#include \"Engine/InputMappingContext.h\""]);
});

test("edit hunks contribute only their added lines, and failed or read-only rounds add nothing", () => {
  const messages = [];
  exchange(messages, "r1", "replace_in_file", { path: "Source/Game/Private/A.cpp" }, { ok: true, path: "project://Source/Game/Private/A.cpp",
    changeEvidence: { hunks: [{ before: "  UEnhancedInputComponent* Input = nullptr;", after: "  UEnhancedInputComponent* Input = nullptr;\n  Input->AddMappingContext(Context, 0);\n  EKeyEvent::Pressed;" }] } });
  exchange(messages, "r2", "replace_in_file", { path: "Source/Game/Private/B.cpp" }, { ok: false, errorCode: "OCCURRENCE_MISMATCH",
    changeEvidence: { hunks: [{ before: "", after: "UNeverApplied::Member();" }] } });
  assert.deepEqual(unverifiedWriteApis(messages).map(api => api.name), ["EKeyEvent::Pressed"]);
  assert.deepEqual(unverifiedWriteApis([]), []);
  assert.equal(writeApiFact([]), null);
});
