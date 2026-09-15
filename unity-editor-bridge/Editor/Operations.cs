using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Text.RegularExpressions;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEditor.Compilation;
using UnityEditor.SceneManagement;
using UnityEngine;
using UnityEngine.SceneManagement;
using Object = UnityEngine.Object;

namespace EvidenceFirst.UnityBridge
{
    internal static class Operations
    {
        static string Journal => Path.Combine(Bridge.StateDirectory, "operations");
        internal static void Recover()
        {
            PathSafety.CheckInternal(Journal); Directory.CreateDirectory(Journal); PathSafety.Private(Journal, true);
            foreach (var file in Directory.GetFiles(Journal, "*.json").Take(1001))
            {
                PathSafety.CheckInternal(file);
                var record = JObject.Parse(File.ReadAllText(file));
                if ((string)record["status"] == "running")
                { record["status"] = "outcome_unknown"; record["result"] = new JObject { ["status"] = "outcome_unknown", ["operationId"] = record["operationId"], ["message"] = "Editor/domain ended before result was recorded; request will not be replayed" }; PathSafety.WritePrivate(file, record.ToString(Formatting.None)); }
            }
        }
        static string Required(JObject args, string field)
        {
            if (args[field]?.Type != JTokenType.String || String.IsNullOrEmpty((string)args[field])) throw new BridgeException("invalid_arguments", field + " must be a nonempty string");
            return (string)args[field];
        }
        static string OperationPath(string id)
        {
            if (id == null || !Regex.IsMatch(id, "^[A-Za-z0-9_-]{8,100}$")) throw new BridgeException("invalid_operation_id", "operationId must contain 8..100 letters, digits, underscores or hyphens");
            var result = Path.Combine(Journal, id + ".json"); PathSafety.CheckInternal(result); return result;
        }
        internal static JObject Dispatch(string method, JObject args)
        {
            if (method == "unity_status") return Bridge.Status();
            if (method == "unity_object_read") return Objects.Read(args);
            if (method == "unity_find") return Find(args);
            if (method == "unity_logs") return Observations.Read(args);
            if (method == "unity_prefab") return Prefab(args);
            if (method == "unity_tests") return new JObject { ["status"] = "unavailable", ["errorCode"] = "capability_unavailable", ["testStatus"] = "not_run", ["reason"] = "Optional test adapter is not implemented in this alpha" };
            if (method == "unity_scene" && (string)args["action"] == "list") return Scenes(args);
            if (method == "unity_operation")
            {
                var file = OperationPath(Required(args, "operationId"));
                if (!File.Exists(file)) return new JObject { ["status"] = "unknown", ["operationId"] = args["operationId"], ["message"] = "No retained execution record; do not infer non-execution after journal loss" };
                var record = JObject.Parse(File.ReadAllText(file));
                if ((string)args["action"] == "cancel") return new JObject { ["status"] = "not_cancelled", ["operationId"] = args["operationId"], ["currentStatus"] = record["status"], ["reason"] = "Synchronous operations cannot be safely interrupted; no running API was aborted" };
                if ((string)args["action"] != "get") throw new BridgeException("invalid_arguments", "Unknown operation action");
                return (JObject)record["result"];
            }
            bool edit = method == "unity_object_patch" || method == "unity_scene" || method == "unity_asset";
            bool execute = method == "unity_editor";
            if (!edit && !execute) throw new BridgeException("capability_unavailable", "Operation is not implemented");
            if (edit && !Bridge.EditAllowed || execute && !Bridge.ExecuteAllowed) throw new BridgeException("permission_denied", "Enable the required permission in the Editor Tools/Evidence First menu");
            string id = Required(args, "operationId"), filePath = OperationPath(id);
            string digest = Bridge.Digest(Bridge.ProjectId + Bridge.Session + method + Canonical(args));
            if (File.Exists(filePath))
            {
                var previous = JObject.Parse(File.ReadAllText(filePath));
                if ((string)previous["digest"] != digest) throw new BridgeException("operation_conflict", "operationId was already bound to different content or Editor session");
                return previous["result"] as JObject ?? new JObject { ["status"] = "outcome_unknown", ["operationId"] = id };
            }
            if (Directory.GetFiles(Journal, "*.json").Length >= 1000) throw new BridgeException("operation_capacity", "Journal capacity reached; records are not evicted automatically");
            if (EditorApplication.isCompiling || EditorApplication.isUpdating) throw new BridgeException("editor_busy", "Editor is compiling/importing; request was not executed");
            var recordNew = new JObject { ["operationId"] = id, ["digest"] = digest, ["status"] = "running" };
            PathSafety.WritePrivate(filePath, recordNew.ToString(Formatting.None));
            JObject result;
            try
            {
                if (method == "unity_object_patch") result = Objects.Patch(args);
                else if (method == "unity_scene") result = SceneAction(args);
                else if (method == "unity_asset") result = AssetAction(args);
                else result = EditorAction(args);
            }
            catch (BridgeException e) { result = Bridge.Error(e); }
            catch (Exception e) { result = Bridge.Error(e, "outcome_unknown"); }
            result["operationId"] = id;
            recordNew["status"] = result["status"]; recordNew["result"] = result;
            try { PathSafety.WritePrivate(filePath, recordNew.ToString(Formatting.None)); }
            catch { return new JObject { ["status"] = "outcome_unknown", ["operationId"] = id, ["errorCode"] = "journal_write_failed" }; }
            return result;
        }
        static string Canonical(JToken value)
        {
            if (value is JObject o) return "{" + String.Join(",", o.Properties().OrderBy(p => p.Name, StringComparer.Ordinal).Select(p => JsonConvert.ToString(p.Name) + ":" + Canonical(p.Value))) + "}";
            if (value is JArray a) return "[" + String.Join(",", a.Select(Canonical)) + "]";
            return value.ToString(Formatting.None);
        }
        internal static JObject WithUndo(Object target, Func<JObject> action)
        {
            Undo.IncrementCurrentGroup(); int group = Undo.GetCurrentGroup(); Undo.SetCurrentGroupName("Evidence First explicit edit");
            if (target != null) Undo.RegisterCompleteObjectUndo(target, "Evidence First explicit edit");
            try { var result = action(); Undo.CollapseUndoOperations(group); return result; }
            catch (Exception error)
            {
                // Undo may restore serialized state, but arbitrary callbacks are not reversible.
                bool restored = false;
                try { Undo.RevertAllDownToGroup(group); restored = true; } catch { }
                var result = Bridge.Error(error, "partially_applied"); result["undoAttempted"] = true; result["undoCompleted"] = restored;
                result["sideEffects"] = "unknown"; return result;
            }
            finally { Undo.IncrementCurrentGroup(); }
        }
        static List<GameObject> LoadedObjects()
        {
            var result = new List<GameObject>();
            for (int i = 0; i < SceneManager.sceneCount; i++)
            {
                var scene = SceneManager.GetSceneAt(i);
                if (!scene.isLoaded || EditorSceneManager.IsPreviewScene(scene)) continue;
                foreach (var root in scene.GetRootGameObjects())
                    foreach (var transform in root.GetComponentsInChildren<Transform>(true))
                    { result.Add(transform.gameObject); if (result.Count > 20000) throw new BridgeException("query_budget_exceeded", "Loaded hierarchy exceeds the current scan limit"); }
            }
            return result;
        }
        static Type FindType(string name, Type baseType)
        {
            // Type lookup only, never arbitrary method invocation or getter evaluation.
            var matches = TypeCache.GetTypesDerivedFrom(baseType).Where(t => t.AssemblyQualifiedName == name || t.FullName == name).ToArray();
            if (matches.Length != 1 || matches[0].IsAbstract || matches[0].ContainsGenericParameters) throw new BridgeException("type_unavailable", "Specify a unique concrete compiled type");
            return matches[0];
        }
        static JObject Find(JObject args)
        {
            var rows = new List<JObject>(); string query = (string)args["query"] ?? "", kind = Required(args, "kind");
            if (kind == "assets")
            {
                // AssetDatabase filter is explicit caller data, never a natural-language plan.
                var guids = AssetDatabase.FindAssets(query);
                if (guids.Length > 10000) throw new BridgeException("query_budget_exceeded", "Narrow the asset query");
                foreach (var guid in guids.OrderBy(g => g, StringComparer.Ordinal))
                {
                    string p = AssetDatabase.GUIDToAssetPath(guid);
                    rows.Add(new JObject { ["guid"] = guid, ["path"] = p });
                    // Only load the bounded page's sub-assets via explicit object reads later.
                }
            }
            else if (kind == "types")
            {
                foreach (var type in TypeCache.GetTypesDerivedFrom<Object>().Where(t => (t.FullName ?? "").IndexOf(query, StringComparison.OrdinalIgnoreCase) >= 0).OrderBy(t => t.AssemblyQualifiedName, StringComparer.Ordinal).Take(10001))
                    rows.Add(new JObject { ["type"] = type.AssemblyQualifiedName, ["abstract"] = type.IsAbstract, ["scriptableObject"] = typeof(ScriptableObject).IsAssignableFrom(type), ["component"] = typeof(Component).IsAssignableFrom(type) });
                if (rows.Count > 10000) throw new BridgeException("query_budget_exceeded", "Narrow the type query");
            }
            else if (kind == "objects")
            {
                foreach (var go in LoadedObjects().Where(g => g.name.IndexOf(query, StringComparison.OrdinalIgnoreCase) >= 0))
                {
                    if (args["type"] == null) rows.Add(Objects.Summary(go));
                    else foreach (var component in go.GetComponents<Component>()) if (component != null && (component.GetType().FullName == (string)args["type"] || component.GetType().AssemblyQualifiedName == (string)args["type"])) rows.Add(Objects.Summary(component));
                }
            }
            else throw new BridgeException("invalid_arguments", "Unknown find kind");
            var page = Objects.Page(rows, args, Bridge.Digest(new JArray(rows).ToString(Formatting.None) + Bridge.Session + Bridge.Generation + Bridge.PlaySession));
            if (kind == "assets")
            {
                foreach (JObject row in (JArray)page["items"])
                {
                    var asset = AssetDatabase.LoadMainAssetAtPath((string)row["path"]);
                    row["target"] = Objects.Ref(asset);
                    row["type"] = asset == null ? null : asset.GetType().AssemblyQualifiedName;
                }
                page["consistency"] = "index_page_then_main_asset_observations";
            }
            return page;
        }
        static JObject Scenes(JObject args)
        {
            var rows = new List<JObject>();
            for (int i = 0; i < SceneManager.sceneCount; i++)
            {
                var scene = SceneManager.GetSceneAt(i);
                rows.Add(new JObject { ["path"] = scene.path, ["name"] = scene.name, ["loaded"] = scene.isLoaded, ["dirty"] = scene.isDirty, ["rootCount"] = scene.rootCount });
            }
            return Objects.Page(rows, args, Bridge.Digest(new JArray(rows).ToString(Formatting.None)));
        }
        static Scene ExactScene(string path)
        {
            PathSafety.Asset(path, ".unity");
            var scene = SceneManager.GetSceneByPath(path);
            if (!scene.IsValid() || !scene.isLoaded || EditorSceneManager.IsPreviewScene(scene)) throw new BridgeException("scene_unavailable", "Exact scene must already be loaded and saved at least once");
            return scene;
        }
        static JObject SceneAction(JObject args)
        {
            if (EditorApplication.isPlayingOrWillChangePlaymode) throw new BridgeException("runtime_edit_disabled", "Scene edits are authoring-only");
            string action = Required(args, "action");
            if (action == "save")
            {
                if ((bool?)args["acknowledgeExistingDirty"] != true) throw new BridgeException("save_acknowledgement_required", "Saving includes existing dirty edits in this exact scene");
                var scene = ExactScene(Required(args, "scenePath")); bool dirty = scene.isDirty;
                bool saved = EditorSceneManager.SaveScene(scene);
                return new JObject { ["status"] = saved ? "saved" : "not_applied", ["saved"] = saved, ["scope"] = scene.path, ["includedExistingDirty"] = dirty, ["dirty"] = scene.isDirty };
            }
            if (action == "create")
            {
                var scene = ExactScene(Required(args, "scenePath")); string name = Required(args, "name");
                return WithUndo(null, () => { var created = new GameObject(name); Undo.RegisterCreatedObjectUndo(created, "Create object"); SceneManager.MoveGameObjectToScene(created, scene); EditorSceneManager.MarkSceneDirty(scene);
                    return new JObject { ["status"] = "applied", ["target"] = Objects.Ref(created), ["receipt"] = Objects.Issue(created), ["changed"] = true, ["dirty"] = true, ["saved"] = false }; });
            }
            var target = Objects.Resolve(args["target"]);
            var go = target as GameObject;
            if (go == null) throw new BridgeException("type_mismatch", "Scene action target must be a GameObject");
            Objects.Editable(go, "sceneInstance"); Objects.CheckReceipt(go, (string)args["receipt"]);
            if (action == "rename") Required(args, "name");
            else if (action == "activate" && args["active"]?.Type != JTokenType.Boolean) throw new BridgeException("invalid_arguments", "active must be boolean");
            else if (action != "activate" && action != "reparent" && action != "add_component" && action != "rename") throw new BridgeException("capability_unavailable", "Unsupported scene action");
            Transform parent = null;
            Type componentType = null;
            if (action == "reparent")
            {
                var parentGo = Objects.Resolve(args["parent"]) as GameObject;
                if (parentGo == null || parentGo.scene != go.scene || parentGo == go || parentGo.transform.IsChildOf(go.transform)) throw new BridgeException("invalid_parent", "Parent must be another object in the same scene, without cycles");
                Objects.Editable(parentGo, "sceneInstance"); parent = parentGo.transform;
            }
            if (action == "add_component") componentType = FindType(Required(args, "type"), typeof(Component));
            return WithUndo(go, () => {
                Object observed = go;
                if (action == "rename") go.name = (string)args["name"];
                if (action == "activate") go.SetActive((bool)args["active"]);
                if (action == "reparent") Undo.SetTransformParent(go.transform, parent, "Reparent object");
                if (action == "add_component") { observed = Undo.AddComponent(go, componentType); if (observed == null) throw new BridgeException("component_not_added", "Unity rejected component addition"); }
                if (PrefabUtility.IsPartOfPrefabInstance(go)) PrefabUtility.RecordPrefabInstancePropertyModifications(go);
                EditorSceneManager.MarkSceneDirty(go.scene);
                return new JObject { ["status"] = "applied", ["target"] = Objects.Ref(observed), ["receipt"] = Objects.Issue(observed), ["changed"] = true, ["dirty"] = true, ["saved"] = false, ["scope"] = "sceneInstance" };
            });
        }
        static JObject AssetAction(JObject args)
        {
            if (EditorApplication.isPlayingOrWillChangePlaymode) throw new BridgeException("runtime_edit_disabled", "Asset mutations are authoring-only");
            string action = Required(args, "action");
            if (action == "create_so")
            {
                string relative = Required(args, "path"), full = PathSafety.Asset(relative, ".asset");
                if ((bool?)args["mustNotExist"] != true || File.Exists(full) || File.Exists(full + ".meta") || !String.IsNullOrEmpty(AssetDatabase.AssetPathToGUID(relative))) throw new BridgeException("creation_precondition_failed", "Target and metadata must not exist");
                Type type = FindType(Required(args, "type"), typeof(ScriptableObject));
                bool scriptResolves = AssetDatabase.FindAssets(type.Name + " t:MonoScript").Take(1000)
                    .Select(guid => AssetDatabase.LoadAssetAtPath<MonoScript>(AssetDatabase.GUIDToAssetPath(guid)))
                    .Any(script => script != null && script.GetClass() == type);
                if (!scriptResolves) throw new BridgeException("script_type_unavailable", "Compiled SO type has no resolvable MonoScript asset; check file/class naming and compilation");
                ScriptableObject instance = null;
                try
                {
                    instance = ScriptableObject.CreateInstance(type); PathSafety.Asset(relative, ".asset");
                    if (File.Exists(full) || File.Exists(full + ".meta")) throw new BridgeException("creation_precondition_failed", "Target appeared during creation");
                    AssetDatabase.CreateAsset(instance, relative);
                    return new JObject { ["status"] = "applied", ["target"] = Objects.Ref(instance), ["receipt"] = Objects.Issue(instance), ["changed"] = true, ["saved"] = File.Exists(full), ["dirty"] = EditorUtility.IsDirty(instance), ["scope"] = relative };
                }
                catch (Exception error)
                { if (instance != null && !EditorUtility.IsPersistent(instance)) Object.DestroyImmediate(instance); return Bridge.Error(error, File.Exists(full) ? "partially_applied" : "outcome_unknown"); }
            }
            if (action != "save") throw new BridgeException("capability_unavailable", "Unsupported asset action");
            var target = Objects.Resolve(args["target"]); Objects.Editable(target, "asset"); Objects.CheckReceipt(target, (string)args["receipt"]);
            if ((bool?)args["acknowledgeExistingDirty"] != true) throw new BridgeException("save_acknowledgement_required", "Saving includes existing dirty edits in this asset file and its sub-assets");
            bool dirty = EditorUtility.IsDirty(target); string assetPath = AssetDatabase.GetAssetPath(target);
            AssetDatabase.SaveAssetIfDirty(target);
            return new JObject { ["status"] = "saved", ["saved"] = true, ["target"] = Objects.Ref(target), ["scope"] = assetPath, ["includedExistingDirty"] = dirty,
                ["dirty"] = EditorUtility.IsDirty(target), ["receipt"] = Objects.Issue(target), ["saveHook"] = "OnWillSaveAssets_not_invoked", ["verification"] = "save_returned_then_state_observed" };
        }
        static JObject Prefab(JObject args)
        {
            if ((string)args["action"] != "overrides") throw new BridgeException("capability_unavailable", "Only override observation is implemented");
            var target = Objects.Resolve(args["target"]);
            if (!PrefabUtility.IsPartOfPrefabInstance(target)) throw new BridgeException("not_prefab_instance", "Target is not a Prefab instance");
            var mods = PrefabUtility.GetPropertyModifications(target) ?? Array.Empty<PropertyModification>();
            if (mods.Length > 10000) throw new BridgeException("query_budget_exceeded", "Too many overrides");
            var rows = mods.Select(m => new JObject { ["sourceTarget"] = Objects.Ref(m.target), ["propertyPath"] = m.propertyPath, ["value"] = m.value, ["objectReference"] = Objects.Ref(m.objectReference) }).ToList();
            return Objects.Page(rows, args, Bridge.Digest(new JArray(rows).ToString(Formatting.None)));
        }
        static JObject EditorAction(JObject args)
        {
            string action = Required(args, "action");
            switch (action)
            {
                case "play": if (!EditorApplication.isPlayingOrWillChangePlaymode) EditorApplication.isPlaying = true; break;
                case "stop": if (EditorApplication.isPlayingOrWillChangePlaymode) EditorApplication.isPlaying = false; break;
                case "pause": if (!EditorApplication.isPlaying) throw new BridgeException("not_playing", "Play Mode is inactive"); EditorApplication.isPaused = true; break;
                case "resume": if (!EditorApplication.isPlaying) throw new BridgeException("not_playing", "Play Mode is inactive"); EditorApplication.isPaused = false; break;
                case "step": if (!EditorApplication.isPlaying || !EditorApplication.isPaused) throw new BridgeException("not_paused", "Step requires paused Play Mode"); EditorApplication.Step(); break;
                case "import": var relative = Required(args, "path"); var full = PathSafety.Asset(relative); if (!File.Exists(full)) throw new BridgeException("not_found", "Import path must be an existing file"); AssetDatabase.ImportAsset(relative); break;
                case "compile": if (EditorApplication.isPlayingOrWillChangePlaymode) throw new BridgeException("editor_busy", "Compile request requires Edit Mode"); CompilationPipeline.RequestScriptCompilation(); break;
                default: throw new BridgeException("capability_unavailable", "Unknown Editor action");
            }
            return new JObject { ["status"] = "accepted", ["action"] = action, ["verification"] = "unknown", ["compilationIdAtRequest"] = Observations.CompilationId,
                ["playingAtReturn"] = EditorApplication.isPlaying, ["pausedAtReturn"] = EditorApplication.isPaused, ["message"] = "Request issued; inspect subsequent status/events for observed completion" };
        }
    }
}
