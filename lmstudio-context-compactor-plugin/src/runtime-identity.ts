import crypto from "node:crypto";
import fs from "node:fs";
import path from "node:path";
import { LOADED_BUILD_ID } from "./generated-runtime-identity";

export type RuntimeInstallationIdentity = {
  loadedBuildId: string;
  installedBuildId: string;
  loadedMatchesInstalled: boolean | null;
  runtimeSourceRevision: string | number;
  installedSourceFingerprint: string;
  installedDistFingerprint: string;
};

export let cachedRuntimeInstallationIdentity: RuntimeInstallationIdentity | null = null;

export function hashInstalledSourceTree(root: string): string {
  const files: Array<string> = [];
  const visit = (directory: string) => {
    for (const entry of fs.readdirSync(directory, { withFileTypes: true })) {
      const absolute = path.join(directory, entry.name);
      if (entry.isDirectory()) visit(absolute);
      else if (entry.isFile()) files.push(absolute);
    }
  };
  visit(root);
  const digest = crypto.createHash("sha256");
  for (const absolute of files.sort((left, right) => left.localeCompare(right))) {
    digest.update(path.relative(root, absolute).replaceAll("\\", "/"));
    digest.update("\0");
    digest.update(fs.readFileSync(absolute));
    digest.update("\0");
  }
  return digest.digest("hex");
}

export function runtimeInstallationIdentity(): RuntimeInstallationIdentity {
  // Called once per execution: disk installation may have changed while this
  // module remained loaded. Only the embedded ID is an execution-code fact.
  const pluginRoot = path.resolve(__dirname, "..");
  let runtimeSourceRevision: string | number = "unknown";
  let installedSourceFingerprint = "unavailable";
  let installedDistFingerprint = "unavailable";
  let installedBuildId = "unavailable";
  try {
    const manifest = JSON.parse(fs.readFileSync(path.join(pluginRoot, "manifest.json"), "utf8")) as {
      revision?: unknown;
    };
    if (typeof manifest.revision === "string" || typeof manifest.revision === "number") {
      runtimeSourceRevision = manifest.revision;
    }
  } catch { /* An incomplete installation is reported as unknown. */ }
  try { installedSourceFingerprint = hashInstalledSourceTree(path.join(pluginRoot, "src")); } catch { /* unavailable */ }
  try {
    const generated = fs.readFileSync(path.join(pluginRoot, "src/generated-runtime-identity.ts"), "utf8");
    installedBuildId = /ef-build-v2-[a-f0-9]{64}/.exec(generated)?.[0] || "unavailable";
  } catch { /* The loaded constant remains valid when disk metadata is unavailable. */ }
  try {
    installedDistFingerprint = hashInstalledSourceTree(path.join(pluginRoot, "dist"));
  } catch { /* unavailable */ }
  cachedRuntimeInstallationIdentity = {
    loadedBuildId: LOADED_BUILD_ID, installedBuildId,
    loadedMatchesInstalled: installedBuildId === "unavailable" ? null : installedBuildId === LOADED_BUILD_ID,
    runtimeSourceRevision,
    installedSourceFingerprint,
    installedDistFingerprint,
  };
  return cachedRuntimeInstallationIdentity;
}
