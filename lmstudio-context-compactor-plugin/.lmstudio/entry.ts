import { LMStudioClient, type PluginContext } from "@lmstudio/sdk";

declare const process: any;

const client = new LMStudioClient({
  clientIdentifier: process.env.LMS_PLUGIN_CLIENT_IDENTIFIER,
  clientPasskey: process.env.LMS_PLUGIN_CLIENT_PASSKEY,
  baseUrl: process.env.LMS_PLUGIN_BASE_URL,
});

const host = client.plugins.getSelfRegistrationHost();
let configRegistered = false;
let predictionLoopRegistered = false;

const context: PluginContext = {
  withConfigSchematics(configSchematics) {
    if (configRegistered) throw new Error("Config schematics already registered");
    configRegistered = true;
    host.setConfigSchematics(configSchematics);
    return context;
  },
  withPredictionLoopHandler(handler) {
    if (predictionLoopRegistered) throw new Error("Prediction loop handler already registered");
    predictionLoopRegistered = true;
    host.setPredictionLoopHandler(handler);
    return context;
  },
  withGlobalConfigSchematics() { throw new Error("Global config is not used by this plugin"); },
  withPromptPreprocessor() { throw new Error("Prompt preprocessor is not used by this plugin"); },
  withToolsProvider() { throw new Error("Tools provider is not used by this plugin"); },
  withGenerator() { throw new Error("Generator proxy is not used by the Direct context compactor"); },
};

(globalThis as any).__LMS_PLUGIN_CONTEXT = true;

import("./../src/index.ts")
  .then(async module => module.main(context))
  .then(() => host.initCompleted())
  .catch(error => {
    console.error("Failed to execute the Unreal context compactor plugin.");
    console.error(error);
  });
