"use strict";

const { createBundleCapability } = require("./direct-bundle-capability");
const { createDeleteCapabilities } = require("./direct-delete-capabilities");
const { createFileMutationCapabilities } = require("./direct-file-mutation-capabilities");

function createMutationCapabilities(context) {
  return Object.freeze({
    ...createFileMutationCapabilities(context),
    ...createBundleCapability(context),
    ...createDeleteCapabilities(context),
  });
}

module.exports = { createMutationCapabilities };
