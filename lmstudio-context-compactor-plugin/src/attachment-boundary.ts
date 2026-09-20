import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import crypto from "node:crypto";
import { Chat, ChatMessage, type LMStudioClient, type PromptPreprocessor } from "@lmstudio/sdk";
import { directConfigSchematics } from "./direct-config";

const MARKER = "\n<!-- workspace-attachments-v1:";
const MAX_ENVELOPE = 16384;
const fingerprint = (messages: Array<ChatMessage>) => crypto.createHash("sha256").update(JSON.stringify(messages.map(m => ({
  role: m.getRole(), text: m.getText(), requests: m.getToolCallRequests(), results: m.getToolCallResults(),
})))).digest("hex");
const digest = (value: string) => crypto.createHash("sha256").update(value).digest("hex");

// Self-contained signed references survive plugin restarts, forks and concurrent
// chats without a global "last attachment". They contain IDs, never document text.
export class AttachmentBoundary {
  constructor(private readonly directory = path.join(os.homedir(), ".lmstudio", "unreal-context-compactor", "attachment-boundary-v1")) {}
  private key(): Buffer {
    fs.mkdirSync(this.directory, { recursive: true, mode: 0o700 });
    const target = path.join(this.directory, "reference-key");
    if (fs.lstatSync(this.directory).isSymbolicLink()) throw new Error("Attachment reference directory must not be a symlink");
    try { fs.writeFileSync(target, crypto.randomBytes(32), { flag: "wx", mode: 0o600 }); }
    catch (error) { if ((error as NodeJS.ErrnoException).code !== "EEXIST") throw error; }
    const stat = fs.lstatSync(target);
    if (!stat.isFile() || stat.isSymbolicLink() || stat.size !== 32 || stat.nlink !== 1) throw new Error("Invalid attachment reference key");
    return fs.readFileSync(target);
  }
  async capture(message: ChatMessage, previous: Chat, client: LMStudioClient) {
    const copy = ChatMessage.from(message);
    const documents = copy.getFiles(client).filter(file => !file.isImage());
    if (!documents.length) return message;
    if (documents.length > 16) throw new Error("Attach at most 16 documents per message");
    const original = copy.getText();
    const body = Buffer.from(JSON.stringify({ version: 1, request: digest(original), prefix: fingerprint(previous.getMessagesArray()),
      files: documents.map(file => ({ type: "file", identifier: file.identifier, name: file.name, fileType: file.type, sizeBytes: file.sizeBytes })),
    })).toString("base64url");
    if (body.length > MAX_ENVELOPE) throw new Error("Attachment manifest exceeds the reference budget");
    const signature = crypto.createHmac("sha256", this.key()).update(body).digest("hex");
    copy.consumeFiles(client, file => !file.isImage());
    copy.replaceText(`${original}${MARKER}${body}.${signature} -->`);
    return copy;
  }
  restore(history: Chat, client?: LMStudioClient) {
    if (!history.hasFiles() && !history.getMessagesArray().some(m => m.isUserMessage() && m.getText().includes(MARKER)))
      return { modelHistory: history, attachmentHistory: history };
    const input = history.getMessagesArray(), attachmentHistory = Chat.empty(), modelHistory = Chat.empty();
    for (let i = 0; i < input.length; i++) {
      const source = input[i], copy = ChatMessage.from(source);
      const raw = copy.getText(), start = raw.lastIndexOf(MARKER);
      if (source.isUserMessage() && start >= 0) {
        const envelope = raw.slice(start + MARKER.length);
        const match = envelope.length <= MAX_ENVELOPE + 70
          ? /^([A-Za-z0-9_-]+)\.([a-f0-9]{64}) -->$/.exec(envelope) : null;
        const expected = match ? crypto.createHmac("sha256", this.key()).update(match[1]).digest("hex") : "";
        // Literal examples and unissued markers remain ordinary user text.
        // Only our authenticated references may alter the attachment boundary.
        if (match && crypto.timingSafeEqual(Buffer.from(expected), Buffer.from(match[2]))) {
          const value = JSON.parse(Buffer.from(match[1], "base64url").toString("utf8"));
          if (value.version !== 1 || value.request !== digest(raw.slice(0, start)) || value.prefix !== fingerprint(input.slice(0, i)))
            throw new Error("Attachment reference does not match this request and history");
          if (!Array.isArray(value.files) || value.files.length > 16 || !client) throw new Error("Attachment references cannot be resolved by this runtime");
          const documentMessage = ChatMessage.from({ role: "user", content: value.files });
          copy.replaceText(raw.slice(0, start));
          for (const file of documentMessage.getFiles(client)) copy.appendFile(file);
        }
      }
      attachmentHistory.append(copy);
      const visible = ChatMessage.from(copy);
      if (client) visible.consumeFiles(client, file => !file.isImage());
      modelHistory.append(visible);
    }
    return { modelHistory, attachmentHistory };
  }
}

export const attachmentBoundary = new AttachmentBoundary();
export const preprocessAttachments: PromptPreprocessor = async (ctl, message) => {
  const config = ctl.getPluginConfig(directConfigSchematics);
  if (config.get("observeOnly") || !config.get("separateAttachments")) return message;
  ctl.guardAbort();
  const before = message.getFiles(ctl.client).filter(file => !file.isImage()).length;
  ctl.debug({ event: "attachment_boundary_probe", typedDocumentCount: before, inputChars: message.getText().length });
  if (!before) return message; // Never infer a lost boundary from document prose.
  const output = await attachmentBoundary.capture(message, await ctl.pullHistory(), ctl.client);
  ctl.debug({ event: "attachment_boundary_captured", documentCount: before });
  return output;
};
