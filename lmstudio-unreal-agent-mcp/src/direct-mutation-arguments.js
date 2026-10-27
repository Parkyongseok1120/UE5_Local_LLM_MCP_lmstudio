"use strict";

// Argument shape checks for write_file and replace_in_file, before any IO.

const WRITE_FILE_FIELDS = new Set(["path", "project", "content", "createDirs"]);
const REPLACE_FILE_FIELDS = new Set([
  "path",
  "project",
  "oldText",
  "newText",
  "expectedOccurrences",
  "expectedHash",
  "fileVersionReceipt",
  "edits",
  "startLine",
  "endLine",
  "expectedRangeSha256",
]);
const MAX_EDITS_PER_FILE = 8;
const RANGE_FIELDS = ["startLine", "endLine", "expectedRangeSha256"];

function mutationArgumentError(args, allowedFields, label) {
  if (!args || typeof args !== "object" || Array.isArray(args)) {
    return `${label} arguments must be an object.`;
  }
  const unsupported = Object.keys(args).find((key) => !allowedFields.has(key));
  return unsupported ? `${label} contains unsupported field: ${unsupported}` : "";
}

function requiredStringError(args, field, { allowEmpty = false } = {}) {
  if (typeof args[field] !== "string") return `${field} must be a string.`;
  if (!allowEmpty && !args[field].trim()) return `${field} must be a non-empty string.`;
  return "";
}

function optionalStringError(args, field) {
  return args[field] === undefined || typeof args[field] === "string"
    ? ""
    : `${field} must be a string when provided.`;
}

function writeFileArgumentError(args) {
  return mutationArgumentError(args, WRITE_FILE_FIELDS, "write_file")
    || requiredStringError(args, "path")
    || requiredStringError(args, "content", { allowEmpty: true })
    || optionalStringError(args, "project")
    || (args.createDirs === undefined || typeof args.createDirs === "boolean"
      ? ""
      : "createDirs must be a boolean when provided.");
}

// edits[]: several exact regions of one file in one call. It replaces, never
// accompanies, the single oldText/newText/expectedOccurrences form.
function editsArgumentError(args) {
  if (["oldText", "newText", "expectedOccurrences"].some((field) => args[field] !== undefined)) {
    return "Use either edits[] or oldText/newText/expectedOccurrences, not both.";
  }
  if (!Array.isArray(args.edits) || args.edits.length < 1 || args.edits.length > MAX_EDITS_PER_FILE) {
    return `edits must be an array of 1-${MAX_EDITS_PER_FILE} {oldText, newText} regions.`;
  }
  for (const [index, edit] of args.edits.entries()) {
    const error = mutationArgumentError(edit, new Set(["oldText", "newText"]), `edits[${index}]`)
      || requiredStringError(edit, "oldText") || requiredStringError(edit, "newText", { allowEmpty: true });
    if (error) return error.startsWith(`edits[${index}]`) ? error : `edits[${index}].${error}`;
  }
  return "";
}

// {startLine, endLine, expectedRangeSha256, newText}: whole lines proven by
// their range hash. It replaces, never accompanies, oldText and edits[].
function rangeArgumentError(args) {
  if (["oldText", "expectedOccurrences", "edits"].some((field) => args[field] !== undefined)) {
    return "Use either startLine/endLine/expectedRangeSha256 or oldText/edits[], not both.";
  }
  const missing = RANGE_FIELDS.find((field) => args[field] === undefined);
  if (missing) return `${missing} is required for a line-range replacement.`;
  if (!Number.isInteger(args.startLine) || args.startLine < 1) return "startLine must be an integer >= 1.";
  if (!Number.isInteger(args.endLine) || args.endLine < args.startLine) return "endLine must be an integer >= startLine.";
  if (typeof args.expectedRangeSha256 !== "string" || !/^[A-Fa-f0-9]{64}$/u.test(args.expectedRangeSha256)) {
    return "expectedRangeSha256 must be the 64-hex rangeSha256 returned by read_file_range or read_symbol.";
  }
  return requiredStringError(args, "newText", { allowEmpty: true });
}

function replaceFileArgumentError(args) {
  if (args && typeof args === "object" && !Array.isArray(args) && RANGE_FIELDS.some((field) => args[field] !== undefined)) {
    return mutationArgumentError(args, REPLACE_FILE_FIELDS, "replace_in_file")
      || requiredStringError(args, "path")
      || optionalStringError(args, "project")
      || optionalStringError(args, "expectedHash")
      || optionalStringError(args, "fileVersionReceipt")
      || rangeArgumentError(args);
  }
  if (args && typeof args === "object" && !Array.isArray(args) && args.edits !== undefined) {
    return mutationArgumentError(args, REPLACE_FILE_FIELDS, "replace_in_file")
      || requiredStringError(args, "path")
      || optionalStringError(args, "project")
      || optionalStringError(args, "expectedHash")
      || optionalStringError(args, "fileVersionReceipt")
      || editsArgumentError(args);
  }
  return mutationArgumentError(args, REPLACE_FILE_FIELDS, "replace_in_file")
    || requiredStringError(args, "path")
    || requiredStringError(args, "oldText")
    || requiredStringError(args, "newText", { allowEmpty: true })
    || optionalStringError(args, "project")
    || optionalStringError(args, "expectedHash")
    || optionalStringError(args, "fileVersionReceipt")
    || normalizeExpectedOccurrences(args.expectedOccurrences).error || "";
}

/** Omitted means one occurrence; explicit values are never coerced. */
function normalizeExpectedOccurrences(value) {
  if (value === undefined) return { value: 1 };
  return Number.isInteger(value) && value === 1 ? { value: 1 }
    : { error: "expectedOccurrences must be the integer 1 for a focused replacement (omit it to use 1)." };
}

module.exports = { MAX_EDITS_PER_FILE, replaceFileArgumentError, writeFileArgumentError, normalizeExpectedOccurrences };
