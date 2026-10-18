"use strict";
const fs = require("node:fs"), path = require("node:path");

// Policies decide access; helpers never widen a root or follow links.
function nearestExistingDirectory(root, relative, isAllowed) {
  let candidate = path.posix.dirname(String(relative).replace(/\\/g, "/"));
  while (candidate !== "." && candidate !== "/") {
    try {
      if (isAllowed(candidate)) {
        const stat = fs.lstatSync(path.join(root, candidate));
        if (stat.isDirectory() && !stat.isSymbolicLink()) return candidate;
      }
    } catch { /* try its parent */ }
    candidate = path.posix.dirname(candidate);
  }
  return null;
}
function fileNameSearchSuggestion(directory, basename, toolName = "search_files") {
  return directory && basename ? { tool: toolName, args: { path: directory, query: basename, matchFileNames: true } } : null;
}
function budgetFields(byteBudget, cutBy) {
  return { effectiveByteBudget: byteBudget, budgetScope: "whole_response", ...(cutBy ? { omissionReason: cutBy } : {}) };
}
function fitPage(items, render, budget) {
  let low = 1, high = items.length, best = null;
  while (low <= high) {
    const count = Math.floor((low + high) / 2), value = render(count);
    if (Buffer.byteLength(JSON.stringify(value)) <= budget) { best = value; low = count + 1; }
    else high = count - 1;
  }
  return best;
}
module.exports = { nearestExistingDirectory, fileNameSearchSuggestion, budgetFields, fitPage };
