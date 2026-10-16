import { type LMStudioClient, type PluginContext } from "@lmstudio/sdk";
import { preprocessAttachments } from "./attachment-boundary";
import { preprocessWorkingContext } from "./working-context-boundary";
import { directConfigSchematics } from "./direct-config";
import { createPredictionLoopHandler } from "./prediction-loop";

export async function main(context: PluginContext, client?: LMStudioClient) {
  context.withConfigSchematics(directConfigSchematics);
  context.withPromptPreprocessor(async (ctl, message) => {
    const scoped = await preprocessWorkingContext(ctl, message);
    return typeof scoped === "string" ? scoped : preprocessAttachments(ctl, scoped);
  });
  context.withPredictionLoopHandler(createPredictionLoopHandler(undefined, client));
}
