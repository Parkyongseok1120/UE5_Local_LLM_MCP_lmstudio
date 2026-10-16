import crypto from "node:crypto";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { Chat, ChatMessage, type PromptPreprocessor } from "@lmstudio/sdk";
import { directConfigSchematics } from "./direct-config";
import { contextManagementMode } from "./execution-config";

const MARKER = "\n<!-- hybrid-context-v1:";
const END = " -->";
const ATTACHMENT = /\n<!-- workspace-attachments-v1:[A-Za-z0-9_-]+\.[a-f0-9]{64} -->/gu;
type Scope = { conversation: string; lineage: string; parentLineage: string | null };

const digest = (value: string) => crypto.createHash("sha256").update(value).digest("hex");
const normalizedFingerprint = (messages: Array<ChatMessage>) => digest(JSON.stringify(messages.map(message => ({
  role: message.getRole(),
  text: message.getText().replace(ATTACHMENT, ""),
  requests: message.getToolCallRequests(),
  results: message.getToolCallResults(),
}))));

export class WorkingContextBoundary {
  private cachedKey: Buffer | null = null;
  constructor(private readonly directory = path.join(os.homedir(), ".lmstudio", "unreal-context-compactor", "hybrid-boundary-v1")) {}

  // The key is created once and never rotated by this process; avoid four
  // synchronous filesystem calls per signed user message on every prediction.
  private key(): Buffer {
    if (this.cachedKey) return this.cachedKey;
    this.cachedKey = this.loadKey();
    return this.cachedKey;
  }

  private loadKey(): Buffer {
    fs.mkdirSync(this.directory, { recursive: true, mode: 0o700 });
    if (fs.lstatSync(this.directory).isSymbolicLink()) throw new Error("Hybrid boundary directory must not be a symlink");
    const target = path.join(this.directory, "reference-key");
    try { fs.writeFileSync(target, crypto.randomBytes(32), { flag: "wx", mode: 0o600 }); }
    catch (error) { if ((error as NodeJS.ErrnoException).code !== "EEXIST") throw error; }
    const stat = fs.lstatSync(target);
    if (!stat.isFile() || stat.isSymbolicLink() || stat.nlink !== 1 || stat.size !== 32) throw new Error("Invalid hybrid boundary key");
    return fs.readFileSync(target);
  }

  /** Scope comes from the most recent contiguous chain of valid markers. One
   * edited or stale older turn breaks the chain at that point only; a later
   * marker whose prefix covers the current history starts a valid chain again.
   * Invalid markers (forged, unknown key, malformed) fail closed by granting no
   * scope, but never make the conversation itself unusable. */
  restore(history: Chat): { modelHistory: Chat; scope: Scope | null; reason: string } {
    const input = history.getMessagesArray(), output = Chat.empty();
    let scope: Scope | null = null;
    let breakReason = "";
    let recovered = false;
    for (let index = 0; index < input.length; index += 1) {
      const source = input[index], copy = ChatMessage.from(source), raw = copy.getText();
      const normalizedRaw = raw.replace(ATTACHMENT, "");
      const start = source.isUserMessage() ? normalizedRaw.lastIndexOf(MARKER) : -1;
      if (start >= 0) {
        const envelope = normalizedRaw.slice(start + MARKER.length);
        const match = envelope.length <= 1400 ? /^([A-Za-z0-9_-]+)\.([a-f0-9]{64}) -->$/u.exec(envelope) : null;
        if (!match) {
          output.append(copy);
          continue;
        }
        const visible = normalizedRaw.slice(0, start);
        // A well-formed envelope is runtime metadata, never model input.
        copy.replaceText(visible);
        const value = this.verifiedEnvelope(match[1], match[2]);
        const failure = typeof value === "string" ? value
          : value.request !== digest(visible) ? "edited_history"
            : value.prefix !== normalizedFingerprint(input.slice(0, index)) ? "stale_prefix"
              : scope && value.conversation !== scope.conversation ? "conversation_changed"
                : scope && value.parentLineage !== scope.lineage ? "new_fork" : "";
        if (failure) {
          scope = null; breakReason = failure;
        } else if (typeof value !== "string") {
          if (breakReason) recovered = true;
          scope = { conversation: String(value.conversation), lineage: String(value.lineage),
            parentLineage: value.parentLineage === null ? null : String(value.parentLineage) };
        }
      }
      output.append(copy);
    }
    const reason = scope ? recovered ? `verified_after_${breakReason}` : "verified" : breakReason || "no_marker";
    return { modelHistory: output, scope, reason };
  }

  private verifiedEnvelope(body: string, signature: string): Record<string, unknown> | string {
    let expected: string;
    try { expected = crypto.createHmac("sha256", this.key()).update(body).digest("hex"); }
    catch { return "boundary_key_unavailable"; }
    if (!crypto.timingSafeEqual(Buffer.from(expected), Buffer.from(signature))) return "signature_invalid";
    let value: Record<string, unknown>;
    try { value = JSON.parse(Buffer.from(body, "base64url").toString("utf8")); }
    catch { return "scope_mismatch"; }
    const shapeValid = value?.version === 1
      && typeof value.conversation === "string" && /^[a-f0-9-]{36}$/u.test(value.conversation)
      && typeof value.lineage === "string" && /^[a-f0-9-]{36}$/u.test(value.lineage)
      && (value.parentLineage === null || typeof value.parentLineage === "string"
        && /^[a-f0-9-]{36}$/u.test(value.parentLineage));
    return shapeValid ? value : "scope_mismatch";
  }

  capture(message: ChatMessage, previous: Chat): ChatMessage {
    const copy = ChatMessage.from(message);
    if (!copy.isUserMessage()) return message;
    const prior = this.restore(previous).scope;
    const visible = copy.getText();
    const value = {
      version: 1,
      conversation: prior?.conversation || crypto.randomUUID(),
      lineage: crypto.randomUUID(),
      parentLineage: prior?.lineage || null,
      request: digest(visible),
      prefix: normalizedFingerprint(previous.getMessagesArray()),
    };
    const body = Buffer.from(JSON.stringify(value)).toString("base64url");
    const signature = crypto.createHmac("sha256", this.key()).update(body).digest("hex");
    copy.replaceText(`${visible}${MARKER}${body}.${signature}${END}`);
    return copy;
  }
}

export const workingContextBoundary = new WorkingContextBoundary();

export const preprocessWorkingContext: PromptPreprocessor = async (ctl, message) => {
  // Same mode resolution as the prediction handler: a missing value must not
  // disable boundary capture while the handler runs hybrid.
  const mode = contextManagementMode(ctl.getPluginConfig(directConfigSchematics).get("contextManagementMode"));
  if (mode === "legacy" || ctl.getPluginConfig(directConfigSchematics).get("observeOnly") === true) return message;
  ctl.guardAbort();
  const output = workingContextBoundary.capture(message, await ctl.pullHistory());
  ctl.debug({ event: "hybrid_context_boundary", captured: output !== message });
  return output;
};

export const __test = { MARKER, normalizedFingerprint };
