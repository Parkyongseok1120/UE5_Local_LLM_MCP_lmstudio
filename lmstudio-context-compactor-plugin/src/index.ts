import { type PluginContext } from "@lmstudio/sdk";
import { configSchematics } from "./config";
import { generate } from "./generator";

export async function main(context: PluginContext) {
  context.withConfigSchematics(configSchematics);
  context.withGenerator(generate);
}
