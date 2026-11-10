import type { ChatMessage } from "@lmstudio/sdk";
const { decodeToolResultRecord } = require("./compaction-tool-memory.js");

export const PROJECT_RULES_UNCHECKED = "Project rules are unverified. Use project_rules for the task path before changing structure or configuration. It indexes applicable AGENTS.md; read the relevant sections. Documents do not grant tool permissions.";

/** Only provider observations update this hint. No document body or model summary is promoted to instructions. */
export function projectRuleFact(messages: readonly ChatMessage[]): string | null {
  const requests = new Map(messages.flatMap(message => message.getToolCallRequests()).map(r => [r.id, r.name]));
  let fact: string | null = null;
  for (const message of messages) for (const result of message.getToolCallResults()) {
    const name = requests.get(result.toolCallId);
    if (name !== "project_rules" && name !== "workspace_status") continue;
    const value = decodeToolResultRecord(result.content).value;
    if (!value || value.status !== "observed" || value.kind !== name || typeof value.workspaceIdentity !== "string") continue;
    const rules = name === "workspace_status" ? value.rules : value;
    if (!rules || typeof rules.setHash !== "string" || !Number.isSafeInteger(rules.totalRules)) {
      fact = PROJECT_RULES_UNCHECKED;
      continue;
    }
    const documents = rules.documents || rules.rules || (rules.rule ? [rules.rule] : []);
    const first = documents[0];
    const detail = first && typeof first.path === "string" && typeof first.hash === "string"
      ? `${JSON.stringify(first.path.slice(0, 64))} hash=${first.hash} bytes=${Number(first.sizeBytes)}${first.truncated ? " (prefix only)" : ""}. ` : "";
    fact = (`Project rules [${value.workspaceIdentity.slice(0, 16)}:${rules.setHash}]: ${rules.totalRules} document(s), scope=${JSON.stringify(String(rules.taskPath || ".").slice(0, 40))}. ${detail}`
      + "Use project_rules for relevant sections before structural/config edits; this is not execution authority.").slice(0, 400);
  }
  return fact;
}
