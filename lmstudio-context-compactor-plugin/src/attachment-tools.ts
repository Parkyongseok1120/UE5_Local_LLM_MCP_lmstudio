import {
  type Chat,
  type FileHandle,
  type LMStudioClient,
  rawFunctionTool,
  type Tool,
} from "@lmstudio/sdk";

const TOOL_NAME = "read_attached_document";
const DEFAULT_CHARS = 6000;
const MAX_CHARS = 12000;

type AttachmentContext = {
  tools: Array<Tool>;
  instruction: string;
  attachmentCount: number;
};

function boundedInteger(value: unknown, fallback: number, min: number, max: number): number {
  const parsed = Number(value);
  if (!Number.isFinite(parsed)) return fallback;
  return Math.max(min, Math.min(max, Math.trunc(parsed)));
}

function supportedDocument(file: FileHandle): boolean {
  return !file.isImage();
}

export function createAttachmentContext(
  history: Chat,
  client?: LMStudioClient,
): AttachmentContext {
  if (!client || !history.hasFiles()) return { tools: [], instruction: "", attachmentCount: 0 };
  let files: Array<FileHandle> = [];
  try {
    const seen = new Set<string>();
    files = history.getAllFiles(client).filter((file) => {
      if (!supportedDocument(file) || seen.has(file.identifier)) return false;
      seen.add(file.identifier);
      return true;
    });
  } catch {
    return { tools: [], instruction: "", attachmentCount: 0 };
  }
  if (files.length === 0) return { tools: [], instruction: "", attachmentCount: 0 };

  const parsedDocuments = new Map<string, Promise<{ content: string; parser: unknown }>>();
  const parse = (file: FileHandle, signal: AbortSignal) => {
    let pending = parsedDocuments.get(file.identifier);
    if (!pending) {
      pending = file.filesNamespace.parseDocument(file, { signal });
      parsedDocuments.set(file.identifier, pending);
      pending.catch(() => parsedDocuments.delete(file.identifier));
    }
    return pending;
  };
  const documentTool = rawFunctionTool({
    name: TOOL_NAME,
    description: "Read one bounded character range from an attached document. Use this when the supplied excerpts do not cover a section needed for the answer. The result identifies the source and exact parsed-text range; it does not imply that other ranges were read.",
    parametersJsonSchema: {
      type: "object",
      properties: {
        attachment: {
          type: "string",
          description: "Exact attachmentId or exact file name from the attachment manifest.",
        },
        startOffset: {
          type: "integer",
          minimum: 0,
          description: "Zero-based character offset in parsed document text. Defaults to 0.",
        },
        maxChars: {
          type: "integer",
          minimum: 500,
          maximum: MAX_CHARS,
          description: `Maximum characters to return. Defaults to ${DEFAULT_CHARS}.`,
        },
      },
      required: ["attachment"],
      additionalProperties: false,
    },
    implementation: async (params, ctx) => {
      const selector = String(params.attachment || "").trim();
      const matches = files.filter((file) => file.identifier === selector || file.name === selector);
      if (matches.length !== 1) {
        return {
          ok: false,
          errorCode: matches.length === 0 ? "ATTACHMENT_NOT_FOUND" : "ATTACHMENT_NAME_AMBIGUOUS",
          message: "Use one exact attachmentId from the attachment manifest.",
        };
      }
      const file = matches[0];
      const startOffset = boundedInteger(params.startOffset, 0, 0, 100_000_000);
      const maxChars = boundedInteger(params.maxChars, DEFAULT_CHARS, 500, MAX_CHARS);
      ctx.status(`Reading ${file.name}`);
      const parsed = await parse(file, ctx.signal);
      const content = String(parsed.content || "");
      const endOffset = Math.min(content.length, startOffset + maxChars);
      return {
        ok: true,
        observation: "attached_document_character_range",
        attachmentId: file.identifier,
        attachmentName: file.name,
        attachmentType: file.type,
        parser: parsed.parser,
        startOffset,
        endOffset,
        totalChars: content.length,
        hasMore: endOffset < content.length,
        content: content.slice(startOffset, endOffset),
      };
    },
  });
  const manifest = files.map((file) => ({
    attachmentId: file.identifier,
    name: file.name,
    type: file.type,
    sizeBytes: file.sizeBytes,
  }));
  const instruction = [
    "[Attached document scope]",
    `manifest=${JSON.stringify(manifest)}`,
    "An attachment's presence does not prove that its complete contents were read. Treat only supplied excerpts and read_attached_document results as observed evidence.",
    "If a missing section matters, call read_attached_document for a bounded range and identify the file and returned character range in the answer.",
    "Do not search the Unity or Unreal project for a chat attachment.",
  ].join("\n");
  return { tools: [documentTool], instruction, attachmentCount: files.length };
}

export const __test = { TOOL_NAME, DEFAULT_CHARS, MAX_CHARS };
