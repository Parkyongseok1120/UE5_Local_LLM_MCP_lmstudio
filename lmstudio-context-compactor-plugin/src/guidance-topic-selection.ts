import type { GuidanceTopic } from "./design-guidance";
import type { ReferenceSnapshot } from "./reference-context";
import { pathApiFor, projectRoot } from "./continuity-file-observations.js";
import { objectiveContinuity, continuityState } from "./context-ports";
import type { NormalizedMessage } from "./execution-contracts";

export type GuidanceSupplement = Exclude<GuidanceTopic, "core">;
export type GuidanceIntent = Readonly<{ primary: GuidanceTopic; secondary?: GuidanceSupplement; omitted?: boolean; reason: string }>;
const rules: ReadonlyArray<readonly [GuidanceTopic, RegExp]> = [
  ["multiplayer", /멀티플레이|네트워크|리플리케이션|레플리케이션|서버\s*권한|(?:상태|변수|서버)\s*복제|\b(?:multiplayer|rpcs?|replication|netcode|networking|networked|netmulticast|repnotify|onrep\w*|server authority)\b/iu],
  ["design", /구조|의존성|상태\s*소유자|\b(?:ssot|solid|architecture|dependencies|design patterns?)\b/iu],
  ["lifecycle", /수명|생명주기|타이머|이벤트\s*해제|구독\s*해제|늦은\s*(?:결과|콜백)|\b(?:lifecycle|lifetime|dispose|endplay|ondisable|unsubscribe)\b/iu],
  ["debugging", /컴파일\s*오류|빌드\s*오류|매크로|델리게이트|(?:심볼|타입|함수)\s*(?:정의|선언|계약)|헤더\s*(?:경로|오류)|\b(?:api|include|uproperty|ufunction|forceinline|delegate|enhanced input|serializefield|compiler? error|build error|header (?:path|error)|symbol definition|debug)\b/iu],
  ["code-style", /줄\s*간격|들여쓰기|포맷|가독성|\b(?:formatting|indentation|readability|code style)\b/iu],
];
const action = /구현|수정|고쳐|고치|조사|점검|검토|확인|정리|개선|설계|분석|찾아|찾을|찾아내|찾고|찾기|\b(?:implement|fix|investigate|review|check|refactor|improve|design|analy[sz]e|format|find|audit)\b/iu;

function userProse(source: string, keepLiteral?: (value: string) => boolean): string {
  let fence: { char: string; length: number } | undefined;
  return source.split(/\r?\n/).filter(line => {
    const marker = /^\s*(`{3,}|~{3,})(.*)$/.exec(line);
    if (marker) {
      if (!fence) fence = { char: marker[1][0], length: marker[1].length };
      else if (marker[1][0] === fence.char && marker[1].length >= fence.length && !marker[2].trim()) fence = undefined;
      return false;
    }
    return !fence && !/^\s*(?:>|\[?(?:DEBUG|INFO|ERROR|WARNING)\b|\d{4}-\d\d-\d\d)/i.test(line);
  }).join("\n").replace(/`[^`]*`|"[^"\n]*"|“[^”\n]*”/g, token =>
    keepLiteral?.(token.slice(1, -1)) ? token.slice(1, -1) : " ");
}

/** Finite text selection only. No tools, model, files, execution state or IO. */
export function selectGuidanceIntent(objective: { text?: string; reason: string }): GuidanceIntent {
  if (objective.text === undefined) return { primary: "core", reason: objective.reason };
  if (objective.text.length > 4000) return { primary: "core", reason: "objective_truncated" };
  let text = userProse(objective.text).replace(/(?:[A-Za-z]:[\\/]|https?:\/\/|\.{0,2}\/)[^\s]+|\b[\w.-]+\.(?:cpp|h|hpp|cs|json|md|log)\b/gi, " ");
  if (/(?:참고|지침|문서).{0,24}(?:넣지|주입하지|추가하지|포함하지|제외|빼)|(?:no|without|omit|disable)\s+(?:guidance|references)|(?:don't|do not)\s+(?:add|include|inject)\s+(?:guidance|references)/iu.test(text))
    return { primary: "core", omitted: true, reason: "references_excluded" };
  // These narrow clauses constrain editing; they do not exclude an API topic.
  text = text.replace(/(?:코드\s*)?수정하지\s*말고|기존\s*동작[은을]?\s*바꾸지\s*않고|(?:do not|don't)\s+edit\s+(?:the\s+)?code[.,]?/giu, " ");
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
  // At most two explicit topics, taken in the order above (sentence, then rule
  // order). The catalogue still owns dependency closure and exact admission.
  if (topics.length >= 2) return { primary: topics[0], secondary: topics[1] as GuidanceSupplement,
    reason: topics.length === 2 ? "explicit_task_topics" : "explicit_task_topics_limited" };
  return { primary: "core", reason: "no_explicit_topic" };
}

/** A follow-up that names no topic of its own ("Die 없어…") keeps the topics
 * of the previous user request. Exclusions, omissions and an explicit topic in
 * the latest request always win; only an explicit earlier topic is carried. */
export function selectFollowUpGuidanceIntent(latest: { text?: string; reason: string },
  previous?: { text?: string; reason: string }): GuidanceIntent {
  const current = selectGuidanceIntent(latest);
  if (current.reason !== "no_explicit_topic" || !previous?.text) return current;
  const earlier = selectGuidanceIntent(previous);
  return earlier.reason.startsWith("explicit_task_topic") ? { ...earlier, reason: "previous_request_topic" } : current;
}

const projectReference = /(?:해당|현재|기존|이|우리)\s*(?:프로젝트|코드베이스)|\b(?:this|our|current|existing) (?:project|codebase)\b/iu;
const designSubject = /설계|상속|구조|컴포넌트|부모\s*클래스|자식\s*클래스|데이터\s*에셋|\b(?:architecture|design|inheritance|composition|components?|subclass(?:es)?|ownership|data assets?)\b/iu;
const designQuestion = /잘못|문제|짚어|검토|평가|비교|대안|(?:맞|좋)을까|(?:방식|설계).{0,30}(?:아니|맞|어때)|\b(?:review|compare|assess|alternative|problem|instead|should|why)\b/iu;
const continuation = /아까|방금|앞서|그렇다면|그렇게|대신|(?:이전|직전)\s*(?:답|판단)|\b(?:earlier|previous|you (?:said|suggested)|that approach|in that case|instead)\b/iu;
const scopeSwitch = /(?:다른|새로운|새)\s*(?:프로젝트|주제)|\b(?:another|different|new) (?:project|topic)\b/iu;

/** Bounded to the active branch's user turns. A past anchor needs an explicit
 * matching path: a deictic reference alone cannot attest its past workspace. */
export function projectDesignTurnsBack(turns: readonly string[], projectIdentity: string): number | undefined {
  if (!projectIdentity) return;
  const api = pathApiFor(projectIdentity);
  const targets = [projectIdentity, projectRoot(projectIdentity)].map(p => api.normalize(p).replaceAll("\\", "/"));
  const alternatives = targets.map(p => p.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")).join("|");
  const projectPath = new RegExp("(?:^|[\\s(])(?:" + alternatives + ")/?(?=$|[\\s),;:!?]|\\.(?:\\s|$))",
    api.sep === "\\" ? "gimu" : "gmu");
  const recent = turns.slice(-3).reverse();
  for (const [distance, raw] of recent.entries()) {
    if (!raw || raw.length > 4000) return;
    const intent = selectGuidanceIntent({ text: raw, reason: "user_request" });
    if (intent.omitted || !["design", "core"].includes(intent.primary) ||
        (intent.primary === "core" && intent.reason !== "no_explicit_topic")) return;
    // Match a whole descriptor or root (including spaces). Another absolute
    // target or a scope change breaks the chain even when the known path also occurs.
    const prose = userProse(raw, p => api.isAbsolute(p) || /^[a-z]:[\\/]/iu.test(p)).replaceAll("\\", "/");
    const body = prose.replace(projectPath, " ");
    if (/(?:^|[\s(])(?:[a-z]:\/|\/\S)/imu.test(body) || scopeSwitch.test(body)) return;
    if (!designSubject.test(body) || !designQuestion.test(body)) return;
    if (body !== prose || (!distance && projectReference.test(body))) return distance;
    if (!continuation.test(body)) return;
  }
}

export function selectConversationGuidance(history: NormalizedMessage[], unrealProject?: string, boundaryReason?: string) {
  const messages = history.map((message, index) => ({ ...message, index }));
  const users = messages.filter(m => m.role === "user");
  const previous = messages.slice(0, users.at(-1)?.index ?? 0);
  const objectiveOf = (prefix: typeof messages) =>
    objectiveContinuity.guidanceObjective(prefix, continuityState.extractPriorContinuityState(prefix));
  const objective = objectiveOf(messages);
  const intent = selectFollowUpGuidanceIntent(objective, users.length > 1 ? objectiveOf(previous) : undefined);
  const canCarry = !boundaryReason || ["disabled", "no_marker", "verified"].includes(boundaryReason);
  const priorTurns = canCarry ? users.slice(-3, -1).map(m => m.text) : [];
  const needsProjectIntent = intent.primary === "core" || (intent.primary === "design" && intent.reason === "previous_request_topic");
  const anchor = unrealProject && objective.reason === "user_objective_verified" && objective.text &&
    objective.text === users.at(-1)?.text.trim() && needsProjectIntent
    ? projectDesignTurnsBack([...priorTurns, objective.text], unrealProject) : undefined;
  return { guidanceObjective: objective, guidanceIntent: intent, projectDesignAnchor: anchor };
}

export function selectGuidanceTopics(intent: GuidanceIntent, snapshot: ReferenceSnapshot) {
  const observed = (snapshot.autoSignals || []).filter(s => s.order === "last_observation"
    && ["diagnostic_present", "diagnostic_recurred", "compilation_pending", "operation_outcome_unknown"].includes(s.signal));
  const diagnostic = !intent.omitted && intent.primary !== "debugging" && observed.length > 0;
  const apiObserved = (snapshot.autoSignals || []).filter(s => s.signal === "api_contract_observed" && s.order === "last_observation");
  const api = !intent.omitted && intent.primary !== "debugging" && apiObserved.length > 0;
  // Any returned source still in the input counts, not only the last one, so
  // the topic does not flip with each read (the instruction is in the system message).
  const networkObserved = (snapshot.autoSignals || []).filter(s => s.signal === "network_contract_observed");
  const network = !intent.omitted && !diagnostic && !intent.secondary && intent.primary !== "multiplayer"
    && networkObserved.length > 0;
  const supplement = intent.omitted ? undefined : diagnostic ? "debugging" as const : network ? "multiplayer" as const
    : api && !intent.secondary ? "debugging" as const : intent.secondary;
  return { primary: intent.primary, supplement, omitted: intent.omitted === true,
    reasons: [intent.reason, ...(diagnostic ? ["returned_diagnostic_or_pending"] : []),
      ...(network ? ["returned_network_contract_context"] : []),
      ...(api && !diagnostic && !network && !intent.secondary ? ["returned_api_contract_context"] : []),
      ...(diagnostic && intent.secondary && intent.secondary !== "debugging" ? ["explicit_secondary_deferred_for_diagnostic"] : [])],
    observationIds: [...new Set([...observed, ...(network ? networkObserved : []),
      ...(api && !diagnostic && !network && !intent.secondary ? apiObserved : [])]
      .flatMap(s => s.observationIds))].slice(0, 4) };
}
