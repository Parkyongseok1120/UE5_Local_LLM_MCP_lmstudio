"use strict";
// Explicit opt-in evaluation. SDK 1.5.0's raw load-config conversion is used only
// here to preserve every original load setting, including custom templates/KV types.
const fs = require("node:fs"), path = require("node:path"), os = require("node:os");
const { execFileSync, spawnSync } = require("node:child_process");
const { LMStudioClient } = require("@lmstudio/sdk");
const arg = key => { const i = process.argv.indexOf(key); return i < 0 ? "" : process.argv[i + 1]; };
async function main() {
  const identifier = arg("--model"), output = arg("--output");
  if (!identifier || !output || !process.argv.includes("--temporary-reload")) throw new Error("Use --model <id> --output <json> --temporary-reload");
  const lms = path.join(os.homedir(), ".lmstudio", "bin", process.platform === "win32" ? "lms.exe" : "lms");
  const inventory = JSON.parse(execFileSync(lms, ["ps", "--json"], { encoding: "utf8" }));
  const before = inventory.find(m => m.identifier === identifier);
  if (!before || before.status !== "idle" || before.queued !== 0 || before.engineConfigFileEnabled) throw new Error("An idle ordinary loaded model with no queued requests is required");
  const client = new LMStudioClient();
  const original = await client.llm.model(identifier);
  const config = await original.getLoadConfig();
  const snapshot = path.join(os.tmpdir(), `harness-load-restore-${Date.now()}.json`);
  fs.writeFileSync(snapshot, JSON.stringify({ before, config }, null, 2));
  const conversion = client.llm.loadConfigToKVConfig;
  if (typeof conversion !== "function") throw new Error("SDK load-config restoration hook unavailable; no reload attempted");
  async function loadExact(id, loadConfig) {
    client.llm.loadConfigToKVConfig = () => loadConfig;
    try { return await client.llm.load(before.modelKey, { identifier: id, config: {}, verbose: false, signal: AbortSignal.timeout(180000) }); }
    finally { client.llm.loadConfigToKVConfig = conversion; }
  }
  let unloaded = false, temporary, testStatus = null, restored = false, error = null, addedDefaultFields = [];
  try {
    await original.unload(); unloaded = true;
    const reduced = { fields: config.fields.map(field => field.key === "llm.load.contextLength" ? { ...field, value: 32768 } : field) };
    temporary = await loadExact(`harness-context-32768-${Date.now()}`, reduced);
    console.log("Temporary 32768 model loaded; running live harness fixture.");
    const test = spawnSync(process.execPath, [path.join(__dirname, "eval-percentage-budget.cjs"), "--model", temporary.identifier,
      "--expect-context", "32768", "--output", output], { stdio: "inherit", timeout: 330000 });
    testStatus = test.status;
    if (test.error) throw test.error;
  } catch (caught) { error = String(caught?.stack || caught); }
  finally {
    if (temporary) await temporary.unload();
    if (unloaded) {
      const model = await loadExact(identifier, config);
      const after = await model.getLoadConfig();
      const values = new Map(after.fields.map(field => [field.key, field.value]));
      addedDefaultFields = after.fields.filter(field => !config.fields.some(old => old.key === field.key));
      // The backend can materialize additional defaults (e.g. an unused vLLM autoFit flag).
      restored = config.fields.every(field => JSON.stringify(values.get(field.key)) === JSON.stringify(field.value))
        && await model.getContextLength() === before.contextLength;
    }
    fs.writeFileSync(`${output}.restore.json`, JSON.stringify({ originalIdentifier: identifier, originalContextLength: before.contextLength,
      originalParallel: before.parallel, restored, addedDefaultFields, testStatus, error, recoverySnapshot: snapshot }, null, 2) + "\n");
    console.log(JSON.stringify({ restored, originalContextLength: before.contextLength, testStatus, error }));
  }
  if (!restored || testStatus !== 0 || error) process.exitCode = 1;
}
main().catch(error => { console.error(error); process.exitCode = 1; });
