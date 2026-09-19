import { type LMStudioClient, type PluginContext } from "@lmstudio/sdk";
import { directConfigSchematics } from "./direct-config";
import { createPredictionLoopHandler } from "./prediction-loop";

export async function main(context: PluginContext, client?: LMStudioClient) {
  context.withConfigSchematics(directConfigSchematics);
  context.withPredictionLoopHandler(createPredictionLoopHandler(undefined, client));
}
