import type { Chat } from "@lmstudio/sdk";
import type { TaskObjective } from "./execution-contracts";
const { exchangeIndex } = require("./working-context.js");
const { decodeToolResultRecord } = require("./compaction-tool-memory.js");

/** What this turn's tool results show was done, apart from what the answer
 * says. "applied" is a functional file change; "built" a successful build
 * after the last change. Neither proves the requested behavior works. */
export type ObjectiveEvidence = {
  level: "none" | "applied" | "built";
  edits: number;
  functionalEdits: number;
  commentOnlyPaths: string[];
  buildAfterLastEdit: "succeeded" | "failed" | "none";
};

const EDIT_TOOLS = new Set(["replace_in_file", "write_file", "apply_edit_bundle", "delete_file"]);
const COMPLETION_CLAIM = /완료|수정했|반영했|적용했|고쳤|구현했|해결했|변경했|\b(?:done|fixed|implemented|applied|completed|resolved)\b/iu;
// A claim that files were changed. "완료" alone also ends text deliverables
// ("작성해" is a change request), so it does not imply a file edit.
const FILE_CHANGE_CLAIM = /수정했|반영했|적용했|고쳤|패치했|변경했|\b(?:fixed|patched|applied|modified|changed|updated)\b/iu;
const BUILD_CLAIM = /빌드\s*(?:성공|완료|통과)|\bbuild\s+(?:succeeded|passed|successful)\b/iu;

/** Code with comments and whitespace removed: two texts equal here differ only in those. */
function codeOnly(text: string): string {
  return String(text || "").replace(/\/\*[\s\S]*?\*\//gu, "").replace(/\/\/[^\n]*/gu, "").replace(/\s+/gu, "");
}

/** Conversation 1794469194476: the spawner's default class was "fixed" by
 * adding the assignment as a comment, and the answer said 수정 완료. */
export function commentOnlyEdit(name: string, args: Record<string, unknown>): boolean {
  const pairs = name === "apply_edit_bundle" && Array.isArray(args.edits) ? args.edits as Array<Record<string, unknown>> : [args];
  return name !== "write_file" && name !== "delete_file" && pairs.length > 0 && pairs.every(edit =>
    typeof edit.oldText === "string" && typeof edit.newText === "string" && edit.oldText !== edit.newText
      && codeOnly(edit.oldText) === codeOnly(edit.newText));
}

export function objectiveEvidence(history: Chat): ObjectiveEvidence {
  const messages = history.getMessagesArray(), index = exchangeIndex(history);
  const start = messages.reduce((last, message, i) => message.isUserMessage() ? i : last, -1);
  const byPath = new Map<string, boolean>();
  let edits = 0, functionalEdits = 0, build: ObjectiveEvidence["buildAfterLastEdit"] = "none";
  for (let mi = Math.max(0, start); mi < messages.length; mi++) {
    for (const [ri, result] of messages[mi].getToolCallResults().entries()) {
      const request = index.matches.get(`${mi}:${ri}`), value = decodeToolResultRecord(result.content).value;
      if (!request) continue;
      if (request.name === "build_unreal_project") { build = value?.ok === true ? "succeeded" : value?.buildSkipped ? build : "failed"; continue; }
      if (!EDIT_TOOLS.has(request.name) || value?.ok !== true) continue;
      const args = (request.arguments || {}) as Record<string, unknown>;
      const commentOnly = commentOnlyEdit(request.name, args);
      edits++; build = "none";
      if (!commentOnly) functionalEdits++;
      const target = String(args.path || "").replace(/^project:\/\//u, "");
      if (target) byPath.set(target, (byPath.get(target) ?? true) && commentOnly);
    }
  }
  return { level: build === "succeeded" && functionalEdits ? "built" : functionalEdits ? "applied" : "none", edits, functionalEdits,
    commentOnlyPaths: [...byPath].filter(([, only]) => only).map(([target]) => target).slice(0, 8), buildAfterLastEdit: build };
}

/** True only where the evidence settles the objective: a build request whose
 * last build, after the last change, succeeded. Otherwise unknown (null). */
export function objectiveSatisfiedBy(objective: TaskObjective, evidence: ObjectiveEvidence): boolean | null {
  return objective.kind === "build" && evidence.buildAfterLastEdit === "succeeded" ? true : null;
}

/** End of a change or build turn: record what the tool results show on the
 * outcome, and return a notice where the answer claims more. */
export function settleObjectiveEvidence(history: Chat, objective: TaskObjective, answer: string,
  owner: { recordObjectiveEvidence(evidence: ObjectiveEvidence, satisfied: boolean | null): void }): string | null {
  if (!objective.requiredEffects.has("mutation")) return null;
  const evidence = objectiveEvidence(history);
  owner.recordObjectiveEvidence(evidence, objectiveSatisfiedBy(objective, evidence));
  return objectiveEvidenceNotice(objective, evidence, answer);
}

/** A completion the answer states that the tool results do not show. */
export function objectiveEvidenceNotice(objective: TaskObjective, evidence: ObjectiveEvidence, answer: string): string | null {
  if (!objective.requiredEffects.has("mutation")) return null;
  const lines: string[] = [];
  const claims = answer.split(/\r?\n/u).filter(line => COMPLETION_CLAIM.test(line));
  if (objective.kind === "mutate" && FILE_CHANGE_CLAIM.test(answer) && !evidence.edits)
    lines.push("- 이번 턴에 적용된 파일 수정이 없습니다. 완료로 확인되지 않았습니다.");
  for (const target of evidence.commentOnlyPaths) {
    const stem = target.split("/").pop()!.replace(/\.[^.]+$/u, "");
    if (claims.some(line => line.includes(stem)) || claims.length && evidence.functionalEdits === 0)
      lines.push(`- ${target}: 주석·공백만 바뀌었습니다. 기능 변경으로 확인되지 않았습니다.`);
  }
  if (BUILD_CLAIM.test(answer) && evidence.edits && evidence.buildAfterLastEdit !== "succeeded")
    lines.push("- 마지막 수정 이후 성공한 빌드가 없습니다.");
  return lines.length ? `\n\n[작업 근거] 답변의 완료 표현과 이번 턴 도구 결과가 다릅니다.\n${lines.join("\n")}` : null;
}
