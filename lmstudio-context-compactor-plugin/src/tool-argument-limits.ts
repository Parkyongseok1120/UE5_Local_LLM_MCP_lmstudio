// A parsed tool request that its schema rejects never reaches the provider, so
// the provider's own limit message (which names the limit and the next step)
// is never seen. Conversation 1794419304890 got only "params.oldText does not
// meet maximum length of 1200" for a 1,235-char oldText whose newText differed
// from it on one line. This names the field, the allowed length and the lines
// that actually differ, compared against the tool's own schema, not SDK wording.
type JsonSchema = {
  maxLength?: unknown;
  properties?: Record<string, JsonSchema>;
  items?: JsonSchema;
  const?: unknown;
  anyOf?: JsonSchema[];
  oneOf?: JsonSchema[];
  required?: string[];
};
type ToolWithSchema = { name: string; parametersJsonSchema?: unknown };

export type LengthViolation = { field: string; actual: number; max: number; parent: Record<string, unknown>; schema: JsonSchema };

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

/** Strings longer than their schema's maxLength, in nested objects and arrays too. */
export function lengthViolations(schema: JsonSchema | undefined, value: unknown, field = "", depth = 0): LengthViolation[] {
  if (!schema || depth > 6) return [];
  const found: LengthViolation[] = [];
  if (isRecord(value) && isRecord(schema.properties)) {
    for (const [key, child] of Object.entries(schema.properties)) {
      const nested = value[key];
      const name = field ? `${field}.${key}` : key;
      if (typeof nested === "string" && typeof child?.maxLength === "number" && nested.length > child.maxLength)
        found.push({ field: name, actual: nested.length, max: child.maxLength, parent: value, schema });
      else found.push(...lengthViolations(child, nested, name, depth + 1));
    }
  } else if (Array.isArray(value) && schema.items) {
    value.forEach((item, index) => found.push(...lengthViolations(schema.items, item, `${field}[${index}]`, depth + 1)));
  }
  return found;
}

/** Lines of oldText that differ from newText, after their shared first and last lines. */
export function changedLineSummary(oldText: string, newText: string) {
  const oldLines = oldText.replace(/\r\n/g, "\n").split("\n"), newLines = newText.replace(/\r\n/g, "\n").split("\n");
  let prefix = 0;
  while (prefix < oldLines.length && prefix < newLines.length && oldLines[prefix] === newLines[prefix]) prefix++;
  let suffix = 0;
  while (suffix < oldLines.length - prefix && suffix < newLines.length - prefix
    && oldLines[oldLines.length - 1 - suffix] === newLines[newLines.length - 1 - suffix]) suffix++;
  return {
    oldLines: oldLines.length, newLines: newLines.length,
    changedOld: [prefix + 1, oldLines.length - suffix] as const,
    changedNew: [prefix + 1, newLines.length - suffix] as const,
  };
}

function span([first, last]: readonly [number, number]) {
  return last < first ? "no lines" : first === last ? `line ${first}` : `lines ${first}-${last}`;
}

export function describeArgumentLengthRejection(
  error: { message?: string },
  request: { name?: string; arguments?: unknown },
  tools: ReadonlyArray<unknown>,
): string | undefined {
  const tool = tools.find(candidate => (candidate as ToolWithSchema)?.name === request.name) as ToolWithSchema | undefined;
  const schema = tool?.parametersJsonSchema as JsonSchema | undefined;
  const violations = lengthViolations(schema, request.arguments);
  if (!violations.length) {
    const args = request.arguments;
    if (!isRecord(args)) return undefined;
    const branches = schema?.anyOf ?? schema?.oneOf;
    if (!Array.isArray(branches)) return undefined;
    const compatible = branches.filter(branch => Object.entries(branch.properties || {}).every(([key, child]) =>
      !("const" in child) || !(key in args) || args[key] === child.const));
    const missing = compatible.map(branch => (branch.required || []).filter(key => !(key in args)));
    if (!missing.length || missing.some(keys => keys.length === 0)) return undefined;
    const choices = missing.map(keys => keys.join(" + "));
    return request.name + " was not run. Supply one of the missing selector alternatives: " +
      [...new Set(choices)].join(" or ") + ". " + (request.name === "evidence_first_read_context" ? "Use an identifier and version from the same catalog entry. " : "") +
      String(error.message || "").slice(0, 200);
  }
  const first = violations[0];
  const parts = [`${request.name} was not run: ${violations.map(v => `${v.field} is ${v.actual} characters; the tool schema allows ${v.max}`).join("; ")}.`];
  const { oldText, newText } = first.parent;
  if (typeof oldText === "string" && typeof newText === "string") {
    const summary = changedLineSummary(oldText, newText);
    parts.push(`Only oldText ${span(summary.changedOld)} (of ${summary.oldLines}) differ from newText ${span(summary.changedNew)} (of ${summary.newLines}); send just the lines that change, with enough unchanged context to match once.`);
    const siblings = first.schema.properties || {};
    if (siblings.startLine && siblings.expectedRangeSha256)
      parts.push("Or replace those file lines by range (startLine/endLine with the rangeSha256 of a read covering them), which needs no oldText.");
  }
  parts.push(`(Schema check: ${String(error.message || "").slice(0, 200)})`);
  return parts.join(" ");
}
