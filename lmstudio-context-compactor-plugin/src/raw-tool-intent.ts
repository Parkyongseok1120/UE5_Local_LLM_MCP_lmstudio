import {
  ChatMessage
} from "@lmstudio/sdk";
import { REASONING_SEPARATOR, modelNotes } from "./context-ports";
import { type RemoteToolLike, UNSAFE_UNKNOWN_NAME_PATTERN, isObservationOnlyToolCall } from "./tool-capability-registry";

export function visibleAssistantOutput(text: string): { visibleText: string; hasFooter: boolean; note: unknown } {
  const raw = String(text || "");
  const separatorIndex = raw.lastIndexOf(REASONING_SEPARATOR);
  return modelNotes.splitVisibleAnswer(separatorIndex >= 0
    ? raw.slice(separatorIndex + REASONING_SEPARATOR.length) : raw);
}

export const RAW_TOOL_WRAPPER_PATTERNS = [
  /<tool_call>[\s\S]*?<\/tool_call>/giu,
  /<function=[^>\r\n]+>[\s\S]*?<\/function>/giu,
  /<\|(?:tool_call|python_tag)\|>[\s\S]*?<\|(?:eom|eot|end)\|>/giu,
];

export function isInsideMarkdownFence(text: string, index: number): boolean {
  let cursor = 0;
  let fence: { marker: string; length: number } | null = null;
  while (cursor < index) {
    const lineEnd = text.indexOf("\n", cursor);
    const end = lineEnd < 0 ? text.length : lineEnd;
    const line = text.slice(cursor, end);
    const marker = /^\s{0,3}(`{3,}|~{3,})/u.exec(line)?.[1];
    if (marker) {
      const kind = marker[0];
      if (!fence) fence = { marker: kind, length: marker.length };
      else if (fence.marker === kind && marker.length >= fence.length) fence = null;
    }
    cursor = lineEnd < 0 ? text.length : lineEnd + 1;
  }
  return fence !== null;
}

export function isLiteralRawToolWrapper(text: string, start: number, end: number): boolean {
  if (isInsideMarkdownFence(text, start)) return true;
  const lineStart = Math.max(0, text.lastIndexOf("\n", start - 1) + 1);
  const lineEndIndex = text.indexOf("\n", end);
  const lineEnd = lineEndIndex < 0 ? text.length : lineEndIndex;
  const line = text.slice(lineStart, lineEnd);
  const relativeStart = start - lineStart;
  const relativeEnd = end - lineStart;
  const before = line.slice(0, relativeStart);
  const after = line.slice(relativeEnd);
  if (/^\s{0,3}>/u.test(line)) return true;

  const trimmedBefore = before.trimEnd();
  const trimmedAfter = after.trimStart();
  const leftQuote = /(?:["“'‘])$/u.test(trimmedBefore);
  const rightQuote = /^(?:["”'’])/u.test(trimmedAfter);
  if (leftQuote && rightQuote) return true;
  if (/(?:example|for example|e\.g\.|citation|quoted|인용|예시)\s*[:：,]?\s*$/iu.test(
    text.slice(Math.max(0, start - 120), start),
  )) return true;

  // Inline Markdown code is a literal example, not a request to dispatch.
  const inlineBefore = before.lastIndexOf("`");
  const inlineAfter = after.indexOf("`");
  return inlineBefore >= 0 && inlineAfter >= 0;
}

export function containsUnresolvedToolIntent(reportText: string, messages: Array<ChatMessage>): boolean {
  if (messages.some(message => message.isAssistantMessage() && message.getToolCallRequests().length > 0)) return true;
  const text = reportText.trim();
  if (!text) return false;

  // Qwen-compatible templates can emit raw XML calls after ordinary prose.
  // Only wrappers explicitly marked as code, quotation, citation, or a
  // Markdown block are treated as literal examples. Everything else remains
  // unresolved intent. This classifier never parses or executes the XML.
  const wrappers = RAW_TOOL_WRAPPER_PATTERNS.flatMap(pattern => [...text.matchAll(pattern)]
    .map(match => {
      const start = match.index ?? -1;
      return { start, end: start + match[0].length };
    })
    .filter(wrapper => wrapper.start >= 0));
  return wrappers.some(wrapper => {
    if (isLiteralRawToolWrapper(text, wrapper.start, wrapper.end)) return false;
    // The inner <function=...> match is part of the same literal outer
    // wrapper in common Qwen XML. Do not let that nested match override the
    // outer code/quotation classification.
    return !wrappers.some(outer => outer !== wrapper
      && outer.start <= wrapper.start && outer.end >= wrapper.end
      && isLiteralRawToolWrapper(text, outer.start, outer.end));
  });
}

export function hasUnexecutedRawToolSyntax(text: string): boolean {
  if (containsUnresolvedToolIntent(text, [])) return true;
  const match = /<(?:tool_call|function=)|\|(?:tool_call|python_tag)\|/iu.exec(text);
  if (!match || match.index === undefined) return false;
  const lineEnd = text.indexOf("\n", match.index);
  return !isLiteralRawToolWrapper(text, match.index, lineEnd < 0 ? text.length : lineEnd);
}

export function visibleTextFromMessages(messages: Array<ChatMessage>): string {
  return messages
    .filter(message => message.isAssistantMessage())
    .map(message => visibleAssistantOutput(message.getText()).visibleText)
    .join("")
    .trim();
}

export type RawToolIntentClassification = {
  names: Array<string>;
  knownReadOnlyNames: Array<string>;
  unknownNames: Array<string>;
  registeredButWithheldNames: Array<string>;
  registeredUnsafeNames: Array<string>;
  unsafeUnknownNames: Array<string>;
};

export function rawToolIntentNames(text: string): Array<string> {
  if (!containsUnresolvedToolIntent(text, [])) return [];
  const functionNames = [...text.matchAll(/<function=([A-Za-z0-9_.:-]+)>/gu)].map(match => match[1]);
  const jsonNames = [...text.matchAll(/["']name["']\s*:\s*["']([A-Za-z0-9_.:-]+)["']/gu)]
    .map(match => match[1]);
  return [...new Set([...functionNames, ...jsonNames])];
}

export function classifyRawToolIntent(
  text: string,
  visibleTools: Array<RemoteToolLike>,
  registeredTools: Array<RemoteToolLike> = visibleTools,
): RawToolIntentClassification {
  const names = rawToolIntentNames(text);
  const knownReadOnlyNames: Array<string> = [];
  const unknownNames: Array<string> = [];
  const registeredButWithheldNames: Array<string> = [];
  const registeredUnsafeNames: Array<string> = [];
  const unsafeUnknownNames: Array<string> = [];
  for (const name of names) {
    const visible = visibleTools.find(tool => tool.name === name);
    if (visible) {
      if (isObservationOnlyToolCall(visible, { name, arguments: {} })) knownReadOnlyNames.push(name);
      else registeredUnsafeNames.push(name);
      continue;
    }
    const registered = registeredTools.find(tool => tool.name === name);
    if (registered) {
      if (isObservationOnlyToolCall(registered, { name, arguments: {} })) {
        registeredButWithheldNames.push(name);
      } else registeredUnsafeNames.push(name);
      continue;
    }
    unknownNames.push(name);
    if (UNSAFE_UNKNOWN_NAME_PATTERN.test(name)) unsafeUnknownNames.push(name);
  }
  return {
    names, knownReadOnlyNames, unknownNames, registeredButWithheldNames,
    registeredUnsafeNames, unsafeUnknownNames
  };
}

export function rawReadOnlyIntentToolNames(text: string, tools: Array<RemoteToolLike>): Array<string> {
  const classified = classifyRawToolIntent(text, tools);
  return classified.names.length > 0
    && classified.unknownNames.length === 0
    && classified.registeredButWithheldNames.length === 0
    && classified.registeredUnsafeNames.length === 0
    ? classified.knownReadOnlyNames : [];
}
