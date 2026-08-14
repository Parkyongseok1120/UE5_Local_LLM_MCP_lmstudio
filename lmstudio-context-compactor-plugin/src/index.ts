import { type PluginContext } from "@lmstudio/sdk";
import path from "node:path";
import { configSchematics } from "./config";
import { generate } from "./generator";
import { verifyRuntimeComponent } from "./runtime-identity";

export async function main(context: PluginContext) {
  const runtimeStatus = verifyRuntimeComponent({ componentRoot: path.resolve(__dirname, "..") });
  console.error(JSON.stringify({
    event: "control_runtime_verified",
    server: "unreal-context-compactor",
    runtimeComponent: runtimeStatus.running,
    runtimeVerified: runtimeStatus.verified === true,
  }));
  context.withConfigSchematics(configSchematics);
  context.withGenerator(generate);
}
