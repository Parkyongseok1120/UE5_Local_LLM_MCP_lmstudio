import { LMStudioClient, type PluginContext } from "@lmstudio/sdk";

declare const process: any;

const client = new LMStudioClient({
  clientIdentifier: process.env.LMS_PLUGIN_CLIENT_IDENTIFIER,
  clientPasskey: process.env.LMS_PLUGIN_CLIENT_PASSKEY,
  baseUrl: process.env.LMS_PLUGIN_BASE_URL,
});

const host = client.plugins.getSelfRegistrationHost();
let configRegistered = false;
let generatorRegistered = false;

const context: PluginContext = {
  withConfigSchematics(configSchematics) {
    if (configRegistered) throw new Error("Config schematics already registered");
    configRegistered = true;
    host.setConfigSchematics(configSchematics);
    return context;
  },
  withGenerator(generator) {
    if (generatorRegistered) throw new Error("Generator already registered");
    generatorRegistered = true;
    host.setGenerator(generator);
    return context;
  },
  withGlobalConfigSchematics() { throw new Error("Global config is not used by this plugin"); },
  withPredictionLoopHandler() { throw new Error("Prediction loop handler is not used by this plugin"); },
  withPromptPreprocessor() { throw new Error("Prompt preprocessor is not used by this plugin"); },
  withToolsProvider() { throw new Error("Tools provider is not used by this plugin"); },
};

(globalThis as any).__LMS_PLUGIN_CONTEXT = true;

import("./../src/index.ts")
  .then(async module => module.main(context))
  .then(() => host.initCompleted())
  .catch(error => {
    console.error("Failed to execute the Unreal context compactor plugin.");
    console.error(error);
  });
