using System;
using System.Collections.Generic;
using System.Globalization;
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
    internal static class Objects
    {
        static readonly Dictionary<string, Object> Handles = new Dictionary<string, Object>();
        sealed class Receipt { internal string Target, Digest; internal DateTime Expires; }
        static readonly Dictionary<string, Receipt> Receipts = new Dictionary<string, Receipt>();
        internal static void ClearHandles() { Handles.Clear(); Receipts.Clear(); }
        internal static JObject Ref(Object target)
        {
            if (target == null) return null;
            var result = new JObject { ["projectIdentity"] = Bridge.ProjectId };
            if (EditorUtility.IsPersistent(target) && AssetDatabase.TryGetGUIDAndLocalFileIdentifier(target, out string guid, out long local))
            { result["kind"] = "asset"; result["guid"] = guid; result["localFileId"] = local.ToString(CultureInfo.InvariantCulture); return result; }
            var go = target as GameObject ?? (target as Component)?.gameObject;
            if (!EditorApplication.isPlaying && go != null && !String.IsNullOrEmpty(go.scene.path))
            {
                var id = GlobalObjectId.GetGlobalObjectIdSlow(target);
                if ((int)id.identifierType != 0)
                { result["kind"] = "scene"; result["globalObjectId"] = id.ToString(); result["scenePath"] = go.scene.path; return result; }
            }
            var handle = Handles.FirstOrDefault(pair => pair.Value == target).Key;
            if (handle == null)
            {
                if (Handles.Count >= 4096) throw new BridgeException("handle_capacity", "Editor handle capacity reached");
                handle = Guid.NewGuid().ToString("N"); Handles.Add(handle, target);
            }
            result["kind"] = EditorApplication.isPlaying ? "runtime" : "temporary";
            result["handle"] = handle; result["editorSessionId"] = Bridge.Session; result["domainGeneration"] = Bridge.Generation;
            if (EditorApplication.isPlaying) result["playSessionId"] = Bridge.PlaySession;
            return result;
        }
        internal static Object Resolve(JToken reference)
        {
            if (!(reference is JObject r) || (string)r["projectIdentity"] != Bridge.ProjectId) throw new BridgeException("invalid_object_ref", "ObjectRef belongs to another project or is absent");
            Object target = null;
            switch ((string)r["kind"])
            {
                case "asset":
                    var path = AssetDatabase.GUIDToAssetPath((string)r["guid"]);
                    if (String.IsNullOrEmpty(path) || !Int64.TryParse((string)r["localFileId"], out var id)) break;
                    var candidates = new List<Object>(AssetDatabase.LoadAllAssetsAtPath(path));
                    var prefabRoot = AssetDatabase.LoadMainAssetAtPath(path) as GameObject;
                    if (prefabRoot != null) foreach (var t in prefabRoot.GetComponentsInChildren<Transform>(true)) { candidates.Add(t.gameObject); candidates.AddRange(t.GetComponents<Component>().Where(c => c != null)); }
                    foreach (var asset in candidates)
                        if (asset != null && AssetDatabase.TryGetGUIDAndLocalFileIdentifier(asset, out string g, out long l) && g == (string)r["guid"] && l == id) { target = asset; break; }
                    break;
                case "scene":
                    if (EditorApplication.isPlaying) throw new BridgeException("expired_object_ref", "Authoring scene references cannot resolve runtime objects");
                    var scene = SceneManager.GetSceneByPath((string)r["scenePath"]);
                    if (!scene.IsValid() || !scene.isLoaded) break;
                    if (GlobalObjectId.TryParse((string)r["globalObjectId"], out var global)) target = GlobalObjectId.GlobalObjectIdentifierToObjectSlow(global);
                    var go = target as GameObject ?? (target as Component)?.gameObject;
                    if (go == null || go.scene.path != (string)r["scenePath"]) target = null;
                    break;
                case "temporary": case "runtime":
                    if ((string)r["editorSessionId"] != Bridge.Session || (int?)r["domainGeneration"] != Bridge.Generation ||
                        ((string)r["kind"] == "runtime" && (!EditorApplication.isPlaying || (string)r["playSessionId"] != Bridge.PlaySession)))
                        throw new BridgeException("expired_object_ref", "Object handle session has ended");
                    Handles.TryGetValue((string)r["handle"] ?? "", out target);
                    break;
                default: throw new BridgeException("invalid_object_ref", "Unknown reference kind");
            }
            if (target == null) throw new BridgeException("object_not_found", "The exact referenced object no longer resolves");
            return target;
        }
        internal static string Fingerprint(Object target)
        {
            var serialized = EditorJsonUtility.ToJson(target);
            if (serialized.Length > 2 * 1024 * 1024) throw new BridgeException("snapshot_budget_exceeded", "Serialized target is too large for the receipt implementation");
            var go = target as GameObject ?? (target as Component)?.gameObject;
            var structure = go == null ? "" : String.Join(",", go.GetComponents<Component>().Select(c => c == null ? "missing" : c.GetInstanceID().ToString())) + "|" +
                (go.transform.parent ? go.transform.parent.GetInstanceID().ToString() : "root") + "|" + go.transform.GetSiblingIndex() + "|" + go.activeSelf + "|" + go.name + "|" +
                String.Join(",", Enumerable.Range(0, go.transform.childCount).Select(i => go.transform.GetChild(i).GetInstanceID()));
            return Bridge.Digest(serialized + "|" + structure);
        }
        internal static string Issue(Object target)
        {
            foreach (var expiredKey in Receipts.Where(p => p.Value.Expires < DateTime.UtcNow).Select(p => p.Key).ToArray()) Receipts.Remove(expiredKey);
            if (Receipts.Count >= 512) throw new BridgeException("receipt_capacity", "Too many active object receipts; wait for expiration or reconnect after reload");
            string key = Guid.NewGuid().ToString("N");
            Receipts[key] = new Receipt { Target = Ref(target).ToString(Formatting.None), Digest = Fingerprint(target), Expires = DateTime.UtcNow.AddMinutes(15) };
            return key;
        }
        internal static void CheckReceipt(Object target, string key)
        {
            if (key == null || !Receipts.TryGetValue(key, out var receipt) || receipt.Expires < DateTime.UtcNow) throw new BridgeException("receipt_required", "A current Bridge-issued receipt is required");
            if (receipt.Target != Ref(target).ToString(Formatting.None) || receipt.Digest != Fingerprint(target)) throw new BridgeException("receipt_conflict", "Observed target changed; no patch was applied");
        }
        internal static JObject Summary(Object target) => new JObject { ["target"] = Ref(target), ["name"] = target.name, ["type"] = target.GetType().AssemblyQualifiedName };
        internal static JObject Page(List<JObject> items, JObject args, string revision)
        {
            int offset = 0, limit = (int?)args["limit"] ?? 50;
            if (limit < 1 || limit > 200) throw new BridgeException("invalid_arguments", "limit must be 1..200");
            if (args["cursor"] != null)
            {
                JObject cursor;
                try { cursor = JObject.Parse(EncodingUtf8(Convert.FromBase64String((string)args["cursor"]))); }
                catch { throw new BridgeException("invalid_cursor", "Malformed cursor"); }
                if ((string)cursor["revision"] != revision) throw new BridgeException("snapshot_changed", "State/query changed between pages");
                offset = (int?)cursor["offset"] ?? -1;
                if (offset < 0 || offset > items.Count) throw new BridgeException("invalid_cursor", "Offset outside result");
            }
            int end = Math.Min(items.Count, offset + limit);
            return new JObject { ["status"] = "observed", ["items"] = new JArray(items.Skip(offset).Take(limit)), ["total"] = items.Count,
                ["revision"] = revision, ["truncated"] = end < items.Count,
                ["nextCursor"] = end < items.Count ? Convert.ToBase64String(System.Text.Encoding.UTF8.GetBytes(new JObject { ["revision"] = revision, ["offset"] = end }.ToString(Formatting.None))) : null };
        }
        static string EncodingUtf8(byte[] value) => System.Text.Encoding.UTF8.GetString(value);
        internal static JObject Read(JObject args, bool issueReceipt = true)
        {
            var target = Resolve(args["target"]);
            var before = Fingerprint(target);
            var rows = new List<JObject>();
            var paths = args["propertyPaths"] as JArray;
            int depth = (int?)args["depth"] ?? 1;
            if (depth < 0 || depth > 6 || paths?.Count > 32) throw new BridgeException("invalid_arguments", "Invalid property query bounds");
            using (var so = new SerializedObject(target))
            {
                so.Update();
                if (paths != null && paths.Count > 0)
                {
                    foreach (var p in paths)
                    {
                        var property = so.FindProperty((string)p);
                        if (property == null) rows.Add(new JObject { ["propertyPath"] = p, ["state"] = "inaccessible", ["editable"] = false });
                        else rows.Add(Property(property));
                    }
                }
                else
                {
                    var iterator = so.GetIterator();
                    int count = 0;
                    bool children = true;
                    while (iterator.NextVisible(children))
                    {
                        if (++count > 10000) throw new BridgeException("snapshot_budget_exceeded", "Use explicit propertyPaths for this object");
                        if (iterator.depth <= depth) rows.Add(Property(iterator));
                        children = iterator.depth < depth && iterator.propertyType != SerializedPropertyType.ManagedReference;
                    }
                }
            }
            if (Fingerprint(target) != before) throw new BridgeException("snapshot_changed", "Object changed during observation");
            var result = Page(rows, args, Bridge.Digest(before + args["propertyPaths"] + depth.ToString() + Bridge.Session + Bridge.Generation + Bridge.PlaySession));
            result["target"] = Ref(target);
            if (issueReceipt) { result["receipt"] = Issue(target); result["receiptScope"] = "serialized_object_and_related_hierarchy"; }
            result["dirty"] = EditorUtility.IsDirty(target); result["runtime"] = EditorApplication.isPlaying && !EditorUtility.IsPersistent(target);
            result["observedFrame"] = Time.frameCount; result["name"] = target.name; result["type"] = target.GetType().AssemblyQualifiedName;
            if (target is MonoScript script) result["compiledType"] = script.GetClass()?.AssemblyQualifiedName;
            return result;
        }
        static JObject Property(SerializedProperty p)
        {
            var row = new JObject { ["propertyPath"] = p.propertyPath, ["type"] = p.type, ["propertyType"] = p.propertyType.ToString(), ["editable"] = p.editable && !EditorApplication.isPlaying, ["state"] = "value" };
            if (p.isArray && p.propertyType != SerializedPropertyType.String) { row["state"] = "container"; row["arraySize"] = p.arraySize; return row; }
            switch (p.propertyType)
            {
                case SerializedPropertyType.Integer: row["value"] = p.longValue.ToString(CultureInfo.InvariantCulture); row["encoding"] = "int64_decimal_string"; break;
                case SerializedPropertyType.Boolean: row["value"] = p.boolValue; break;
                case SerializedPropertyType.Float: row["value"] = p.doubleValue; break;
                case SerializedPropertyType.String: row["value"] = p.stringValue; break;
                case SerializedPropertyType.Enum: row["value"] = p.intValue; row["enumNames"] = new JArray(p.enumNames); break;
                case SerializedPropertyType.ObjectReference:
                    row["state"] = p.objectReferenceValue != null ? "value" : p.objectReferenceInstanceIDValue != 0 ? "missing_reference" : "null";
                    row["value"] = Ref(p.objectReferenceValue); break;
                case SerializedPropertyType.Color: var c = p.colorValue; row["value"] = new JArray(c.r, c.g, c.b, c.a); break;
                case SerializedPropertyType.Vector2: var v2 = p.vector2Value; row["value"] = new JArray(v2.x, v2.y); break;
                case SerializedPropertyType.Vector3: var v3 = p.vector3Value; row["value"] = new JArray(v3.x, v3.y, v3.z); break;
                case SerializedPropertyType.Vector4: var v4 = p.vector4Value; row["value"] = new JArray(v4.x, v4.y, v4.z, v4.w); break;
                case SerializedPropertyType.Quaternion: var q = p.quaternionValue; row["value"] = new JArray(q.x, q.y, q.z, q.w); break;
                case SerializedPropertyType.ManagedReference:
                    row["state"] = p.managedReferenceValue == null ? "null" : "managed_reference";
                    row["managedReferenceId"] = p.managedReferenceId.ToString(CultureInfo.InvariantCulture); row["managedType"] = p.managedReferenceFullTypename;
                    row["editable"] = false; break;
                case SerializedPropertyType.Generic: row["state"] = "container"; row["editable"] = false; break;
                default: row["state"] = "unsupported"; row["editable"] = false; break;
            }
            if (p.propertyPath == "m_Script" || p.propertyPath == "m_ObjectHideFlags") row["editable"] = false;
            return row;
        }
        static float[] Values(JToken value, int count)
        {
            if (!(value is JArray array) || array.Count != count || array.Any(v => v.Type != JTokenType.Float && v.Type != JTokenType.Integer)) throw new BridgeException("type_mismatch", "Expected a numeric vector of the declared length");
            var values = array.Select(v => (float)v).ToArray();
            if (values.Any(v => Single.IsNaN(v) || Single.IsInfinity(v))) throw new BridgeException("type_mismatch", "Non-finite values are unsupported");
            return values;
        }
        static void Set(SerializedProperty p, JToken value)
        {
            if (!p.editable || p.propertyPath == "m_Script" || p.propertyPath == "m_ObjectHideFlags") throw new BridgeException("property_read_only", "Property is protected");
            if (value == null) throw new BridgeException("invalid_arguments", "set requires value (explicit null allowed for object references)");
            switch (p.propertyType)
            {
                case SerializedPropertyType.Integer:
                    if (value.Type != JTokenType.String || !Int64.TryParse((string)value, NumberStyles.Integer, CultureInfo.InvariantCulture, out var number)) throw new BridgeException("type_mismatch", "Integers use signed decimal strings");
                    if (p.type == "int" && (number < Int32.MinValue || number > Int32.MaxValue)) throw new BridgeException("type_mismatch", "Value exceeds the serialized int32 range");
                    p.longValue = number; break;
                case SerializedPropertyType.Boolean: if (value.Type != JTokenType.Boolean) throw new BridgeException("type_mismatch", "Expected boolean"); p.boolValue = (bool)value; break;
                case SerializedPropertyType.Float:
                    if (value.Type != JTokenType.Float && value.Type != JTokenType.Integer) throw new BridgeException("type_mismatch", "Expected number");
                    double real = (double)value;
                    if (Double.IsNaN(real) || Double.IsInfinity(real) || p.type == "float" && (real < -Single.MaxValue || real > Single.MaxValue)) throw new BridgeException("type_mismatch", "Value exceeds the finite property range");
                    p.doubleValue = real; break;
                case SerializedPropertyType.String: if (value.Type != JTokenType.String) throw new BridgeException("type_mismatch", "Expected string"); p.stringValue = (string)value; break;
                case SerializedPropertyType.Enum: if (value.Type != JTokenType.Integer) throw new BridgeException("type_mismatch", "Expected numeric enum/flags value"); p.intValue = (int)value; break;
                case SerializedPropertyType.ObjectReference: p.objectReferenceValue = value.Type == JTokenType.Null ? null : Resolve(value); break;
                case SerializedPropertyType.Color: var c = Values(value, 4); p.colorValue = new Color(c[0], c[1], c[2], c[3]); break;
                case SerializedPropertyType.Vector2: var a = Values(value, 2); p.vector2Value = new Vector2(a[0], a[1]); break;
                case SerializedPropertyType.Vector3: var b = Values(value, 3); p.vector3Value = new Vector3(b[0], b[1], b[2]); break;
                case SerializedPropertyType.Vector4: var d = Values(value, 4); p.vector4Value = new Vector4(d[0], d[1], d[2], d[3]); break;
                case SerializedPropertyType.Quaternion: var e = Values(value, 4); p.quaternionValue = new Quaternion(e[0], e[1], e[2], e[3]); break;
                default: throw new BridgeException("capability_unavailable", "This serialized property type cannot be assigned");
            }
        }
        internal static void Editable(Object target, string scope)
        {
            if (EditorApplication.isPlayingOrWillChangePlaymode) throw new BridgeException("runtime_edit_disabled", "Runtime edits are unavailable");
            if (EditorUtility.IsPersistent(target))
            {
                if (scope != "asset" || !(target is ScriptableObject) || target is MonoScript || AssetDatabase.GetAssetPath(target).EndsWith(".prefab")) throw new BridgeException("scope_unavailable", "Persistent edit supports ScriptableObject assets only");
                PathSafety.Asset(AssetDatabase.GetAssetPath(target), ".asset");
            }
            else
            {
                var go = target as GameObject ?? (target as Component)?.gameObject;
                if (scope != "sceneInstance" || go == null || !go.scene.IsValid() || EditorSceneManager.IsPreviewScene(go.scene) || PrefabStageUtility.GetPrefabStage(go) != null) throw new BridgeException("scope_unavailable", "Only ordinary loaded scene instances can be edited here");
            }
        }
        internal static JObject Patch(JObject args)
        {
            var target = Resolve(args["target"]); Editable(target, (string)args["scope"]); CheckReceipt(target, (string)args["receipt"]);
            return PatchTarget(target, args, false);
        }
        // Only Prefabs calls isolated=true on contents it has explicitly loaded and owns.
        internal static JObject PatchTarget(Object target, JObject args, bool isolated)
        {
            if (!(args["patches"] is JArray changes) || changes.Count == 0 || changes.Count > 32) throw new BridgeException("invalid_arguments", "patches must contain 1..32 edits");
            using (var so = new SerializedObject(target))
            {
                so.Update();
                foreach (var item in changes)
                {
                    var p = so.FindProperty((string)item["propertyPath"]);
                    if (p == null) throw new BridgeException("property_not_found", "Exact propertyPath does not resolve");
                    string op = (string)item["op"];
                    if (op == "set") Set(p, item["value"]);
                    else
                    {
                        if (!p.isArray || p.propertyType == SerializedPropertyType.String) throw new BridgeException("type_mismatch", "Array operation requires an array/list");
                        int index = (int?)item["index"] ?? -1;
                        if (index < 0 || index >= p.arraySize + (op == "array_insert" ? 1 : 0)) throw new BridgeException("index_out_of_range", "Invalid array index");
                        if (op == "array_insert") { p.InsertArrayElementAtIndex(index); Set(p.GetArrayElementAtIndex(index), item["value"]); }
                        else if (op == "array_remove") { int size = p.arraySize; p.DeleteArrayElementAtIndex(index); if (p.arraySize == size) p.DeleteArrayElementAtIndex(index); }
                        else if (op == "array_move") { int to = (int?)item["toIndex"] ?? -1; if (to < 0 || to >= p.arraySize) throw new BridgeException("index_out_of_range", "Invalid destination"); p.MoveArrayElement(index, to); }
                        else throw new BridgeException("invalid_arguments", "Unknown patch operation");
                    }
                }
                // Pending SerializedObject edits have not touched the live target yet.
                if (!isolated) CheckReceipt(target, (string)args["receipt"]);
                return Operations.WithUndo(target, () => {
                    bool changed = so.ApplyModifiedProperties();
                    if (PrefabUtility.IsPartOfPrefabInstance(target)) PrefabUtility.RecordPrefabInstancePropertyModifications(target);
                    EditorUtility.SetDirty(target);
                    var result = isolated ? new JObject() : Read(new JObject { ["target"] = Ref(target), ["propertyPaths"] = new JArray(changes.Select(c => c["propertyPath"])), ["limit"] = 32 });
                    result["status"] = "applied"; result["changed"] = changed; result["saved"] = false; result["dirty"] = true;
                    result["scope"] = args["scope"]; result["verification"] = "observed_after_apply"; return result;
                });
            }
        }
    }
}
