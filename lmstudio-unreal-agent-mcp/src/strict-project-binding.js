"use strict";

const { canonicalAbsolutePathIdentity } = require("./filesystem-path-identity.js");

const PROJECT_SELECTOR_FIELDS = ["project", "projectPath", "hint"];

function createStrictProjectBinding(directRuntime) {
  const definitions = new Map(
    (directRuntime?.tools || []).map((definition) => [definition.name, definition]),
  );

  async function resolveProject(selector) {
    if (typeof directRuntime?.resolveProject !== "function") {
      throw new Error("Strict project binding requires the Direct project resolver");
    }
    const resolved = await directRuntime.resolveProject(selector);
    if (!resolved) throw new Error(`Could not resolve Strict project selector: ${selector}`);
    return resolved;
  }

  function sameProject(left, right) {
    const first = canonicalAbsolutePathIdentity(left);
    const second = canonicalAbsolutePathIdentity(right);
    return Boolean(first && second && first === second);
  }

  async function canonicalizeBeginArgs(args) {
    const selector = String(args.project || "").trim();
    if (!selector) return args;
    return { ...args, project: await resolveProject(selector) };
  }

  async function bindToolArguments(toolName, args, boundProject) {
    if (!boundProject) return args;
    const properties = definitions.get(toolName)?.inputSchema?.properties || {};
    const selectorFields = PROJECT_SELECTOR_FIELDS.filter((field) => (
      Object.hasOwn(properties, field)
    ));
    if (Object.hasOwn(properties, "clear") && args.clear === true) {
      throw new Error("Strict project switch differs from the project bound at strict_begin");
    }
    const suppliedSelectors = selectorFields.filter((field) => (
      String(args[field] || "").trim()
    ));
    if (Object.hasOwn(properties, "projectPath") && suppliedSelectors.length === 0) {
      throw new Error("Strict project switch differs from the project bound at strict_begin");
    }
    for (const field of selectorFields) {
      const selector = String(args[field] || "").trim();
      if (!selector) continue;
      const resolved = await resolveProject(selector);
      if (!sameProject(resolved, boundProject)) {
        throw new Error("Strict tool project differs from the project bound at strict_begin");
      }
    }
    for (const field of selectorFields) delete args[field];
    delete args.clear;
    const canonicalField = selectorFields.includes("project")
      ? "project"
      : selectorFields.includes("projectPath") ? "projectPath" : null;
    if (canonicalField) args[canonicalField] = boundProject;
    return args;
  }

  return { bindToolArguments, canonicalizeBeginArgs };
}

module.exports = { createStrictProjectBinding };
