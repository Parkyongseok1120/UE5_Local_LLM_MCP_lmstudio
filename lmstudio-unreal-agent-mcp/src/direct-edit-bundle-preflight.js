"use strict";

const { normalizedBundlePath } = require("./direct-edit-bundle-plan");

function initialExpectedHashes(bundle) {
  const hashes = new Map();
  for (const patch of bundle.patches || []) {
    const relativePath = normalizedBundlePath(patch, "patch");
    const expectedHash = patch.expectedHash.toLowerCase();
    if (!hashes.has(relativePath)) hashes.set(relativePath, expectedHash);
  }
  return hashes;
}

function validateBundleBaselineHashes(bundle, baseline) {
  for (const [relativePath, expectedHash] of initialExpectedHashes(bundle)) {
    const snapshot = baseline.get(relativePath);
    if (!snapshot || !snapshot.existedBefore || snapshot.preHash.toLowerCase() !== expectedHash) {
      const error = new Error(
        `FILE_VERSION_CONFLICT: ${relativePath} changed before the atomic bundle acquired all locks`,
      );
      error.mutationFailure = {
        errorCode: "FILE_VERSION_CONFLICT",
        relativePath,
        expectedHash,
        currentHash: snapshot?.preHash || "",
      };
      throw error;
    }
  }
}

module.exports = {
  initialExpectedHashes,
  validateBundleBaselineHashes,
};
