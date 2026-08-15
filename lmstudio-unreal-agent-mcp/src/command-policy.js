"use strict";

const READ_ONLY_GIT_SUBCOMMANDS = new Set(["status", "diff", "log", "show", "rev-parse", "branch"]);

function splitCommandTokens(commandLine) {
  return String(commandLine || "").match(/(?:[^\s"]+|"[^"]*")+/g) || [];
}

function readOnlyGitCommandAllowed(commandLine) {
  const parts = splitCommandTokens(commandLine).map((part) => part.replace(/^"|"$/g, ""));
  if (parts.length < 2 || parts[0].toLowerCase() !== "git") return false;

  const subcommand = parts[1].toLowerCase();
  if (!READ_ONLY_GIT_SUBCOMMANDS.has(subcommand)) return false;

  const args = parts.slice(2);
  const unsafeDiffOption = args.some((arg) => {
    const lower = arg.toLowerCase();
    return lower === "--output"
      || lower.startsWith("--output=")
      || lower === "--ext-diff"
      || lower === "--textconv";
  });
  if (unsafeDiffOption) return false;

  if (subcommand !== "branch") return true;
  if (args.length === 0) return true;
  if (args.length === 1 && args[0].toLowerCase() === "--show-current") return true;

  // `git branch <name>` creates a ref, while several branch options delete,
  // move, copy, configure, or launch an editor. Expose only explicit listing
  // forms; their remaining operands are patterns rather than new ref names.
  const first = args[0].toLowerCase();
  const listingForms = new Set([
    "--list", "-l", "--all", "-a", "--remotes", "-r",
    "--verbose", "-v", "-vv",
  ]);
  return listingForms.has(first)
    && args.slice(1).every((arg) => !arg.startsWith("-"));
}

function allowedCommandBase(commandLine, hostPlatform = process.platform) {
  const trimmed = String(commandLine || "").trim();
  if (!trimmed) return false;
  if (/[&|<>]/.test(trimmed)) return false;
  if (
    hostPlatform === "win32"
    && /^(dir|type|where|findstr)(\s|$)/i.test(trimmed)
    && /[%!^\r\n]/.test(trimmed)
  ) {
    // These built-ins are dispatched through cmd.exe. Block expansion/escape
    // syntax before the allowlist so an inherited environment cannot turn a
    // seemingly read-only probe into a second command.
    return false;
  }

  const lower = trimmed.toLowerCase();
  const denyPatterns = [
    /\bdel\b/i,
    /\berase\b/i,
    /\brmdir\b/i,
    /\brd\b/i,
    /\bformat\b/i,
    /\breg\s+delete\b/i,
    /\bshutdown\b/i,
    /\btaskkill\b/i,
    /\bsetx\b/i,
    /\bmklink\b/i,
    /\btakeown\b/i,
    /\bicacls\b/i,
    /\bpowershell\b.*\b(iwr|irm|invoke-webrequest|invoke-restmethod)\b/i,
    /\bcurl\b.*\|\s*(powershell|cmd|sh|bash)/i,
  ];
  if (denyPatterns.some((re) => re.test(lower))) return false;

  if (/^git(?:\s|$)/i.test(trimmed)) {
    return readOnlyGitCommandAllowed(trimmed);
  }

  const allowPatterns = [
    /^dir(\s|$)/i,
    /^type(\s|$)/i,
    /^where(\s|$)/i,
    /^findstr(\s|$)/i,
    /^cl(\s|$)/i,
    /^msbuild(\s|$)/i,
    /^dotnet\s+build(\s|$)/i,
    /^node\s+--version$/i,
    /^npm\s+--version$/i,
    /^python\s+--version$/i,
    /^py\s+--version$/i,
  ];
  if (hostPlatform !== "win32") {
    // The repository's POSIX Python bridge resolves to python3. Keep this probe
    // as narrow as its Windows counterpart: no scripts, flags, or operands.
    allowPatterns.push(/^python3\s+--version$/i);
  }
  return allowPatterns.some((re) => re.test(trimmed));
}

function parseAllowedCommand(commandLine, hostPlatform = process.platform) {
  const trimmed = String(commandLine || "").trim();
  if (!allowedCommandBase(trimmed, hostPlatform)) return null;
  if (hostPlatform === "win32" && /^(dir|type|where|findstr)(\s|$)/i.test(trimmed)) {
    return { file: process.env.ComSpec || "cmd.exe", args: ["/d", "/s", "/c", trimmed], shell: false };
  }
  const parts = splitCommandTokens(trimmed);
  if (!parts.length) return null;
  const file = parts[0].replace(/^"|"$/g, "");
  const args = parts.slice(1).map((part) => part.replace(/^"|"$/g, ""));
  return { file, args, shell: false };
}

module.exports = {
  allowedCommandBase,
  parseAllowedCommand,
  readOnlyGitCommandAllowed,
};
