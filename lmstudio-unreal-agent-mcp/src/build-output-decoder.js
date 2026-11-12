"use strict";

const fs = require("node:fs");
const path = require("node:path");
const { spawnSync } = require("node:child_process");
const { localeOutputEncoding, normalizeOutputEncoding } = require("./process-output-decoder");

// Conversation 1794469194476: every MSVC error of five failed builds reached the
// model cut after its code ("error C2440: '"). A Korean-only MSVC printed its
// messages in cp949 and each byte >= 0x80 arrived as U+FF00 + byte (sign
// extended on the way through UBA/UBT). The old sanitizer cut each line at the
// first such character, dropping the ASCII type names after it. The bytes are
// recoverable: "'\uFFC3\uFFCA\uFFB1\uFFE2..." is cp949 "'초기화 중'".
const SIGN_EXTENDED = /[\uff80-\uffff]/;
const CODE_PAGES = { 65001: "utf-8", 949: "euc-kr", 932: "shift_jis", 936: "gb18030", 950: "big5" };
const SINGLE_BYTE = /^(?:windows-12\d\d|iso-8859-\d+|ibm\d+|cp\d+)$/u;

let cachedAnsiEncoding;
/** The Windows ANSI code page (not the UI locale), read once. Null when unknown. */
function systemAnsiEncoding(hostPlatform = process.platform) {
  if (hostPlatform !== "win32") return null;
  if (cachedAnsiEncoding !== undefined) return cachedAnsiEncoding;
  cachedAnsiEncoding = null;
  try {
    const reg = path.join(process.env.SystemRoot || "C:\\Windows", "System32", "reg.exe");
    const result = spawnSync(reg, ["query", "HKLM\\SYSTEM\\CurrentControlSet\\Control\\Nls\\CodePage", "/v", "ACP"],
      { timeout: 2000, windowsHide: true, encoding: "latin1" });
    const page = Number(/ACP\s+REG_SZ\s+(\d+)/u.exec(String(result.stdout || ""))?.[1]);
    if (page) cachedAnsiEncoding = CODE_PAGES[page] || `windows-${page}`;
  } catch { /* unknown: callers fall back to the locale */ }
  return cachedAnsiEncoding;
}

function strictDecoder(label) {
  try { return new TextDecoder(label, { fatal: true }); } catch { return null; }
}

/** Decode a capture part; a head/tail cut can split a UTF-8 sequence at its edges. */
function decodePart(buffer, fallbackLabel) {
  const utf8 = strictDecoder("utf-8");
  for (const [start, end] of [[0, buffer.length], ...edgeTrims(buffer)]) {
    try { return utf8.decode(buffer.subarray(start, end)); } catch { /* try the next trim */ }
  }
  try { return new TextDecoder(fallbackLabel || "utf-8").decode(buffer); } catch { return buffer.toString("utf8"); }
}

function edgeTrims(buffer) {
  let start = 0, end = buffer.length;
  while (start < Math.min(3, buffer.length) && (buffer[start] & 0xc0) === 0x80) start++;
  // Drop a trailing lead byte plus its incomplete continuation bytes.
  for (let back = 1; back <= Math.min(3, buffer.length); back++) {
    const byte = buffer[buffer.length - back];
    if ((byte & 0xc0) === 0xc0) { end = buffer.length - back; break; }
    if ((byte & 0x80) === 0) break;
  }
  return start || end !== buffer.length ? [[start, end]] : [];
}

/** Rebuild the original bytes of runs that hold sign-extended characters.
 * A run is ASCII plus U+FF80-U+FFFF, so double-byte characters whose second
 * byte is ASCII stay whole; real Unicode text ends a run and is kept. A run
 * that does not decode strictly loses only its broken characters. */
function recoverSignExtendedLine(line, label) {
  if (!SIGN_EXTENDED.test(line)) return { text: line, recovered: 0, unrecovered: 0 };
  const decoder = label ? strictDecoder(label) : null;
  let text = "", recovered = 0, unrecovered = 0;
  for (let i = 0; i < line.length;) {
    const code = line.charCodeAt(i);
    if (code >= 0x80 && code < 0xff80) { text += line[i]; i++; continue; }
    let j = i, signed = false;
    const bytes = [];
    for (; j < line.length; j++) {
      const c = line.charCodeAt(j);
      if (c < 0x80) bytes.push(c);
      else if (c >= 0xff80) { bytes.push(c & 0xff); signed = true; }
      else break;
    }
    const run = line.slice(i, j);
    i = j;
    if (!signed) { text += run; continue; }
    let decoded = null;
    try { decoded = decoder ? decoder.decode(Buffer.from(bytes)) : null; } catch { decoded = null; }
    if (decoded === null) { text += run.replace(/[\uff80-\uffff]+/gu, "\ufffd"); unrecovered++; continue; }
    // Node's euc-kr (ICU) covers KS X 1001 only: a cp949 extension syllable
    // (lead 0x81-0xC6, ASCII second byte) comes back as a C1 control. Mark it
    // rather than pass a wrong character on.
    if (/[\u0080-\u009f]/u.test(decoded)) { decoded = decoded.replace(/[\u0080-\u009f]/gu, "\ufffd"); unrecovered++; }
    text += decoded; recovered++;
  }
  return { text, recovered, unrecovered };
}

function buildOutputEncoding(options = {}) {
  const hostPlatform = options.hostPlatform || process.platform;
  const explicit = options.encoding || process.env.MCP_BUILD_OUTPUT_ENCODING;
  if (explicit) return { label: normalizeOutputEncoding(explicit), source: "configured" };
  const ansi = options.ansiEncoding !== undefined ? options.ansiEncoding : systemAnsiEncoding(hostPlatform);
  if (ansi) return { label: ansi, source: "system_ansi_code_page" };
  let locale = options.locale;
  if (!locale) { try { locale = Intl.DateTimeFormat().resolvedOptions().locale; } catch { locale = ""; } }
  return { label: normalizeOutputEncoding(localeOutputEncoding(locale, hostPlatform)), source: "locale" };
}

/** A decoder for one build's stdout/stderr that keeps what it recovered. */
function createBuildOutputDecoder(options = {}) {
  const stats = { recoveredLines: 0, unrecoveredRuns: 0 };
  let encoding = null;
  const decode = (chunks, runtime = {}) => {
    encoding ||= buildOutputEncoding({ ...options, ...runtime });
    const parts = (Array.isArray(chunks) ? chunks : [chunks]).filter(Boolean).map(chunk => Buffer.from(chunk));
    const text = parts.map(part => decodePart(part, encoding.label)).join("");
    return text.split("\n").map((line) => {
      const result = recoverSignExtendedLine(line, encoding.label);
      if (result.recovered) stats.recoveredLines++;
      stats.unrecoveredRuns += result.unrecovered;
      return result.text;
    }).join("\n");
  };
  const summary = () => stats.recoveredLines || stats.unrecoveredRuns ? {
    encoding: encoding?.label || null, encodingSource: encoding?.source || null,
    ...(encoding && SINGLE_BYTE.test(encoding.label) ? { encodingConfidence: "low" } : {}),
    recoveredLines: stats.recoveredLines, unrecoveredRuns: stats.unrecoveredRuns,
  } : null;
  return { decode, summary };
}

const notifiedToolchains = new Set();
/** VSLANG=1033 asks MSVC for English messages; without its 1033 resources it
 * stays localized. Said once per toolchain per server process. */
function missingEnglishCompilerNotice(output, env = process.env, exists = fs.existsSync) {
  if (String(env.VSLANG || "") !== "1033") return null;
  const dir = /Using Visual Studio [\d.]+ toolchain \(([^)]+)\)/u.exec(String(output || ""))?.[1];
  if (!dir || notifiedToolchains.has(dir)) return null;
  const hosts = ["Hostx64/x64", "Hostx64/x86", "Hostx86/x86", "Hostx86/x64", "Hostarm64/arm64"];
  const anyHost = hosts.some(host => exists(path.join(dir, "bin", ...host.split("/"))));
  if (!anyHost || hosts.some(host => exists(path.join(dir, "bin", ...host.split("/"), "1033")))) return null;
  notifiedToolchains.add(dir);
  return "MSVC at this toolchain has no English (1033) UI resources, so VSLANG=1033 had no effect and compiler "
    + "messages are localized. They were recovered for this response. For English diagnostics, add the English "
    + "language pack in Visual Studio Installer.";
}

module.exports = {
  createBuildOutputDecoder,
  recoverSignExtendedLine,
  buildOutputEncoding,
  systemAnsiEncoding,
  missingEnglishCompilerNotice,
  decodePart,
};
