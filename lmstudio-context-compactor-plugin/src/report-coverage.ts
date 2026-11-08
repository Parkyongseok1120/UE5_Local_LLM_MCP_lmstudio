import type { ObjectiveKind } from "./execution-contracts";
import type { ListingInventory, ReturnedSources } from "./context-ports";
const { isAuditSource } = require("./directory-listing.js") as { isAuditSource(path: string): boolean };

/** Objectives whose delivered report gets the review of open working notes and scope. */
export const REPORT_REVIEW_OBJECTIVES: ReadonlySet<ObjectiveKind> = new Set(["review", "explain"]);

const CONCLUSION_HEADING = /^\s{0,3}#{1,6}\s*.*(?:결론|요약|종합|총평|conclusion|summary)/iu;
// A verdict that nothing (or nothing severe) is wrong. "P0/P1 없음" is a
// severity-limited verdict and is named as such in the notice.
// "문제없이" (without trouble) describes how something ran, not a verdict.
const CLEAN_VERDICT = /(?:결함|문제|이상|이슈)\s*(?:은|이|가|는)?\s*(?:발견되지\s*않|없(?!이))|\bno\s+(?:defects?|issues?|problems?)\s+(?:were\s+)?found\b/iu;
const SEVERITY_LIMITED = /P0\s*\/\s*P1\s*(?:결함|문제)?\s*(?:은|이|가|는)?\s*없/iu;
// The same sentence already limits the verdict to what was read.
const SCOPED = /(?:확인한|읽은|검토한|반환된|열람한)\s*(?:범위|파일|구현)|미확인\s*범위|\bwithin\s+the\s+(?:read|reviewed|returned)\b|\bof\s+the\s+files\s+read\b/iu;
const RETRACTED = /철회|아니(?:다|었|라)|not\s+a\s+(?:bug|defect)|withdraw/iu;

/** The lines that state the report's conclusion: under a conclusion or summary
 * heading, else the last ten prose lines. Code, quotes and tables are not verdicts. */
function conclusionLines(answer: string): string[] {
  const lines: string[] = [], tail: string[] = [];
  let fenced = false, inConclusion = false;
  for (const line of answer.split(/\r?\n/u)) {
    if (/^\s*(```|~~~)/u.test(line)) { fenced = !fenced; continue; }
    if (fenced) continue;
    if (/^\s{0,3}#{1,6}\s/u.test(line)) { inConclusion = CONCLUSION_HEADING.test(line); continue; }
    if (!line.trim() || /^\s*(?:>|\|)/u.test(line)) continue;
    tail.push(line);
    if (inConclusion) lines.push(line);
  }
  return lines.length ? lines : tail.slice(-10);
}

/** Whether the answer already states the unread scope: lines with an
 * unverified marker that together name the unread files, or at least five of
 * them when there are more. Naming one of many does not scope the verdict. */
function namesUnreadScope(answer: string, unread: readonly string[]) {
  const marked = answer.split(/\r?\n/u).filter(line => /미확인|미검증|unverified|not\s+read|읽지\s*않/iu.test(line)).join("\n");
  const named = unread.filter(path => marked.includes(path.split("/").pop()!)).length;
  return named > 0 && named >= Math.min(unread.length, 5);
}

/** Advisory: a clean verdict while this execution's listed sources were not
 * all read, or its listings were incomplete. Never a gate: reading everything
 * is not required, only that the verdict does not claim more than was read. */
export function cleanVerdictScopeGap(answer: string, source: Pick<ReturnedSources, "listed">)
  : { kind: "clean_verdict_scope"; message: string; notice: string } | null {
  const inventory: ListingInventory | null | undefined = source.listed;
  if (!inventory || !answer.trim()) return null;
  const verdicts = conclusionLines(answer).filter(line => (CLEAN_VERDICT.test(line) || SEVERITY_LIMITED.test(line))
    && !SCOPED.test(line) && !RETRACTED.test(line));
  if (!verdicts.length) return null;
  const open = inventory.files.filter(file => isAuditSource(file.path) && file.coverage !== "complete");
  const listedPaths = new Set(inventory.directories.map(directory => directory.path));
  const unlisted = inventory.directories.flatMap(directory => directory.subdirectories
    .map(name => `${directory.path}/${name}`).filter(path => !listedPaths.has(path)));
  const partialListings = inventory.directories.filter(directory => !directory.complete).map(directory => directory.path);
  if (!open.length && !unlisted.length && !partialListings.length && !inventory.overflow) return null;
  if (open.length && !unlisted.length && !partialListings.length && namesUnreadScope(answer, open.map(file => file.path))) return null;
  const state = { unread: "미열람", partial: "일부만 읽음", unknown: "읽음 여부 불명" } as Record<string, string>;
  const shown = open.slice(0, 12).map(file => `${file.path.split("/").slice(-2).join("/")}(${state[file.coverage] || file.coverage})`);
  const parts = [
    open.length ? `목록에 있으나 끝까지 읽지 않은 소스 ${open.length}개: ${shown.join(", ")}${open.length > shown.length ? ` 외 ${open.length - shown.length}개` : ""}` : "",
    unlisted.length ? `열어 보지 않은 하위 디렉터리 ${unlisted.length}개: ${unlisted.slice(0, 6).join(", ")}` : "",
    partialListings.length ? `목록이 일부만 반환된 디렉터리: ${partialListings.slice(0, 6).join(", ")}` : "",
    inventory.overflow ? "목록 기록이 상한을 넘어 일부 파일은 집계되지 않았습니다" : "",
  ].filter(Boolean);
  const verdict = SEVERITY_LIMITED.test(verdicts[0]) ? "P0/P1 없음 결론" : "결함 없음 결론";
  return { kind: "clean_verdict_scope", message: `${verdict}이 읽은 범위를 넘습니다. ${parts.join("; ")}. `
    + "결론을 실제로 읽은 범위로 한정하고 위 항목을 미확인 범위로 적어라.",
    notice: `

[확인 범위] 이 보고의 ${verdict}은 이번 실행에서 읽은 범위에 대한 판단입니다. ${parts.join("; ")}.` };
}
