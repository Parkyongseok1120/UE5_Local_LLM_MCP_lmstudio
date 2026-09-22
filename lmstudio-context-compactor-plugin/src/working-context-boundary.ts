import crypto from "node:crypto";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { Chat, ChatMessage, type PromptPreprocessor } from "@lmstudio/sdk";
import { directConfigSchematics } from "./direct-config";

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
  constructor(private readonly directory = path.join(os.homedir(), ".lmstudio", "unreal-context-compactor", "hybrid-boundary-v1")) {}

  private key(): Buffer {
    fs.mkdirSync(this.directory, { recursive: true, mode: 0o700 });
    if (fs.lstatSync(this.directory).isSymbolicLink()) throw new Error("Hybrid boundary directory must not be a symlink");
    const target = path.join(this.directory, "reference-key");
    try { fs.writeFileSync(target, crypto.randomBytes(32), { flag: "wx", mode: 0o600 }); }
    catch (error) { if ((error as NodeJS.ErrnoException).code !== "EEXIST") throw error; }
    const stat = fs.lstatSync(target);
    if (!stat.isFile() || stat.isSymbolicLink() || stat.nlink !== 1 || stat.size !== 32) throw new Error("Invalid hybrid boundary key");
    return fs.readFileSync(target);
  }

  restore(history: Chat): { modelHistory: Chat; scope: Scope | null } {
    const input = history.getMessagesArray(), output = Chat.empty();
    let scope: Scope | null = null;
    for (let index = 0; index < input.length; index += 1) {
      const source = input[index], copy = ChatMessage.from(source), raw = copy.getText();
      const normalizedRaw = raw.replace(ATTACHMENT, "");
      const start = source.isUserMessage() ? normalizedRaw.lastIndexOf(MARKER) : -1;
      if (start >= 0) {
        const envelope = normalizedRaw.slice(start + MARKER.length);
        const match = envelope.length <= 1400 ? /^([A-Za-z0-9_-]+)\.([a-f0-9]{64}) -->$/u.exec(envelope) : null;
        const expected = match ? crypto.createHmac("sha256", this.key()).update(match[1]).digest("hex") : "";
        if (match && crypto.timingSafeEqual(Buffer.from(expected), Buffer.from(match[2]))) {
          const value = JSON.parse(Buffer.from(match[1], "base64url").toString("utf8"));
          const visible = normalizedRaw.slice(0, start);
          const valid = value?.version === 1
            && typeof value.conversation === "string" && /^[a-f0-9-]{36}$/u.test(value.conversation)
            && typeof value.lineage === "string" && /^[a-f0-9-]{36}$/u.test(value.lineage)
            && (value.parentLineage === null || /^[a-f0-9-]{36}$/u.test(value.parentLineage))
            && value.request === digest(visible)
            && value.prefix === normalizedFingerprint(input.slice(0, index))
            && (!scope || (value.conversation === scope.conversation && value.parentLineage === scope.lineage));
          if (!valid) throw new Error("Hybrid context reference does not match this request lineage");
          scope = { conversation: value.conversation, lineage: value.lineage, parentLineage: value.parentLineage };
          copy.replaceText(visible);
        }
      }
      output.append(copy);
    }
    return { modelHistory: output, scope };
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
  const mode = String(ctl.getPluginConfig(directConfigSchematics).get("contextManagementMode") || "legacy");
  if (mode === "legacy" || ctl.getPluginConfig(directConfigSchematics).get("observeOnly") === true) return message;
  ctl.guardAbort();
  const output = workingContextBoundary.capture(message, await ctl.pullHistory());
  ctl.debug({ event: "hybrid_context_boundary", captured: output !== message });
  return output;
};

export const __test = { MARKER, normalizedFingerprint };
