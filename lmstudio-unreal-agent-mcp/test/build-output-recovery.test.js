"use strict";

// Conversation 1794469194476: a Korean-only MSVC printed cp949 bytes that
// reached the MCP as U+FF00 + byte, and every MSVC error of five failed builds
// was cut after its code ("error C2440: '"). The bytes are rebuilt now, and the
// note and source line MSVC prints after an error ride along in its entry.
const test = require("node:test");
const assert = require("node:assert/strict");

const { recoverSignExtendedLine, createBuildOutputDecoder, missingEnglishCompilerNotice, decodePart } = require("../src/build-output-decoder");
const { sanitizeBrokenCompilerLocalization } = require("../src/process-output-decoder");
const { buildDirectResponse, boundLongRunningPayload } = require("../src/direct-build-response");

// cp949 bytes as the build delivered them: each byte >= 0x80 sign-extended.
const signExtend = (bytes) => bytes.map(byte => String.fromCharCode(byte >= 0x80 ? 0xff00 | byte : byte)).join("");
const ascii = (text) => [...Buffer.from(text, "ascii")];
const INIT = [0xc3, 0xca, 0xb1, 0xe2, 0xc8, 0xad, 0x20, 0xc1, 0xdf]; // 초기화 중
const FROM = [0xbf, 0xa1, 0xbc, 0xad]; // 에서
const C2440 = signExtend([...ascii("Source\\Game\\Mover.cpp(27,43): error C2440: '"), ...INIT, ...ascii("': 'AController *'"),
  ...FROM, ...ascii(" 'AAIController *'")]);

test("sign-extended cp949 is rebuilt, and the ASCII after it is kept", () => {
  const result = recoverSignExtendedLine(C2440, "euc-kr");
  assert.equal(result.text, "Source\\Game\\Mover.cpp(27,43): error C2440: '초기화 중': 'AController *'에서 'AAIController *'");
  assert.deepEqual([result.recovered, result.unrecovered], [1, 0]);
  // A cp949 extension syllable ("갂" = 0x81 0x41, ASCII second byte): Node's
  // euc-kr decoder does not cover it, so it is marked, never passed on wrong.
  const extension = recoverSignExtendedLine(signExtend([...FROM, 0x81, 0x41]), "euc-kr");
  assert.equal(extension.text, "에서�A");
  assert.equal(extension.unrecovered, 1);
  // Real Unicode on the same line ends a run and is kept as is.
  assert.equal(recoverSignExtendedLine(`경로 ${signExtend(FROM)} 끝`, "euc-kr").text, "경로 에서 끝");
  assert.equal(recoverSignExtendedLine("plain line", "euc-kr").text, "plain line");
});

test("a run that does not decode loses only its broken characters", () => {
  const broken = signExtend([...ascii("error C2039: '"), 0xa1, ...ascii(")': 'APawn'")]);
  const result = recoverSignExtendedLine(broken, "euc-kr");
  assert.equal(result.unrecovered, 1);
  assert.match(result.text, /^error C2039: '\ufffd\)': 'APawn'$/u, "the type name after the broken byte survives");
  assert.equal(recoverSignExtendedLine(signExtend([0xc3, 0xca]), null).unrecovered, 1, "no code page: nothing is guessed");
});

test("the shared sanitizer no longer cuts a line at its first broken character", () => {
  assert.equal(sanitizeBrokenCompilerLocalization("x(1): error C2440: '\u3141 from AController* to AAIController*'"),
    "x(1): error C2440: '\ufffd from AController* to AAIController*'");
  assert.equal(sanitizeBrokenCompilerLocalization("error C2039: 'Empty'?\u3134\u3144 \u3142"), "error C2039: 'Empty'",
    "trailing garbage is still dropped");
});

test("a head/tail cut inside a UTF-8 sequence does not turn the whole output into another code page", () => {
  const text = "빌드 시작\n" + "가".repeat(40) + "\n오류 없음\n";
  const bytes = Buffer.from(text, "utf8");
  const head = bytes.subarray(0, 20), tail = bytes.subarray(bytes.length - 25);
  assert.match(decodePart(head, "euc-kr"), /^빌드 시작\n가/u);
  assert.match(decodePart(tail, "euc-kr"), /오류 없음\n$/u);
  const decoder = createBuildOutputDecoder({ ansiEncoding: "euc-kr" });
  const output = decoder.decode([head, Buffer.from("\n[... 1 process-output bytes omitted ...]\n"), tail]);
  assert.match(output, /^빌드 시작/u);
  assert.match(output, /오류 없음/u);
  assert.equal(decoder.summary(), null, "nothing needed recovery");
});

test("the decoder reports what it recovered and which code page it used", () => {
  const decoder = createBuildOutputDecoder({ ansiEncoding: "euc-kr" });
  const output = decoder.decode([Buffer.from(`${C2440}\r\nResult: Failed\r\n`, "utf8")]);
  assert.match(output, /'초기화 중'/u);
  assert.deepEqual(decoder.summary(), { encoding: "euc-kr", encodingSource: "system_ansi_code_page", recoveredLines: 1, unrecoveredRuns: 0 });
  const configured = createBuildOutputDecoder({ encoding: "cp1252" });
  configured.decode([Buffer.from(signExtend([0xe9]), "utf8")]);
  assert.equal(configured.summary().encodingConfidence, "low", "a single-byte code page decodes anything");
});

test("a missing English MSVC resource is said once per toolchain", () => {
  const output = "Using Visual Studio 14.51.36248 toolchain (C:\\VS\\MSVC\\14.51) and Windows SDK";
  const exists = (target) => /Hostx64[\\/]x64$/u.test(target);
  const notice = missingEnglishCompilerNotice(output, { VSLANG: "1033" }, exists);
  assert.match(notice, /no English \(1033\) UI resources/u);
  assert.equal(missingEnglishCompilerNotice(output, { VSLANG: "1033" }, exists), null, "once");
  const other = output.replace("14.51)", "14.52)");
  assert.equal(missingEnglishCompilerNotice(other, { VSLANG: "1033" }, target => true), null, "English resources present");
  assert.equal(missingEnglishCompilerNotice(other, { VSLANG: "1042" }, exists), null, "user chose another language");
});

const LOG = [
  "[2/5] Compile [x64] Mover.cpp",
  "C:\\P\\Source\\Game\\Mover.cpp(27,43): error C2440: '초기화 중': 'AController *'에서 'AAIController *'(으)로 변환할 수 없습니다.",
  "            if (AAIController* Controller = Pawn->GetController())",
  "                                          ^",
  "C:\\P\\Source\\Game\\Mover.cpp(27,43): note: 기본 형식에서 파생 형식으로 캐스트하려면 dynamic_cast 또는 static_cast가 필요합니다.",
  "C:\\P\\Source\\Game\\Enemy.h(12): note: 'AEnemy' 선언을 참조하십시오.",
  "C:\\P\\Source\\Game\\Enemy.h(13): note: third note is dropped",
  "C:\\P\\Source\\Game\\Data.cpp(3,18): error C2511: 'UData::UData(void)': 오버로드된 멤버 함수가 'UData'에 없습니다.",
  "Mover.cpp.obj : error LNK2019: unresolved external symbol",
  "Result: Failed (OtherCompilationError)",
].join("\r\n");

test("an MSVC error carries its notes and source line, from the build output itself", () => {
  const payload = buildDirectResponse({ result: { ok: false, exitCode: 6, stdout: LOG, stderr: "",
    outputRecovery: { encoding: "euc-kr", recoveredLines: 3, unrecoveredRuns: 0 } },
  build: { target: "GameEditor", engineRoot: "E" }, projectPath: "C:/P/Game.uproject", command: "c", logPath: "l" });
  const [first, second, link] = payload.diagnostics;
  assert.equal(first, "Source/Game/Mover.cpp(27,43): error C2440: '초기화 중': 'AController *'에서 'AAIController *'(으)로 변환할 수 없습니다."
    + " | note: 기본 형식에서 파생 형식으로 캐스트하려면 dynamic_cast 또는 static_cast가 필요합니다. | note: Enemy.h:12 'AEnemy' 선언을 참조하십시오."
    + " | src: if (AAIController* Controller = Pawn->GetController())");
  assert.doesNotMatch(first, /third note/u, "at most two notes");
  assert.equal(second, "Source/Game/Data.cpp(3,18): error C2511: 'UData::UData(void)': 오버로드된 멤버 함수가 'UData'에 없습니다.",
    "the next error is a boundary, not context");
  assert.equal(link, "Mover.cpp.obj : error LNK2019: unresolved external symbol");
  assert.equal(payload.outputRecovery.recoveredLines, 3);
  assert.ok(JSON.stringify(boundLongRunningPayload(payload)).length <= 3584);
});
