import type { ObjectiveKind } from "./execution-contracts";
import type { ListingInventory, ReturnedSources } from "./context-ports";
const { isAuditSource } = require("./directory-listing.js") as { isAuditSource(path: string): boolean };

/** Objectives whose delivered report gets the review of open working notes and scope. */
export const REPORT_REVIEW_OBJECTIVES: ReadonlySet<ObjectiveKind> = new Set(["review", "explain"]);

// Inventory can contradict an explicit reading-history claim, but it cannot
// decide whether the required functions/headers suffice for an inference.
const FULL_READ = /(?:전량|전부|모두|끝까지).{0,18}(?:확인했|확인하였|읽었|열람했|검토했|완독했)|(?:전량|전부)\s*(?:소스\s*)?확인\s*(?:[)\].,]|$)|\b(?:all|every)\s+(?:source\s+)?files?\s+(?:(?:were|was|have\s+been|has\s+been)\s+)?(?:read|inspected)\s+(?:in\s+full|entirely|completely)\b/iu;
const WHOLE_SOURCE_SCOPE = /(?:전체|모든)\s*(?:소스|파일|코드)|(?:소스|파일|코드).{0,100}전량|전량\s*(?:소스|파일|코드)|\b(?:all|every)\s+(?:source\s+)?files?\b/iu;
const RETRACTED = /철회|아니(?:다|었|라)|withdraw/iu;
const READING_LIMIT = /예정|계획|필요|해야|아니|않|\b(?:not|never|must|should|will|would|if|unless)\b|didn't/iu;

function readingClaims(answer: string): string[] {
  let fenced = false;
  return answer.split(/\r?\n/u).filter(line => {
    if (/^\s*(?:```|~~~)/u.test(line)) { fenced = !fenced; return false; }
    return !fenced && !/^\s*>/u.test(line) && WHOLE_SOURCE_SCOPE.test(line) && FULL_READ.test(line)
      && !READING_LIMIT.test(line) && !RETRACTED.test(line);
  });
}

/** Whether the answer already states the unread scope: lines with an
 * unverified marker that together name the unread files, or at least five of
 * them when there are more. Naming one of many does not scope the verdict. */
function namesUnreadScope(answer: string, unread: readonly string[]) {
  const marked = answer.split(/\r?\n/u).filter(line => /미확인|미검증|unverified|not\s+read|읽지\s*않/iu.test(line)).join("\n");
  const named = unread.filter(path => marked.includes(path.split("/").pop()!)).length;
  return named > 0 && named >= Math.min(unread.length, 5);
}

/** Check explicit full-reading claims only. Unread inventory is neither a
 * defect nor evidence that a function/class-level review was insufficient. */
export function cleanVerdictScopeGap(answer: string, source: Pick<ReturnedSources, "listed">)
  : { kind: "clean_verdict_scope"; message: string; notice: string } | null {
  const inventory: ListingInventory | null | undefined = source.listed;
  if (!inventory || !answer.trim()) return null;
  const claims = readingClaims(answer);
  if (!claims.length) return null;
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
  return { kind: "clean_verdict_scope", message: `소스를 전량 읽었다는 수행 이력은 반환 기록과 맞지 않습니다. ${parts.join("; ")}. `
    + "전량 확인 표현을 실제 조사 방법으로 정정하라. 필요한 함수·클래스·헤더·정의와 관계를 확인한 추론은 허용하며, 미완독 자체는 결함이나 추가 읽기 의무가 아니다.",
    notice: `

[확인 범위] 전량 읽기 주장은 반환 기록으로 뒷받침되지 않습니다. ${parts.join("; ")}. 필요한 정의와 관계에 근거한 추론은 가능하며, 미완독 수만으로 결론의 정확성을 판정하지 않습니다.` };
}
