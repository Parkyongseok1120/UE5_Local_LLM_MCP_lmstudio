"use strict";

function normalizeOutputEncoding(value) {
  const label = String(value || "").trim().toLowerCase().replace(/_/g, "-");
  const aliases = {
    utf8: "utf-8",
    cp949: "euc-kr",
    "windows-949": "euc-kr",
    cp932: "shift_jis",
    "windows-31j": "shift_jis",
    cp936: "gb18030",
  };
  return aliases[label] || label;
}

function localeOutputEncoding(locale = "", hostPlatform = process.platform) {
  if (hostPlatform !== "win32") return "utf-8";
  const normalized = String(locale || "").toLowerCase();
  if (normalized.startsWith("ko")) return "euc-kr";
  if (normalized.startsWith("ja")) return "shift_jis";
  if (normalized.startsWith("zh-tw") || normalized.startsWith("zh-hk")) return "big5";
  if (normalized.startsWith("zh")) return "gb18030";
  return "windows-1252";
}

function sanitizeBrokenCompilerLocalization(text) {
  return String(text || "").split(/\r?\n/).map((line) => {
    const brokenAt = line.search(/[\u3130-\u318f\uff61-\uffdc\ufffd]/);
    if (brokenAt < 0) return line;
    return line.slice(0, brokenAt).replace(/\?+\s*$/, "").trimEnd();
  }).join("\n");
}

function decodeProcessOutput(chunks, options = {}) {
  const list = Array.isArray(chunks) ? chunks : [chunks];
  const buffer = Buffer.concat(list.filter(Boolean).map((chunk) => Buffer.from(chunk)));
  if (!buffer.length) return "";

  try {
    return sanitizeBrokenCompilerLocalization(
      new TextDecoder("utf-8", { fatal: true }).decode(buffer)
    );
  } catch {
    // Windows compiler output often follows the installed UI codepage.
  }

  let locale = options.locale;
  if (!locale) {
    try { locale = Intl.DateTimeFormat().resolvedOptions().locale; } catch { locale = ""; }
  }
  const requested = normalizeOutputEncoding(
    options.encoding
      || process.env.MCP_BUILD_OUTPUT_ENCODING
      || localeOutputEncoding(locale, options.hostPlatform || process.platform)
  );
  try {
    return sanitizeBrokenCompilerLocalization(
      new TextDecoder(requested || "utf-8").decode(buffer)
    );
  } catch {
    return sanitizeBrokenCompilerLocalization(buffer.toString("utf8"));
  }
}

module.exports = {
  decodeProcessOutput,
  localeOutputEncoding,
  normalizeOutputEncoding,
  sanitizeBrokenCompilerLocalization,
};
