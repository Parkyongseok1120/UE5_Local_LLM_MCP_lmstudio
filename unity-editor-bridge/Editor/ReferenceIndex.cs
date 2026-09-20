using System;
using System.Linq;
using System.Collections.Generic;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEngine;
namespace EvidenceFirst.UnityBridge
{
    internal static class ReferenceIndex
    {
        static readonly EvidenceStore Store = new EvidenceStore("references");
        internal static JObject Call(JObject args)
        {
            string action = (string)args["action"];
            if (action == "release") return Store.Release((string)args["indexId"]);
            if (action == "query") {
                var index = Store.Get((string)args["indexId"]); var rows = ((JArray)index["edges"]).OfType<JObject>();
                if (args["endpoint"] != null) { string side = (string)args["direction"] == "reverse" ? "to" : "from"; rows = rows.Where(e => JToken.DeepEquals(e[side], args["endpoint"])); }
                var result = Objects.Page(rows.ToList(), args, Bridge.Digest((string)index["id"] + args["endpoint"] + (string)args["direction"]));
                foreach (string key in new[] { "id", "scope", "kind", "startedAt", "endedAt", "editorSessionId", "playSessionId", "domainGeneration", "omissions", "completeWithinScope" }) result[key] = index[key]?.DeepClone();
                result["freshness"] = "immutable_collection_not_live"; result["unusedConclusion"] = "not_inferred"; return result;
            }
            if (action != "collect") throw new BridgeException("invalid_arguments", "Expected collect/query/release");
            var edges = new JArray(); var omissions = new JArray(); string started = DateTime.UtcNow.ToString("O"), kind = (string)args["kind"];
            var scope = new JObject();
            void Add(JObject row) { if (edges.Count >= 2000) throw new BridgeException("reference_capacity", "Narrow collection scope (maximum 2000 edges)"); edges.Add(row); }
            if (kind == "asset_dependency") {
                var paths = args["paths"] as JArray; if (paths == null || paths.Count < 1 || paths.Count > 100) throw new BridgeException("invalid_scope", "Explicit 1..100 asset paths required");
                scope["paths"] = paths.DeepClone(); scope["recursive"] = (bool?)args["recursive"] ?? false;
                foreach (string p in paths) {
                    PathSafety.Asset(p);
                    foreach (string dependency in AssetDatabase.GetDependencies(p, (bool)scope["recursive"])) if (dependency != p) Add(new JObject { ["kind"] = kind, ["from"] = p, ["to"] = dependency, ["evidence"] = "AssetDatabase.GetDependencies; path-level only" });
                }
            } else if (kind == "serialized_reference") {
                var targets = args["targets"] as JArray; if (targets == null || targets.Count < 1 || targets.Count > 32) throw new BridgeException("invalid_scope", "Explicit 1..32 ObjectRefs required");
                scope["targets"] = targets.DeepClone(); scope["closedScenes"] = "not_opened_or_inspected";
                foreach (var r in targets) {
                    try {
                        var target = Objects.Resolve(r);
                        using (var so = new SerializedObject(target)) {
                            so.Update(); var p = so.GetIterator(); int count = 0;
                            while (p.Next(true)) {
                                if (++count > 10000) { omissions.Add(new JObject { ["target"] = r.DeepClone(), ["reason"] = "property_limit" }); break; }
                                if (p.propertyType != SerializedPropertyType.ObjectReference) continue;
                                if (p.objectReferenceValue != null) Add(new JObject { ["kind"] = kind, ["from"] = r.DeepClone(), ["to"] = Objects.Ref(p.objectReferenceValue), ["propertyPath"] = p.propertyPath, ["evidence"] = "SerializedProperty.objectReferenceValue" });
                                else if (p.objectReferenceInstanceIDValue != 0) omissions.Add(new JObject { ["target"] = r.DeepClone(), ["propertyPath"] = p.propertyPath, ["reason"] = "missing_reference" });
                            }
                        }
                        var script = target is MonoBehaviour mb ? MonoScript.FromMonoBehaviour(mb) : target is ScriptableObject soTarget ? MonoScript.FromScriptableObject(soTarget) : target as MonoScript;
                        if (script != null && script.GetClass() != null) Add(new JObject { ["kind"] = "compiled_script_link", ["from"] = r.DeepClone(), ["to"] = Objects.Ref(script), ["scriptGuid"] = AssetDatabase.AssetPathToGUID(AssetDatabase.GetAssetPath(script)), ["compiledType"] = script.GetClass().AssemblyQualifiedName, ["evidence"] = "MonoScript.GetClass + explicit component/script identity", ["sourceAssemblyVerification"] = "unknown" });
                    } catch (BridgeException e) { if (e.Code == "reference_capacity") throw; omissions.Add(new JObject { ["target"] = r.DeepClone(), ["reason"] = e.Code }); }
                }
            } else if (kind == "observed_runtime_reference") {
                var targets = args["targets"] as JArray; if (targets == null || targets.Count < 1 || targets.Count > 32) throw new BridgeException("invalid_scope", "Explicit runtime endpoints required");
                scope["targets"] = targets.DeepClone(); scope["coverage"] = "retained subscribed instrumentation events only; uninstrumented dynamic relationships unknown";
                foreach (var edge in DebugAdapters.RuntimeReferences()) if (targets.Any(t => JToken.DeepEquals(t, edge["from"]) || JToken.DeepEquals(t, edge["to"]))) Add(edge);
            } else throw new BridgeException("invalid_kind", "Use unity_symbols.usages for compiler-based code references");
            var indexNew = new JObject { ["projectIdentity"] = Bridge.ProjectId, ["kind"] = kind, ["scope"] = scope, ["edges"] = edges, ["omissions"] = omissions,
                ["completeWithinScope"] = omissions.Count == 0 && kind != "observed_runtime_reference", ["startedAt"] = started, ["endedAt"] = DateTime.UtcNow.ToString("O"),
                ["editorSessionId"] = Bridge.Session, ["domainGeneration"] = Bridge.Generation, ["playSessionId"] = Bridge.PlaySession };
            string id = Store.Put(indexNew);
            return new JObject { ["status"] = "collected", ["indexId"] = id, ["edgeCount"] = edges.Count, ["omissionCount"] = omissions.Count, ["scope"] = scope };
        }
    }
}
