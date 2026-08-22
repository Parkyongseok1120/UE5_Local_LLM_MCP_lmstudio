import { type PluginContext } from "@lmstudio/sdk";
import { directConfigSchematics } from "./direct-config";
import { handlePredictionLoop } from "./prediction-loop";

export async function main(context: PluginContext) {
  context.withConfigSchematics(directConfigSchematics);
  context.withPredictionLoopHandler(handlePredictionLoop);
}
