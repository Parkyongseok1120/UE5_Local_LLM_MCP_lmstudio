import type { Chat } from "@lmstudio/sdk";
import type { ReturnedSources } from "./context-ports";
import { verificationScopeNotice } from "./delivery-controller";
import { fileCoverageNotice } from "./quote-anchoring";
import { returnedEngineEvidence, unresolvedReportEngineClaimGaps } from "./engine-claim-anchoring";
import { deliveredReportFact, reportReviewFact, type ReviewRegister } from "./review-register";
import { cleanVerdictScopeGap, REPORT_REVIEW_OBJECTIVES } from "./report-coverage";
import type { ObjectiveKind } from "./execution-contracts";
const { visibleAssistantText } = require("./continuity-text.js") as { visibleAssistantText(text: string): string };
const { exchangeIndex } = require("./working-context.js");
const { decodeToolResultRecord } = require("./compaction-tool-memory.js");
type Gap = { kind: string; target?: string; message: string };
/** Build advisory obligations; the execution owner still decides phase and dispatch. */
export function reportReviewObligations(history:Chat,answer:string,source:ReturnedSources,engine:string,objective:string,register:ReviewRegister) {
  const gaps=unresolvedReportEngineClaimGaps(answer,returnedEngineEvidence(source),engine);
  const missing=register.missing(answer),scopeGaps=reportScopeGaps(history,answer,source);
  const reviewed=REPORT_REVIEW_OBJECTIVES.has(objective as ObjectiveKind),coverage=reviewed?cleanVerdictScopeGap(answer,source):null;
  if(coverage)scopeGaps.push(coverage);
  // A delivered report is data rather than the final assistant message. Keeping
  // that message last makes EOS look like an answer that needs no continuation.
  // Report supplements instead begin after the last tool results.
  // What the review was asked to address: its acceptance check keeps any section naming one.
  const subjects=[...missing.map(row=>row.identifier),...gaps.map(g=>g.factId),...scopeGaps.flatMap(g=>g.target?[g.target]:[])];
  return {missing,subjects,needed:Boolean(answer.trim() && (scopeGaps.length || reviewed && (missing.length || gaps.length))),
    reviewFact:reportReviewFact(missing,gaps)+"\n"+reportScopeReviewFact(scopeGaps),deliveredFact:deliveredReportFact(answer),
    telemetry:{missing:missing.map(row=>row.identifier),contracts:gaps.map(g=>g.factId),scopeGaps:scopeGaps.map(g=>g.kind),toolCount:0}};
}
const normalized = (p: string) => p.replace(/^project:\/\//u, "").replace(/\\/gu, "/").toLowerCase();
const ABSENCE_CLAIM = /없(?:다|음|습|는|어|고|으|이|기)|존재하지\s*않|찾을\s*수\s*없|부재|\bnot\s+found\b|\bdoes(?:\s+not|n't)\s+exist\b|\bno\s+(?:existing\s+)?\w+\s+(?:exists?|found|class|manager|implementation)\b|\bmissing\b|\babsent\b/iu;
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
  // Searches this turn that found nothing in part of their scope, and those
  // that settled a term (a hit, or complete coverage).
  const partialMisses = new Set<string>(), settled = new Set<string>();
  for (let mi = Math.max(0, start); mi < messages.length; mi++) {
    for (const [ri, result] of messages[mi].getToolCallResults().entries()) {
      const request = index.matches.get(`${mi}:${ri}`), value = decodeToolResultRecord(result.content).value;
      if (request?.name === "search_files" && value?.ok === true && Array.isArray(value.results)) {
        const term = String(request.arguments?.query || "");
        if (!/^[A-Za-z_][\w:]{3,}$/u.test(term)) continue;
        if (value.results.length || value.coverage === "complete") settled.add(term.toLowerCase());
        else partialMisses.add(term);
        continue;
      }
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
  // Conversation 1794469194476: a search that read none of Source returned
  // coverage=partial with 0 hits, and the report stated the spawner did not
  // exist. An absence claim about a term only such a search looked for is
  // lowered to unverified, unless a later search this turn settled it.
  for (const term of [...partialMisses].filter(term => !settled.has(term.toLowerCase())).slice(0, 2)) {
    const token = term.split("::").pop()!.toLowerCase();
    const claim = answer.split(/\r?\n/u).find(line => line.toLowerCase().includes(token) && ABSENCE_CLAIM.test(line));
    if (claim) gaps.push({ kind: "partial_search_absence", target: term,
      message: `${term}: 이번 턴의 search_files는 범위 일부만 검색했고(coverage=partial) 결과가 0건이다. "${claim.trim().slice(0, 80)}"처럼 부재로 단정하지 말고 미확인으로 낮추거나, 확인한 범위를 밝혀라.` });
  }
  // A prior assistant report is a set of unverified judgments. A follow-up
  // change report should account for them, not silently imply they were fixed.
  const user = messages[start]?.getText() || "";
  if (/수정|고쳐|\bfix\b/iu.test(user)) {
    const prior = messages.slice(0, start).filter(m => m.isAssistantMessage())
      .map(m => visibleAssistantText(m.getText())).filter(t => /\bP1\b/u.test(t)).at(-1) || "";
    let high = false, numbered = false;
    for (const line of prior.split(/\r?\n/u)) {
      const heading = /^\s*(?:#{1,6}\s+|[-*]\s+)?(?:\*\*)?(P1-\d+)\b\s*[:.)-]?\s*(.+?)(?:\*\*)?\s*$/iu.exec(line);
      if (heading) {
        numbered = true;
        const [, id, title] = heading;
        const accounted = answer.split(/\r?\n/u).some(row => row.includes(id) && /해결|수정|기각|보류|범위 밖|미확인|남음|완료|fixed|deferred|rejected|unverified/iu.test(row));
        if (!accounted) gaps.push({ kind: "prior_item_disposition", target: id,
          message: id + " " + title.slice(0, 120) + "의 처리 상태와 근거를 밝히거나 미확인으로 남겨라." });
        continue;
      }
      if (/^#{1,2}\s|^\s*\*\*P[0-3]\b/u.test(line)) numbered = false;
      if (numbered) continue;
      // Sections: "## P1 ..." or a bold-only line. Items: "### ...", a
      // top-level list item or a bold-led line (the conversation above wrote
      // its P1 items as bold bullets). Nested evidence bullets are not items.
      if (/^#{1,2}\s|^\s{0,3}\*\*[^*]+\*\*\s*:?\s*$/u.test(line)) { high = /\bP1\b/u.test(line); continue; }
      const item = /^###+\s|^(?:[-*]|\d+[.)])\s|^\*\*/u.test(line);
      if (!item || !(high || /\bP1\b/u.test(line))) continue;
      const targets = [...line.matchAll(/`([^`]+)`/gu)].map(m => m[1]).filter(t => /^[A-Za-z_]\w*(?:::\w+)?$/u.test(t)).slice(0, 2);
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
