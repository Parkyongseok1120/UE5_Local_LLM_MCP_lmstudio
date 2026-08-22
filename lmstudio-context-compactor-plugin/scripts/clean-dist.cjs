#!/usr/bin/env node
"use strict";

const fs = require("node:fs");
const path = require("node:path");

const componentRoot = path.resolve(__dirname, "..");
const distRoot = path.resolve(componentRoot, "dist");

if (path.dirname(distRoot) !== componentRoot || path.basename(distRoot) !== "dist") {
  throw new Error(`Refusing to clean an unexpected build directory: ${distRoot}`);
}

fs.rmSync(distRoot, { recursive: true, force: true });
