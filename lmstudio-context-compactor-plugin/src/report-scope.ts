import type { Chat } from "@lmstudio/sdk";
import type { ReturnedSources } from "./context-ports";
import { verificationScopeNotice } from "./delivery-controller";
import { fileCoverageNotice } from "./quote-anchoring";
const { exchangeIndex } = require("./working-context.js");
const { decodeToolResultRecord } = require("./compaction-tool-memory.js");
type Gap = { kind: string; target?: string; message: string };
const normalized = (p: string) => p.replace(/^project:\/\//u, "").replace(/\\/gu, "/").toLowerCase();
/** Advisory obligations, never source truth or a new research gate. Only
 * completed, paired inventory and returned header declarations can name an
 * unread implementation; a directory entry is not implementation evidence. */
export function reportScopeGaps(history: Chat, answer: string, source: ReturnedSources): Gap[] {
  const gaps: Gap[] = [];
  const verification = verificationScopeNotice(history, answer), coverage = fileCoverageNotice(source, answer);
  if (verification) gaps.push({ kind: "verification_scope", message: verification });
  if (coverage) gaps.push({ kind: "file_scope", message: coverage });
  const messages = history.getMessagesArray(), index = exchangeIndex(history);
  const start = messages.reduce((last, message, i) => message.isUserMessage() ? i : last, -1);
  const delivered = new Set(source.files.filter(f => f.bodyReturned).map(f => normalized(f.path)));
  for (let mi = Math.max(0, start); mi < messages.length; mi++) {
    for (const [ri, result] of messages[mi].getToolCallResults().entries()) {
      const request = index.matches.get(`${mi}:${ri}`), value = decodeToolResultRecord(result.content).value;
      if (request?.name !== "list_directory" || value?.ok !== true || value.truncated !== false || !Array.isArray(value.entries)) continue;
      for (const entry of value.entries) {
        if (entry.type !== "file" || typeof entry.name !== "string" || !/\.(?:cpp|cs)$/iu.test(entry.name) || /[\\/]/u.test(entry.name)) continue;
        const path = String(value.path || request.arguments?.path || "").replace(/[\\/]$/u, "") + "/" + entry.name;
        if ([...delivered].some(p => p === normalized(path) || normalized(path).endsWith("/" + p))) continue;
        const stem = entry.name.replace(/\.[^.]+$/u, "");
        const header = source.files.find(f => normalized(f.path).endsWith("/" + stem.toLowerCase() + ".h"));
        if (!header) continue;
        const declarations = [...header.lines.values()].join("\n");
        const symbols = [...declarations.matchAll(/\b([A-Za-z_]\w*)\s*\(/gu)].map(m => m[1]);
        if (answer.split(/\r?\n/u).some(line => /구현\s*없|구현(?:이|은)?\s*(?:부재|없)|구현\s*없음|\bno\s+implementation\b/iu.test(line)
          && symbols.some(symbol => line.includes(symbol))))
          gaps.push({ kind: "implementation_scope", target: path,
            message: `${path}: 목록에 있으나 구현 본문을 반환하지 않았다. 헤더 선언만으로 구현 부재를 확정하지 말고 이 지적을 미확인으로 낮춰라.` });
      }
    }
  }
  // A prior assistant report is a set of unverified judgments. A follow-up
  // change report should account for them, not silently imply they were fixed.
  const user = messages[start]?.getText() || "";
  if (/수정|고쳐|\bfix\b/iu.test(user)) {
    const prior = messages.slice(0, start).filter(m => m.isAssistantMessage() && /\bP1\b/u.test(m.getText())).at(-1)?.getText() || "";
    let high = false;
    for (const line of prior.split(/\r?\n/u)) {
      if (/^##\s/u.test(line)) high = /\bP1\b/u.test(line);
      if (!high || !/^###\s/u.test(line)) continue;
      const targets = [...line.matchAll(/`([^`]+)`/gu)].map(m => m[1]).filter(t => /^[A-Za-z_]\w*(?:::\w+)?$/u.test(t));
      for (const target of targets) if (!answer.includes(target)) gaps.push({ kind: "prior_item_disposition", target,
        message: `${target}: 이전 assistant 지적은 미검증 판단이다. 해결·기각·보류·이번 범위 밖 중 처리 상태를 밝히고, 새 근거 없이는 해결됐다고 하지 말라.` });
    }
  }
  return gaps.slice(0, 8);
}
export function reportScopeReviewFact(gaps: readonly Gap[]): string {
  return gaps.length ? "Runtime evidence observation: report scope corrections (not factual validation). Write only a ## 검토 보완 supplement, no tools or new changes.\n"
    + gaps.map(g => "- " + g.message).join("\n") : "";
}
