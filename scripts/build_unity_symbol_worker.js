#!/usr/bin/env node
"use strict";
// Optional offline developer build using an explicitly supplied Roslyn toolchain.
// Normal deployment can use `dotnet publish unity-symbol-worker -o <output>`.
const fs = require("node:fs"), path = require("node:path"), cp = require("node:child_process");
const [dotnet, compiler, runtime, output] = process.argv.slice(2);
if (![dotnet, compiler, runtime, output].every(Boolean)) throw Error("Usage: node build_unity_symbol_worker.js <dotnet> <Roslyn-dir> <runtime-dir> <output>");
fs.mkdirSync(output, { recursive: true });
const source = path.resolve(__dirname, "../unity-symbol-worker/Program.cs");
const references = fs.readdirSync(runtime).filter(n => n.endsWith(".dll")).map(n => path.join(runtime, n));
const roslyn = ["Microsoft.CodeAnalysis.dll", "Microsoft.CodeAnalysis.CSharp.dll"].map(n => path.join(compiler, n));
const result = cp.spawnSync(dotnet, [path.join(compiler, "csc.dll"), "/nologo", "/target:exe", "/langversion:9", "/out:" + path.join(output, "UnitySymbolWorker.dll"), ...references.concat(roslyn).map(r => "/r:" + r), source], { encoding: "utf8" });
if (result.status !== 0) { console.error(result.stdout, result.stderr); process.exit(1); }
for (const lib of roslyn) fs.copyFileSync(lib, path.join(output, path.basename(lib)));
const version = path.basename(runtime);
fs.writeFileSync(path.join(output, "UnitySymbolWorker.runtimeconfig.json"), JSON.stringify({ runtimeOptions: { tfm: `net${version.split(".").slice(0, 2).join(".")}`, framework: { name: "Microsoft.NETCore.App", version } } }));
console.log(JSON.stringify({ status: "built", worker: path.join(output, "UnitySymbolWorker.dll"), runtime: version }));
