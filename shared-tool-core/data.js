"use strict";
const { fail, hash, page, bounded } = require("./files");

// Dependencies are injected by the adapter; this module has no engine knowledge.
function dataTools(files, jsonc, Ajv) {
  function jsonDocument(text) {
    const errors = [];
    const data = jsonc.parse(text.replace(/^\uFEFF/, " "), errors, { allowTrailingComma: false, disallowComments: true });
    if (errors.length) fail("invalid_json", "File is not strict JSON");
    const tree = jsonc.parseTree(text.replace(/^\uFEFF/, " "));
    const uniqueKeys = (node, depth = 0) => {
      if (depth > 64) fail("json_depth_exceeded", "JSON nesting exceeds 64 levels");
      if (node.type === "object") {
        const keys = node.children.map(property => property.children[0].value);
        if (new Set(keys).size !== keys.length) fail("duplicate_json_key", "Duplicate object keys are ambiguous");
      }
      for (const child of node.children || []) uniqueKeys(child, depth + 1);
    };
    uniqueKeys(tree);
    const check = value => {
      if (typeof value === "number" && (!Number.isFinite(value) || (Number.isInteger(value) && !Number.isSafeInteger(value)))) fail("number_precision", "Unsafe integer precision; this JSON file is not supported");
      if (value && typeof value === "object") Object.values(value).forEach(check);
    };
    check(data);
    return data;
  }
  function at(root, selector) {
    let value = root;
    for (const key of selector) {
      if (key === "__proto__" || key === "constructor" || key === "prototype") fail("invalid_selector", "Unsafe selector");
      if (value === null || typeof value !== "object" || !Object.hasOwn(value, key)) fail("missing_path", "Selected JSON path does not exist");
      value = value[key];
    }
    return value;
  }
  function limited(value, depth) {
    if (value === null || typeof value !== "object") return { state: "value", value };
    const entries = Object.entries(value);
    return { state: "container", type: Array.isArray(value) ? "array" : "object", count: entries.length,
      ...(depth > 0 && entries.length <= 100 ? { children: entries.map(([key, v]) => ({ key, ...limited(v, depth - 1) })) } : { truncated: entries.length > 0 }) };
  }
  async function schemaCheck(args, data) {
    if (!args.schemaPath) return "not_requested";
    const schema = jsonDocument((await files.snapshot(args.schemaPath)).content);
    const ajv = new Ajv({ allErrors: false, strict: true, coerceTypes: false, useDefaults: false, removeAdditional: false });
    let validate;
    try { validate = ajv.compile(schema); } catch { fail("unsupported_schema", "Schema must be self-contained JSON Schema draft-07 (no remote loading)"); }
    if (!validate(data)) fail("schema_invalid", JSON.stringify(validate.errors));
    return "passed";
  }
  function csvDocument(text, options) {
    if (!options || options.header !== true || ![",", ";", "\t", "|"].includes(options.delimiter)) fail("csv_options_required", "Specify header=true and delimiter; headerless CSV is unavailable");
    const delimiter = options.delimiter;
    const bom = text.startsWith("\uFEFF");
    const rows = [];
    let row = [];
    let i = bom ? 1 : 0;
    let newline = null;
    while (i < text.length) {
      const start = i;
      let value = "";
      const quoted = text[i] === '"';
      if (quoted) {
        i++;
        let closed = false;
        while (i < text.length) {
          if (text[i] === '"') {
            if (text[i + 1] === '"') { value += '"'; i += 2; continue; }
            i++; closed = true; break;
          }
          value += text[i++];
        }
        if (!closed) fail("invalid_csv", "Unclosed quoted field");
      } else {
        while (i < text.length && ![delimiter, "\r", "\n"].includes(text[i])) {
          if (text[i] === '"') fail("invalid_csv", "Quote inside unquoted field");
          value += text[i++];
        }
      }
      row.push({ value, start, end: i, quoted });
      if (i === text.length) { rows.push(row); row = []; break; }
      if (text[i] === delimiter) {
        i++;
        if (i === text.length) { row.push({ value: "", start: i, end: i, quoted: false }); rows.push(row); row = []; }
        continue;
      }
      const nl = text[i] === "\r" && text[i + 1] === "\n" ? "\r\n" : text[i];
      if (!["\r", "\n", "\r\n"].includes(nl)) fail("invalid_csv", "Unexpected characters after quoted field");
      if (newline && newline !== nl) fail("unsupported_csv_format", "Mixed record newlines are not supported");
      newline = nl; i += nl.length; rows.push(row); row = [];
    }
    if (!rows.length) fail("invalid_csv", "Missing header");
    const columns = rows.shift().map(c => c.value);
    if (new Set(columns).size !== columns.length || columns.some(c => !c)) fail("invalid_csv", "Headers must be unique and nonempty");
    if (rows.some(r => r.length !== columns.length)) fail("invalid_csv", "Row width differs from header");
    return { rows, columns, format: { encoding: "utf-8", bom, delimiter, quote: '"', newline, header: true } };
  }
  async function read(args) {
    const s = await files.snapshot(args.path);
    if (args.format === "json") {
      const selected = at(jsonDocument(s.content), args.selector ?? []);
      const entries = selected && typeof selected === "object" ? Object.entries(selected).map(([key, value]) => ({ key, ...limited(value, args.depth ?? 1) })) : [{ ...limited(selected, 0) }];
      return bounded({ status: "observed", receipt: s.receipt, ...page(entries, args, hash(s.hash + JSON.stringify([args.selector, args.depth]))), validation: await schemaCheck(args, jsonDocument(s.content)) }, args.byteBudget);
    }
    if (args.schemaPath) fail("capability_unavailable", "CSV schema validation is not implemented; schemaPath is JSON-only");
    const doc = csvDocument(s.content, args.csv);
    let rows = doc.rows.map((r, index) => ({ index, values: Object.fromEntries(doc.columns.map((c, i) => [c, r[i].value])) }));
    if (args.filter) {
      if (!doc.columns.includes(args.filter.column)) fail("missing_column", "Filter column does not exist");
      rows = rows.filter(r => r.values[args.filter.column] === args.filter.equals);
    }
    return bounded({ status: "observed", receipt: s.receipt, columns: doc.columns, format: doc.format,
      ...page(rows, args, hash(s.hash + JSON.stringify([args.csv, args.filter]))) }, args.byteBudget);
  }
  async function patch(args) {
    return files.mutate(args, async text => {
      if (args.format === "json") {
        jsonDocument(text);
        let output = text;
        for (const change of args.changes) {
          if (!Array.isArray(change.path) || change.path.length === 0) fail("invalid_patch", "Root replacement is not supported");
          if (change.op === "replace" && !Object.hasOwn(change, "value")) fail("invalid_patch", "replace requires an explicit value");
          const current = jsonDocument(output);
          if (change.op === "replace" || change.op === "remove") at(current, change.path);
          else fail("unsupported_patch", "JSON supports replace/remove of explicit existing paths");
          at(current, change.path.slice(0, -1));
          const edits = jsonc.modify(output, change.path, change.op === "remove" ? undefined : change.value, {});
          output = jsonc.applyEdits(output, edits);
        }
        await schemaCheck(args, jsonDocument(output));
        return output;
      }
      if (args.schemaPath) fail("capability_unavailable", "CSV schema validation is not implemented; schemaPath is JSON-only");
      const doc = csvDocument(text, args.csv);
      if (args.csv.duplicateKey !== "error" || args.csv.missingKey !== "error") fail("csv_policy_required", "Specify duplicateKey=error and missingKey=error");
      const keyIndex = doc.columns.indexOf(args.csv.keyColumn);
      if (keyIndex < 0) fail("missing_key_column", "Explicit keyColumn is required");
      const keys = doc.rows.map(row => row[keyIndex].value);
      if (new Set(keys).size !== keys.length) fail("duplicate_key", "Duplicate keys are not modified");
      const replacements = new Map();
      for (const change of args.cells) {
        const row = keys.indexOf(change.key);
        const column = doc.columns.indexOf(change.column);
        if (row < 0 || column < 0) fail("missing_key_or_column", "Exact key or column is absent");
        if (column === keyIndex) fail("key_change_unavailable", "Key column changes are not supported");
        const cell = doc.rows[row][column];
        if (replacements.has(cell.start)) fail("duplicate_patch", "A cell may only be changed once");
        const quote = cell.quoted || change.value.includes(doc.format.delimiter) || /["\r\n]/.test(change.value);
        replacements.set(cell.start, { ...cell, text: quote ? '"' + change.value.replace(/"/g, '""') + '"' : change.value });
      }
      let result = text;
      for (const cell of [...replacements.values()].sort((a, b) => b.start - a.start)) result = result.slice(0, cell.start) + cell.text + result.slice(cell.end);
      csvDocument(result, args.csv);
      return result;
    });
  }
  return { read, patch };
}
module.exports = { dataTools };
