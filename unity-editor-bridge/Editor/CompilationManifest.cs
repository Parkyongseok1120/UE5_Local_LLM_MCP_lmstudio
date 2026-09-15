using System;
using System.IO;
using System.Linq;
using System.Collections.Generic;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEditor.Compilation;
using UnityEditor.PackageManager;
using PackageInfo = UnityEditor.PackageManager.PackageInfo;

namespace EvidenceFirst.UnityBridge
{
    internal static class CompilationManifest
    {
        internal static JObject Export(JObject args)
        {
            var all = CompilationPipeline.GetAssemblies(AssembliesType.Editor);
            if ((string)args["action"] == "list") return new JObject { ["status"] = "observed", ["assemblies"] = new JArray(all.Select(a => a.name)), ["context"] = "current_editor_target" };
            var names = ((JArray)args["assemblies"]).Values<string>().Distinct().ToArray();
            if (names.Length == 0 || names.Length > 32) throw new BridgeException("invalid_scope", "Select 1..32 assembly names explicitly");
            var selected = new Dictionary<string, UnityEditor.Compilation.Assembly>();
            Action<string> add = null;
            add = name => {
                if (selected.ContainsKey(name)) return;
                var a = all.FirstOrDefault(x => x.name == name);
                if (a == null) throw new BridgeException("assembly_missing", name);
                selected.Add(name, a);
                if (selected.Count > 256) throw new BridgeException("manifest_capacity", "Dependency closure exceeds 256 assemblies");
                foreach (var dependency in a.assemblyReferences) add(dependency.name);
            };
            foreach (var name in names) add(name);
            if (selected.Values.Sum(a => a.sourceFiles.Length) > 5000) throw new BridgeException("manifest_capacity", "Source closure exceeds 5000 files");
            string Physical(string source) {
                if (Path.IsPathRooted(source)) return Path.GetFullPath(source);
                var package = source.StartsWith("Packages/", StringComparison.Ordinal) ? PackageInfo.FindForAssetPath(source) : null;
                return package == null ? Path.GetFullPath(Path.Combine(Bridge.Root, source)) : Path.Combine(package.resolvedPath, source.Substring(package.assetPath.Length).TrimStart('/'));
            }
            var rows = new JArray();
            foreach (var a in selected.Values)
            {
                var o = a.compilerOptions;
                rows.Add(new JObject { ["name"] = a.name, ["outputPath"] = Path.GetFullPath(a.outputPath),
                    ["sources"] = new JArray(a.sourceFiles.Select(s => new JObject { ["path"] = Physical(s), ["projectPath"] = s.Replace('\\', '/'), ["guid"] = AssetDatabase.AssetPathToGUID(s) })),
                    ["references"] = new JArray(a.compiledAssemblyReferences.Select(Path.GetFullPath)), ["assemblyReferences"] = new JArray(a.assemblyReferences.Select(d => d.name)),
                    ["defines"] = new JArray(a.defines), ["arguments"] = new JArray(o.AdditionalCompilerArguments ?? new string[0]),
                    ["responseFiles"] = new JArray((o.ResponseFiles ?? new string[0]).Select(Path.GetFullPath)), ["analyzers"] = new JArray(o.RoslynAnalyzerDllPaths ?? new string[0]),
                    ["languageVersion"] = o.LanguageVersion, ["allowUnsafe"] = o.AllowUnsafeCode, ["optimization"] = o.CodeOptimization.ToString(), ["apiCompatibility"] = o.ApiCompatibilityLevel.ToString() });
            }
            var manifest = new JObject { ["canonicalProjectRoot"] = Bridge.Root, ["projectIdentity"] = Bridge.ProjectId, ["editorSessionId"] = Bridge.Session,
                ["domainGeneration"] = Bridge.Generation, ["compilationId"] = Observations.CompilationId, ["compiling"] = EditorApplication.isCompiling,
                ["context"] = "current_editor_target", ["selectedAssemblies"] = new JArray(names), ["assemblies"] = rows };
            string id = Bridge.Digest(manifest.ToString(Formatting.None)); manifest["manifestId"] = id;
            var text = manifest.ToString(Formatting.None);
            if (System.Text.Encoding.UTF8.GetByteCount(text) > 16 * 1024 * 1024) throw new BridgeException("manifest_capacity", "Manifest exceeds 16 MiB");
            PathSafety.WritePrivate(Path.Combine(Bridge.StateDirectory, "analysis-manifest.json"), text);
            return new JObject { ["status"] = "observed", ["manifestId"] = id, ["selectedAssemblies"] = new JArray(names), ["compiling"] = EditorApplication.isCompiling };
        }
    }
}
