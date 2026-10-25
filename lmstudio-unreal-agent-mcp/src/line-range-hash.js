"use strict";

const { sha256Text } = require("../../shared-tool-core/direct-file-snapshot");

// The hash a line-range edit proves: lines startLine..endLine (1-based, as
// read_file_range splits them) joined by "\n", without the last terminator.
// Terminator style does not change it, so a range read on CRLF and LF copies
// of a file proves the same text.
function lineRangeSha256(lines, startLine, endLine) {
  return sha256Text(lines.slice(startLine - 1, endLine).join("\n"));
}

module.exports = { lineRangeSha256 };
