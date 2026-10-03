import type { GuidanceTopic } from "./design-guidance";
import type { ReferenceSnapshot } from "./reference-context";

export type GuidanceSupplement = Exclude<GuidanceTopic, "core">;
export type GuidanceIntent = Readonly<{ primary: GuidanceTopic; secondary?: GuidanceSupplement; omitted?: boolean; reason: string }>;
const rules: ReadonlyArray<readonly [GuidanceTopic, RegExp]> = [
  ["multiplayer", /멀티플레이|서버\s*권한|(?:상태|변수|서버|네트워크)\s*복제|네트워크\s*예측|\b(?:multiplayer|rpc|replication|netcode|server authority)\b/iu],
  ["design", /구조|의존성|상태\s*소유자|\b(?:ssot|solid|architecture|dependencies|design patterns?)\b/iu],
  ["lifecycle", /수명|생명주기|타이머|이벤트\s*해제|구독\s*해제|늦은\s*(?:결과|콜백)|\b(?:lifecycle|lifetime|dispose|endplay|ondisable|unsubscribe)\b/iu],
  ["debugging", /컴파일\s*오류|빌드\s*오류|(?:API|심볼|타입|함수)\s*(?:정의|선언|계약)|헤더\s*(?:경로|오류)|\b(?:compiler? error|build error|header (?:path|error)|api contract|symbol definition|debug)\b/iu],
  ["code-style", /줄\s*간격|들여쓰기|포맷|가독성|\b(?:formatting|indentation|readability|code style)\b/iu],
];
const action = /구현|수정|고쳐|고치|조사|점검|검토|확인|정리|개선|설계|분석|찾아|찾을|찾아내|찾고|찾기|\b(?:implement|fix|investigate|review|check|refactor|improve|design|analy[sz]e|format|find|audit)\b/iu;

/** Finite text selection only. No tools, model, files, execution state or IO. */
export function selectGuidanceIntent(objective: { text?: string; reason: string }): GuidanceIntent {
  if (objective.text === undefined) return { primary: "core", reason: objective.reason };
  if (objective.text.length > 4000) return { primary: "core", reason: "objective_truncated" };
  let fence: { char: string; length: number } | undefined;
  const text = objective.text.split(/\r?\n/).filter(line => {
    const marker = /^\s*(`{3,}|~{3,})(.*)$/.exec(line);
    if (marker) {
      if (!fence) fence = { char: marker[1][0], length: marker[1].length };
      else if (marker[1][0] === fence.char && marker[1].length >= fence.length && !marker[2].trim()) fence = undefined;
      return false;
    }
    return !fence && !/^\s*(?:>|\[?(?:DEBUG|INFO|ERROR|WARNING)\b|\d{4}-\d\d-\d\d)/i.test(line);
  }).join("\n").replace(/`[^`]*`|"[^"\n]*"|“[^”\n]*”/g, " ")
    .replace(/(?:[A-Za-z]:[\\/]|https?:\/\/|\.{0,2}\/)[^\s]+|\b[\w.-]+\.(?:cpp|h|hpp|cs|json|md|log)\b/gi, " ");
  if (/(?:참고|지침|문서).{0,24}(?:넣지|주입하지|추가하지|포함하지|제외|빼)|(?:no|without|omit|disable)\s+(?:guidance|references)|(?:don't|do not)\s+(?:add|include|inject)\s+(?:guidance|references)/iu.test(text))
    return { primary: "core", omitted: true, reason: "references_excluded" };
  // Complex negation is deliberately conservative: it never chooses the
  // excluded subject because a positive keyword happened to match first.
  if (/하지\s*마|말고|제외|아니라|않|\b(?:not|without|except|don't|ignore)\b/iu.test(text))
    return { primary: "core", reason: "topic_exclusion_ambiguous" };
  const matched = new Set<GuidanceTopic>();
  for (const sentence of text.split(/[.!?\n;]+/)) {
    if (!action.test(sentence)) continue;
    if (/예시|비교|설명|언급|\b(?:example|compare|explain|mention)\b/iu.test(sentence)) continue;
    for (const [topic, pattern] of rules) if (pattern.test(sentence)) matched.add(topic);
  }
  const topics = [...matched];
  if (topics.length === 1) return { primary: topics[0], reason: "explicit_task_topic" };
  // At most two explicit topics; the catalogue still owns dependency closure
  // and exact token admission. More complex requests keep the common fallback.
  if (topics.length === 2) return { primary: topics[0], secondary: topics[1] as GuidanceSupplement,
    reason: "explicit_task_topics" };
  return { primary: "core", reason: topics.length ? "conflicting_topics" : "no_explicit_topic" };
}

export function selectGuidanceTopics(intent: GuidanceIntent, snapshot: ReferenceSnapshot) {
  const observed = (snapshot.autoSignals || []).filter(s => s.order === "last_observation"
    && ["diagnostic_present", "diagnostic_recurred", "compilation_pending", "operation_outcome_unknown"].includes(s.signal));
  const diagnostic = !intent.omitted && intent.primary !== "debugging" && observed.length > 0;
  const supplement = intent.omitted ? undefined : diagnostic ? "debugging" as const : intent.secondary;
  return { primary: intent.primary, supplement, omitted: intent.omitted === true,
    reasons: [intent.reason, ...(diagnostic ? ["returned_diagnostic_or_pending"] : []),
      ...(diagnostic && intent.secondary && intent.secondary !== "debugging" ? ["explicit_secondary_deferred_for_diagnostic"] : [])],
    observationIds: [...new Set(observed.flatMap(s => s.observationIds))].slice(0, 4) };
}
