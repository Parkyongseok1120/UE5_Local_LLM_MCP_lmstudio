import type {
  LLMPredictionFragmentWithRoundIndex,
  PredictionProcessContentBlockController,
  PredictionLoopHandlerController,
} from "@lmstudio/sdk";
import { InternalDataOutputFilter } from "./internal-data-output";
import { ToolProtocolOutputFilter, stripUnexecutedToolProtocol, hasRawToolIntent, visibleTextFromMessages } from "./raw-tool-intent";
import { ChatMessage } from "@lmstudio/sdk";
import { DELIVERED_REPORT_HEADER as FRAME_OPEN, DELIVERED_REPORT_END as FRAME_CLOSE } from "./review-patterns.js";
const { visibleAssistantText } = require("./continuity-text.js") as { visibleAssistantText(text: string): string };

type SplitVisibleAnswer = (text: string) => {
  visibleText: string;
  hasFooter: boolean;
  note: unknown;
};

type StreamState = {
  answerBlock?: PredictionProcessContentBlockController;
  thinkingBlock?: PredictionProcessContentBlockController;
  rawVisible: string;
  pendingVisible: string;
  footerSeen: boolean;
  sawFragment: boolean;
  finished: boolean;
  consumed: boolean;
  dataFilter: InternalDataOutputFilter;
  protocolFilter: ToolProtocolOutputFilter;
  echoFilter: DeliveredPrefixEchoFilter;
  visibleText: string;
};

export type StreamedAssistant = {
  streamed: boolean;
  visibleText: string;
  duplicateSuppressed?: boolean;
  suppressedChars?: number;
  suppressedProtocol?: boolean;
  incompleteProtocol?: boolean;
};

const FOOTER_MARKER = "\n<!-- direct-continuity-note-v";

// A long report echo may fix a typo while repeating it (conversation
// 1793526507401: "해armless"→"harmless" at 14% showed the report twice). Only
// long prefixes tolerate edits; each edit must resynchronize on this many chars.
const ECHO_EDIT_MIN_PREFIX = 200, ECHO_EDIT_SHARE = 0.02, ECHO_EDIT_WINDOW = 8, ECHO_ANCHOR = 8;

/** Greedy alignment of normalized echo text against the delivered prefix.
 * `i` is consumed echo, `j` covered prefix; "pending" needs more echo text. */
function alignEcho(prefix: string, current: string, maxEdits: number, final: boolean) {
  let i = 0, j = 0, edits = 0;
  while (i < current.length && j < prefix.length) {
    if (current[i] === prefix[j]) { i++; j++; continue; }
    // The end of the prefix is where a real delta starts; do not edit across it.
    if (prefix.length - j <= ECHO_ANCHOR) return { i, j, state: "diverged" as const };
    let pending = false, step: [number, number, number] | null = null;
    for (let k = 1; k <= ECHO_EDIT_WINDOW && edits + k <= maxEdits && !step; k++) {
      for (const [di, dj] of [[k, k], [k, 0], [0, k]]) {
        if (i + di + ECHO_ANCHOR > current.length) { pending ||= !final; continue; }
        if (current.slice(i + di, i + di + ECHO_ANCHOR) === prefix.slice(j + dj, j + dj + ECHO_ANCHOR)) { step = [di, dj, k]; break; }
      }
    }
    if (!step) return { i, j, state: pending ? "pending" as const : "diverged" as const };
    i += step[0]; j += step[1]; edits += step[2];
  }
  return { i, j, state: j >= prefix.length ? "complete" as const : "consistent" as const };
}

// A review may instead rewrite the whole report with a section inserted in the
// middle (conversation 1793532481549: a new "### 6." at 71%). An echo that
// covered this much of the report before diverging is held to the end; when the
// output still contains most report lines, only its new lines are shown.
const REWRITE_MIN_CHARS = 80, REWRITE_MIN_SHARE = 0.2, REWRITE_MIN_COVERAGE = 0.8, SAME_LINE_RATIO = 0.9;
const lineKey = (line: string) => line.replace(/\s+/gu, " ").trim();
/** One line edited in place (a fixed typo or word) still counts as the same line. */
function sameLine(a: string, b: string): boolean {
  if (a === b) return true;
  const longest = Math.max(a.length, b.length);
  if (longest < 12 || Math.abs(a.length - b.length) > longest * (1 - SAME_LINE_RATIO)) return false;
  let start = 0, end = 0;
  while (start < a.length && start < b.length && a[start] === b[start]) start++;
  while (end < a.length - start && end < b.length - start && a[a.length - 1 - end] === b[b.length - 1 - end]) end++;
  return (start + end) / longest >= SAME_LINE_RATIO;
}

/** A supplement may echo a delivered answer before producing its actual delta.
 * Hold only a possible prefix; unrelated replies and short partial matches pass.
 * The echo can be the whole data block the report was shown in, its header and
 * end line included (conversation 1794306949131, round 52): both lines are
 * stripped, so the block is recognized as the echo it is. */
export class DeliveredPrefixEchoFilter {
  private buffer = "";
  private passthrough = false;
  private rewriting = false;
  private readonly prefix: string;
  private readonly deliveredLines: string[];
  private readonly maxEdits: number;
  duplicateSuppressed = false;
  suppressedChars = 0;
  constructor(deliveredPrefix = "") {
    this.prefix = this.normalize(deliveredPrefix);
    this.deliveredLines = deliveredPrefix.split(/\r?\n/u).map(lineKey).filter(Boolean);
    this.maxEdits = this.prefix.length >= ECHO_EDIT_MIN_PREFIX ? Math.floor(this.prefix.length * ECHO_EDIT_SHARE) : 0;
  }
  private normalize(value: string) { return value.replace(/\s+/gu, " ").trim(); }
  /** null until the start of the output shows whether it copies the frame header. */
  private framed: boolean | null = null;
  private stripFrameHeader(final: boolean): boolean {
    if (this.framed !== null) return true;
    const head = this.buffer.trimStart();
    if (!final && head.length < FRAME_OPEN.length && FRAME_OPEN.startsWith(head)) return false;
    this.framed = head.startsWith(FRAME_OPEN);
    if (this.framed) {
      const cut = this.buffer.length - head.length + FRAME_OPEN.length;
      this.duplicateSuppressed = true; this.suppressedChars += cut; this.buffer = this.buffer.slice(cut);
    }
    return true;
  }
  push(text: string): string {
    if (this.passthrough || !this.prefix) return this.release(text);
    this.buffer += text;
    if (!this.stripFrameHeader(false)) return "";
    if (this.rewriting) return "";
    const aligned = alignEcho(this.prefix, this.normalize(this.buffer), this.maxEdits, false);
    if (aligned.state === "pending" || aligned.state === "consistent") return "";
    if (aligned.j < this.prefix.length * 0.9) {
      if (this.maxEdits && aligned.j >= Math.max(REWRITE_MIN_CHARS, this.prefix.length * REWRITE_MIN_SHARE)) {
        this.rewriting = true; return "";
      }
      this.passthrough = true; return this.drain();
    }
    this.passthrough = true;
    return this.cutEcho(aligned.i);
  }
  /** A held rewrite: its new lines when it still contains most of the report, else all of it. */
  private rewriteDelta(): string {
    const lines = this.buffer.split(/\r?\n/u);
    const known = (line: string) => line === FRAME_CLOSE || this.deliveredLines.some(delivered => sameLine(delivered, line));
    const keys = lines.map(lineKey);
    const covered = this.deliveredLines.filter(delivered => keys.some(key => key && sameLine(delivered, key))).length;
    if (covered < this.deliveredLines.length * REWRITE_MIN_COVERAGE) return this.drain();
    const runs: string[][] = [];
    let run: string[] | null = null;
    lines.forEach((line, index) => {
      const key = keys[index];
      if (key && !known(key)) { (run ||= (runs.push([]), runs[runs.length - 1])).push(line); return; }
      if (!key && run) run.push(line); else run = null;
    });
    const delta = runs.map(block => block.join("\n").trim()).filter(Boolean).join("\n\n");
    this.duplicateSuppressed = true; this.suppressedChars += this.buffer.length - delta.length; this.buffer = "";
    if (!delta) return "";
    return `\n\n${/^#{1,6}\s*검토 보완/mu.test(delta) ? "" : "## 검토 보완\n"}${delta}`;
  }
  /** Map a normalized echo length back to the original text, preserving the delta. */
  private cutEcho(matched: number): string {
    let normalized = 0, cut = this.buffer.length, started = false;
    for (const match of this.buffer.matchAll(/\s+|\S+/gu)) {
      const whitespace = /^\s/u.test(match[0]);
      if (whitespace && !started) continue;
      const length = whitespace ? 1 : match[0].length;
      if (normalized + length > matched) { cut = match.index! + (whitespace ? 0 : matched - normalized); break; }
      normalized += length; started = true;
    }
    if (cut > 0 && /[\uDC00-\uDFFF]/u.test(this.buffer[cut] || "") && /[\uD800-\uDBFF]/u.test(this.buffer[cut - 1])) cut--;
    this.duplicateSuppressed = true; this.suppressedChars += cut;
    this.buffer = this.buffer.slice(cut);
    this.heldSpace = true; this.closing = true;
    return this.drain();
  }
  // Whitespace right after a suppressed echo is shown only with the delta it
  // precedes; alone it would deliver an empty supplement.
  private heldSpace = false;
  private space = "";
  /** Right after a suppressed echo the frame's end line may follow; it is dropped too. */
  private closing = false;
  private release(text: string, final = false): string {
    if (!this.heldSpace) return text;
    this.space += text;
    let body = this.space.trimStart();
    if (this.closing && body) {
      if (!final && body.length < FRAME_CLOSE.length && FRAME_CLOSE.startsWith(body)) return "";
      this.closing = false;
      if (body.startsWith(FRAME_CLOSE)) {
        this.suppressedChars += this.space.length - body.length + FRAME_CLOSE.length;
        this.space = body = body.slice(FRAME_CLOSE.length);
        if (!body.trim()) return "";
      }
    }
    if (!body) return "";
    this.heldSpace = false; const out = this.space; this.space = ""; return out;
  }
  finish(): string {
    if (this.passthrough || !this.prefix) { this.passthrough = true; return this.release(this.drain(), true); }
    this.passthrough = true;
    this.stripFrameHeader(true);
    if (this.rewriting) return this.rewriteDelta();
    const aligned = alignEcho(this.prefix, this.normalize(this.buffer), this.maxEdits, true);
    const out = aligned.j >= this.prefix.length * 0.9 ? this.cutEcho(aligned.i) : this.drain();
    return out + this.release("", true);
  }
  private drain() { const text = this.buffer; this.buffer = ""; return this.release(text); }
}

export class GenerationRepetitionDetector {
  private text = "";
  private detected = false;
  private totalChars = 0;
  private lastCheckedAt = 0;
  private block = "";
  /** The block that repeated, once detected. */
  get repeatedBlock() { return this.block; }

  constructor(private readonly repeatCount = 3, private readonly minimumBlockChars = 80) {}

  observe(fragment: LLMPredictionFragmentWithRoundIndex): boolean {
    if (this.detected || fragment.isStructural || fragment.reasoningType === "reasoningStartTag"
      || fragment.reasoningType === "reasoningEndTag" || !fragment.content) return false;
    this.totalChars += fragment.content.length;
    this.text = (this.text + fragment.content).slice(-12_000);
    if (this.totalChars - this.lastCheckedAt < 32) return false;
    this.lastCheckedAt = this.totalChars;
    const maximumBlock = Math.min(2_000, Math.floor(this.text.length / this.repeatCount));
    for (let size = this.minimumBlockChars; size <= maximumBlock; size += 1) {
      const suffix = this.text.slice(-size);
      if (new Set(suffix.replace(/\s/gu, "")).size < 12) continue;
      let repeated = true;
      for (let copy = 2; copy <= this.repeatCount; copy += 1) {
        if (this.text.slice(-size * copy, -size * (copy - 1)) !== suffix) {
          repeated = false;
          break;
        }
      }
      if (repeated) {
        this.detected = true;
        this.block = suffix;
        return true;
      }
    }
    return false;
  }
}

export class PredictionStreamRenderer {
  private readonly states = new Map<number, StreamState>();
  private readonly consumedMessages = new WeakMap<ChatMessage, StreamedAssistant>();
  private fallbackSuppressed = 0;
  get suppressedChars() { return this.fallbackSuppressed + [...this.states.values()].reduce((n, s) => n + s.echoFilter.suppressedChars, 0); }
  consumeMessage(message: ChatMessage): StreamedAssistant {
    let result = this.consumedMessages.get(message);
    if (!result) { result = this.consumeAssistant(message.getText()); this.consumedMessages.set(message, result); }
    return result;
  }
  visibleMessage(message: ChatMessage): ChatMessage {
    if (!message.isAssistantMessage() || !message.getText()) return message;
    const copy = ChatMessage.from(message); copy.replaceText(this.consumeMessage(message).visibleText); return copy;
  }
  /** Use the same filtered prose for the model while retaining SDK reasoning.
   * Tool intent remains a separate captured observation, never displayed prose. */
  historyMessage(message: ChatMessage): ChatMessage {
    if (!message.isAssistantMessage() || !message.getText()) return message;
    const raw = this.splitVisibleAnswer(message.getText()).visibleText;
    const prose = visibleAssistantText(raw), reasoning = raw.slice(0, raw.length - prose.length);
    const copy = ChatMessage.from(message); copy.replaceText(reasoning + this.consumeMessage(message).visibleText); return copy;
  }
  normalizeCaptured<T extends { messages: ChatMessage[]; predictionUsage: { rawToolIntentCandidate?: boolean } }>(captured: T) {
    const unexecutedToolIntent = hasRawToolIntent(visibleTextFromMessages(captured.messages, this.internalDataParts), captured.messages);
    const messages = captured.messages.map(message => this.visibleMessage(message));
    const observations = captured.messages.filter(m => m.isAssistantMessage() && m.getText()).map(m => this.consumeMessage(m));
    return { ...captured, messages, unexecutedToolIntent, presentation: {
      deliveredChars: messages.filter(m => m.isAssistantMessage()).reduce((n, m) => n + m.getText().length, 0),
      suppressedInternalProtocol: observations.some(o => o.suppressedProtocol),
      incompleteInternalProtocol: observations.some(o => o.incompleteProtocol),
      duplicateSuppressedChars: this.suppressedChars,
    },
      predictionUsage: { ...captured.predictionUsage, rawToolIntentCandidate: captured.predictionUsage.rawToolIntentCandidate || unexecutedToolIntent } };
  }

  constructor(
    private readonly ctl: Pick<PredictionLoopHandlerController, "createContentBlock">,
    private readonly splitVisibleAnswer: SplitVisibleAnswer,
    private readonly protectContinuityFooter: boolean,
    private readonly suppressReasoning = false,
    private readonly internalDataParts: readonly string[] = [],
    private readonly deliveredPrefix?: string,
  ) {}

  private state(roundIndex: number): StreamState {
    let state = this.states.get(roundIndex);
    if (!state) {
      state = {
        rawVisible: "", pendingVisible: "", footerSeen: false,
        sawFragment: false, finished: false, consumed: false,
        dataFilter: new InternalDataOutputFilter(this.internalDataParts),
        protocolFilter: new ToolProtocolOutputFilter(),
        echoFilter: new DeliveredPrefixEchoFilter(this.deliveredPrefix), visibleText: "",
      };
      this.states.set(roundIndex, state);
    }
    return state;
  }

  private answerBlock(state: StreamState): PredictionProcessContentBlockController {
    state.answerBlock ??= this.ctl.createContentBlock({ roleOverride: "assistant" });
    return state.answerBlock;
  }

  private thinkingBlock(state: StreamState): PredictionProcessContentBlockController {
    state.thinkingBlock ??= this.ctl.createContentBlock({
      roleOverride: "assistant",
      includeInContext: false,
      style: { type: "thinking", ended: false, title: "생각 중" },
    });
    return state.thinkingBlock;
  }

  private appendVisible(state: StreamState, text: string, fragment: LLMPredictionFragmentWithRoundIndex): void {
    text = state.echoFilter.push(state.dataFilter.push(state.protocolFilter.push(text)));
    if (!text) return;
    state.visibleText += text;
    this.answerBlock(state).appendText(text, {
      tokensCount: fragment.tokensCount,
      fromDraftModel: fragment.containsDrafted,
      isStructural: false,
    });
  }

  private streamVisible(state: StreamState, fragment: LLMPredictionFragmentWithRoundIndex): void {
    state.rawVisible += fragment.content;
    if (!this.protectContinuityFooter) {
      this.appendVisible(state, fragment.content, fragment);
      return;
    }
    if (state.footerSeen) return;
    state.pendingVisible += fragment.content;
    const markerIndex = state.pendingVisible.indexOf(FOOTER_MARKER);
    if (markerIndex >= 0) {
      this.appendVisible(state, state.pendingVisible.slice(0, markerIndex), fragment);
      state.pendingVisible = "";
      state.footerSeen = true;
      return;
    }
    const safeLength = Math.max(0, state.pendingVisible.length - FOOTER_MARKER.length + 1);
    if (safeLength > 0) {
      this.appendVisible(state, state.pendingVisible.slice(0, safeLength), fragment);
      state.pendingVisible = state.pendingVisible.slice(safeLength);
    }
  }

  onFragment = (fragment: LLMPredictionFragmentWithRoundIndex): void => {
    const state = this.state(fragment.roundIndex);
    state.sawFragment = true;
    if (fragment.reasoningType === "reasoning") {
      if (this.suppressReasoning) return;
      this.thinkingBlock(state).appendText(fragment.content, {
        tokensCount: fragment.tokensCount,
        fromDraftModel: fragment.containsDrafted,
        isStructural: false,
      });
      return;
    }
    if (fragment.reasoningType === "reasoningStartTag"
      || fragment.reasoningType === "reasoningEndTag" || fragment.isStructural) return;
    this.streamVisible(state, fragment);
  };

  finish(roundIndex: number): void {
    const state = this.state(roundIndex);
    if (state.finished) return;
    state.finished = true;
    if (!state.footerSeen && state.pendingVisible) {
      const visible = state.echoFilter.push(state.dataFilter.push(state.protocolFilter.push(state.pendingVisible)));
      if (visible) { state.visibleText += visible; this.answerBlock(state).appendText(visible); }
      state.pendingVisible = "";
    }
    const remaining = state.echoFilter.push(state.dataFilter.push(state.protocolFilter.finish()) + state.dataFilter.finish())
      + state.echoFilter.finish();
    if (remaining) { state.visibleText += remaining; this.answerBlock(state).appendText(remaining); }
    if (state.thinkingBlock) {
      state.thinkingBlock.setStyle({ type: "thinking", ended: true, title: "생각" });
    }
  }

  finishAll(): void { for (const roundIndex of this.states.keys()) this.finish(roundIndex); }

  consumeAssistant(rawText: string): StreamedAssistant {
    const entry = [...this.states.entries()]
      .sort(([left], [right]) => left - right)
      .find(([, state]) => state.sawFragment && !state.consumed);
    if (!entry) {
      const echo = new DeliveredPrefixEchoFilter(this.deliveredPrefix);
      const dataFilter = new InternalDataOutputFilter(this.internalDataParts);
      const raw = stripUnexecutedToolProtocol(this.splitVisibleAnswer(visibleAssistantText(rawText)).visibleText);
      const text = dataFilter.push(raw) + dataFilter.finish();
      const visibleText = echo.push(text) + echo.finish();
      this.fallbackSuppressed += echo.suppressedChars;
      return { streamed: false, visibleText,
        ...dataFilter.observation,
        duplicateSuppressed: echo.duplicateSuppressed, suppressedChars: echo.suppressedChars };
    }
    const [roundIndex, state] = entry;
    this.finish(roundIndex);
    state.consumed = true;
    return {
      streamed: true,
      visibleText: state.visibleText, duplicateSuppressed: state.echoFilter.duplicateSuppressed,
      ...state.dataFilter.observation,
      suppressedChars: state.echoFilter.suppressedChars,
    };
  }
}
