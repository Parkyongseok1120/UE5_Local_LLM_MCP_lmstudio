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
]);
const MAX_EDITS_PER_FILE = 8;

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

function replaceFileArgumentError(args) {
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
    || (Number.isInteger(args.expectedOccurrences) && args.expectedOccurrences === 1
      ? ""
      : "expectedOccurrences must be the integer 1 for a focused replacement.");
}

module.exports = { MAX_EDITS_PER_FILE, replaceFileArgumentError, writeFileArgumentError };
