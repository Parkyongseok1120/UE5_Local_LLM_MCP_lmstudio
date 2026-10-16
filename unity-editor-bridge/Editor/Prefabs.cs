using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;
using UnityEngine.SceneManagement;
using Object = UnityEngine.Object;
namespace EvidenceFirst.UnityBridge
{
    internal static class Prefabs
    {
        sealed class Receipt { internal string Target, Path, Digest; internal DateTime Until; }
        static readonly Dictionary<string, Receipt> Receipts = new Dictionary<string, Receipt>();
        internal static string FileDigest(string path) {
            string full = PathSafety.Asset(path); if (!File.Exists(full)) throw new BridgeException("not_found", "Asset file is absent");
            if (new FileInfo(full).Length > 8 * 1048576) throw new BridgeException("asset_budget", "Asset exceeds 8 MiB operation bound");
            PathSafety.CheckInternal(full + ".meta");
            if (File.Exists(full + ".meta") && new FileInfo(full + ".meta").Length > 1048576) throw new BridgeException("asset_budget", "Metadata exceeds 1 MiB operation bound");
            return Bridge.Digest(Convert.ToBase64String(File.ReadAllBytes(full)) + "|" + (File.Exists(full + ".meta") ? File.ReadAllText(full + ".meta") : ""));
        }
        internal static string TreeDigest(Object target) {
            var go = target as GameObject;
            if (go == null) return Objects.Fingerprint(target);
            var all = go.GetComponentsInChildren<Transform>(true); if (all.Length > 2000) throw new BridgeException("hierarchy_budget", "Operation scope exceeds 2000 objects");
            var parts = new List<string>();
            foreach (var t in all) { parts.Add(Objects.Fingerprint(t.gameObject)); foreach (var c in t.GetComponents<Component>()) parts.Add(c == null ? "missing" : Objects.Fingerprint(c)); }
            if (parts.Count > 10000) throw new BridgeException("hierarchy_budget", "Operation scope exceeds 10000 components");
            return Bridge.Digest(String.Join("|", parts));
        }
        static string SourcePath(Object target) {
            string path = AssetDatabase.GetAssetPath(target); PathSafety.Asset(path, ".prefab");
            if (!EditorUtility.IsPersistent(target) || PrefabUtility.GetPrefabAssetType(target) == PrefabAssetType.Model) throw new BridgeException("prefab_source_required", "A writable prefab asset ObjectRef is required");
            return path;
        }
        internal static void CheckSourceIdle(string path) {
            if (EditorApplication.isPlayingOrWillChangePlaymode) throw new BridgeException("runtime_edit_disabled", "Prefab source edits require Edit Mode");
            var stage = PrefabStageUtility.GetCurrentPrefabStage();
            if (stage != null && stage.assetPath == path) throw new BridgeException("prefab_stage_open", "Close the target Prefab Stage explicitly before source mutation");
            var root = AssetDatabase.LoadAssetAtPath<GameObject>(path);
            if (root != null && root.GetComponentsInChildren<Transform>(true).Any(t => EditorUtility.IsDirty(t.gameObject) || t.GetComponents<Component>().Any(c => c != null && EditorUtility.IsDirty(c))))
                throw new BridgeException("source_dirty", "Existing dirty source edits must be saved or discarded by the user first");
        }
        static string Issue(Object target, string path) {
            foreach (var k in Receipts.Where(p => p.Value.Until < DateTime.UtcNow).Select(p => p.Key).ToArray()) Receipts.Remove(k);
            if (Receipts.Count >= 128) throw new BridgeException("receipt_capacity", "Prefab receipt capacity reached");
            string id = Guid.NewGuid().ToString("N"); Receipts[id] = new Receipt { Target = Objects.Ref(target).ToString(Formatting.None), Path = path, Digest = FileDigest(path), Until = DateTime.UtcNow.AddMinutes(10) }; return id;
        }
        static void Check(Object target, string path, string id) {
            if (id == null || !Receipts.TryGetValue(id, out var r) || r.Until < DateTime.UtcNow) throw new BridgeException("receipt_required", "Read the exact prefab source first");
            if (r.Path != path || r.Target != Objects.Ref(target).ToString(Formatting.None) || r.Digest != FileDigest(path)) throw new BridgeException("receipt_conflict", "Prefab source or target changed");
            CheckSourceIdle(path);
        }
        static Object MapToContents(Object original, GameObject asset, GameObject contents) {
            var go = original as GameObject ?? (original as Component)?.gameObject;
            if (go == null) throw new BridgeException("type_mismatch", "Prefab target must be an object or component");
            var indices = new List<int>(); var t = go.transform;
            while (t != asset.transform) { if (t.parent == null) throw new BridgeException("source_mismatch", "Target outside explicit prefab"); indices.Add(t.GetSiblingIndex()); t = t.parent; }
            var mapped = contents.transform; for (int i = indices.Count - 1; i >= 0; i--) { if (indices[i] >= mapped.childCount) throw new BridgeException("source_changed", "Loaded structure differs"); mapped = mapped.GetChild(indices[i]); }
            if (original is GameObject) return mapped.gameObject;
            int index = Array.IndexOf(go.GetComponents<Component>(), original as Component); var components = mapped.GetComponents<Component>();
            if (index < 0 || index >= components.Length || components[index] == null || components[index].GetType() != original.GetType()) throw new BridgeException("source_changed", "Loaded component structure differs");
            return components[index];
        }
        internal static JObject Read(JObject args) {
            var target = Objects.Resolve(args["target"]); string path = SourcePath(target);
            if ((string)args["action"] == "contents") {
                var root = AssetDatabase.LoadAssetAtPath<GameObject>(path); var rows = new List<JObject>();
                foreach (var t in root.GetComponentsInChildren<Transform>(true)) { rows.Add(Objects.Summary(t.gameObject)); foreach (var c in t.GetComponents<Component>()) if (c != null) rows.Add(Objects.Summary(c)); if (rows.Count > 10000) throw new BridgeException("query_budget_exceeded", "Prefab contents exceed limit"); }
                var page = Objects.Page(rows, args, FileDigest(path)); page["assetPath"] = path; return page;
            }
            var result = Objects.Read(args); result["sourceReceipt"] = Issue(target, path); result["sourceReceiptScope"] = "exact_asset_file_and_metadata"; result["assetPath"] = path; return result;
        }
        internal static JObject Mutate(JObject args) {
            if (EditorApplication.isPlayingOrWillChangePlaymode) throw new BridgeException("runtime_edit_disabled", "Prefab mutations require Edit Mode");
            string action = (string)args["action"];
            var target = Objects.Resolve(args["target"]);
            if (action == "instantiate") {
                string path = SourcePath(target); var prefab = target as GameObject;
                if (prefab == null || prefab != AssetDatabase.LoadMainAssetAtPath(path)) throw new BridgeException("prefab_root_required", "Instantiate requires main prefab root");
                PathSafety.Asset((string)args["scenePath"], ".unity"); var scene = SceneManager.GetSceneByPath((string)args["scenePath"]);
                if (!scene.isLoaded || EditorSceneManager.IsPreviewScene(scene)) throw new BridgeException("scene_unavailable", "Exact ordinary scene must already be loaded");
                return Operations.WithUndo(null, () => { var go = (GameObject)PrefabUtility.InstantiatePrefab(prefab, scene); Undo.RegisterCreatedObjectUndo(go, "Instantiate explicit prefab"); EditorSceneManager.MarkSceneDirty(scene);
                    return new JObject { ["status"] = "applied", ["target"] = Objects.Ref(go), ["receipt"] = Objects.Issue(go), ["dirty"] = true, ["saved"] = false }; });
            }
            if (action == "create") {
                var go = target as GameObject; if (go == null) throw new BridgeException("gameobject_required", "Create requires a scene GameObject");
                Objects.Editable(go, "sceneInstance"); Objects.CheckReceipt(go, (string)args["receipt"]);
                string path = (string)args["assetPath"], full = PathSafety.Asset(path, ".prefab");
                if ((bool?)args["save"] != true || (bool?)args["mustNotExist"] != true || File.Exists(full) || File.Exists(full + ".meta")) throw new BridgeException("creation_precondition_failed", "Explicit save and absent prefab/meta required");
                var asset = PrefabUtility.SaveAsPrefabAsset(go, path, out bool saved);
                return new JObject { ["status"] = saved ? "applied" : "outcome_unknown", ["saved"] = saved, ["target"] = Objects.Ref(asset), ["assetPath"] = path, ["sourceReceipt"] = saved ? Issue(asset, path) : null };
            }
            if (action == "patch") {
                string path = SourcePath(target); Check(target, path, (string)args["sourceReceipt"]);
                if ((bool?)args["save"] != true) throw new BridgeException("save_required", "Isolated source edit requires explicit save=true");
                var asset = AssetDatabase.LoadAssetAtPath<GameObject>(path); GameObject contents = null; bool saveAttempted = false;
                try {
                    contents = PrefabUtility.LoadPrefabContents(path); var mapped = MapToContents(target, asset, contents);
                    Check(target, path, (string)args["sourceReceipt"]);
                    var applied = Objects.PatchTarget(mapped, args, true); if ((string)applied["status"] != "applied") return applied;
                    Check(target, path, (string)args["sourceReceipt"]); saveAttempted = true;
                    PrefabUtility.SaveAsPrefabAsset(contents, path, out bool saved);
                    return new JObject { ["status"] = saved ? "applied" : "outcome_unknown", ["saved"] = saved, ["assetPath"] = path, ["target"] = args["target"], ["changed"] = applied["changed"], ["sourceReceipt"] = saved ? Issue(Objects.Resolve(args["target"]), path) : null, ["verification"] = "save_returned; read_source_explicitly", ["sideEffects"] = "project_callbacks_not_rolled_back" };
                } catch (Exception e) { var failure = Bridge.Error(e, saveAttempted ? "outcome_unknown" : "not_applied"); failure["sourceSaveAttempted"] = saveAttempted; failure["sideEffects"] = contents == null ? "none_observed" : "project_load_or_validation_callbacks_not_rolled_back"; return failure; }
                finally { if (contents != null) PrefabUtility.UnloadPrefabContents(contents); }
            }
            if (action != "apply" && action != "revert") throw new BridgeException("invalid_arguments", "Unknown prefab action");
            Objects.Editable(target, "sceneInstance"); Objects.CheckReceipt(target, (string)args["receipt"]);
            if (!PrefabUtility.IsPartOfPrefabInstance(target)) throw new BridgeException("not_prefab_instance", "Exact instance target required");
            var paths = args["propertyPaths"] as JArray; if (paths == null || paths.Count < 1 || paths.Count > 32) throw new BridgeException("invalid_arguments", "1..32 exact property paths required");
            string destination = null;
            if (action == "apply") {
                destination = (string)args["assetPath"]; PathSafety.Asset(destination, ".prefab");
                var source = PrefabUtility.GetCorrespondingObjectFromSourceAtPath(target, destination);
                if (source == null || !JToken.DeepEquals(Objects.Ref(source), args["sourceTarget"])) throw new BridgeException("source_mismatch", "Select the exact source at the explicit nested/variant destination");
                Check(source, destination, (string)args["sourceReceipt"]);
                if ((bool?)args["save"] != true) throw new BridgeException("save_required", "Apply saves the selected source; explicit save=true required");
            }
            using (var so = new SerializedObject(target)) {
                foreach (string p in paths.Values<string>()) { var field = so.FindProperty(p); if (field == null || !field.prefabOverride) throw new BridgeException("override_required", "Exact overridden property required"); if (field.isArray || p.Contains(".Array.") || field.propertyType == SerializedPropertyType.ManagedReference || p == "m_Script") throw new BridgeException("scope_unavailable", "Use source patch for arrays/managed-reference structure; override operations do not expand scope"); }
                if (action == "revert") Approvals.Consume("unity_prefab", args);
                return Operations.WithUndo(target, () => {
                    int count = 0;
                    try { foreach (string p in paths.Values<string>()) { var field = so.FindProperty(p); if (action == "apply") PrefabUtility.ApplyPropertyOverride(field, destination, InteractionMode.UserAction); else PrefabUtility.RevertPropertyOverride(field, InteractionMode.UserAction); count++; so.Update(); } }
                    catch (Exception e) { var failed = Bridge.Error(e, "partially_applied"); failed["appliedPropertyCount"] = count; failed["saved"] = action == "apply"; return failed; }
                    return new JObject { ["status"] = "applied", ["changed"] = count > 0, ["saved"] = action == "apply", ["sourceAssetPath"] = destination, ["scope"] = action == "apply" ? "explicit_source_properties" : "instance_overrides", ["receipt"] = Objects.Issue(target), ["dirty"] = EditorUtility.IsDirty(target) };
                });
            }
        }
    }
}
