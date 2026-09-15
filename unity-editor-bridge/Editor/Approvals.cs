using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;
using Object = UnityEngine.Object;
namespace EvidenceFirst.UnityBridge
{
    internal static class Approvals
    {
        internal sealed class Entry { internal string Id, Method, Digest, StateDigest, Summary, State; internal JObject Args; internal DateTime Until; }
        static readonly Dictionary<string, Entry> Entries = new Dictionary<string, Entry>();
        internal static bool Required(string method, JObject args) => method == "unity_scene" && ((string)args["action"] == "delete" || (string)args["action"] == "remove_component") || method == "unity_asset" && (string)args["action"] == "delete" || method == "unity_prefab" && (string)args["action"] == "revert";
        static string Canonical(JToken t) => t is JObject o ? "{" + String.Join(",", o.Properties().OrderBy(p => p.Name, StringComparer.Ordinal).Select(p => JsonConvert.ToString(p.Name) + ":" + Canonical(p.Value))) + "}" : t is JArray a ? "[" + String.Join(",", a.Select(Canonical)) + "]" : t.ToString(Formatting.None);
        static string Binding(string method, JObject args) { var clean = (JObject)args.DeepClone(); clean.Remove("approvalId"); return Bridge.Digest(Bridge.ProjectId + Bridge.Session + Bridge.Generation + method + Canonical(clean)); }
        internal static string Validate(string method, JObject args, out string summary) {
            if (!Required(method, args)) throw new BridgeException("invalid_approval_scope", "Only explicit delete/remove-component/revert requests need destructive approval");
            if (EditorApplication.isPlayingOrWillChangePlaymode) throw new BridgeException("runtime_edit_disabled", "Destructive edits require Edit Mode");
            if (args["operationId"]?.Type != JTokenType.String) throw new BridgeException("invalid_operation_id", "Bind approval to the final operationId");
            var target = Objects.Resolve(args["target"]); Objects.CheckReceipt(target, (string)args["receipt"]);
            if (method == "unity_asset") {
                string path = AssetDatabase.GetAssetPath(target); string full = PathSafety.Asset(path);
                if (!new[] { ".asset", ".prefab", ".mat" }.Contains(Path.GetExtension(path).ToLowerInvariant()) || target != AssetDatabase.LoadMainAssetAtPath(path) || Directory.Exists(full)) throw new BridgeException("asset_scope_denied", "Delete supports one main .asset/.prefab/.mat file, never folders or sub-assets");
                if (EditorUtility.IsDirty(target)) throw new BridgeException("source_dirty", "Save or discard dirty asset edits explicitly first");
                if (path.EndsWith(".prefab", StringComparison.OrdinalIgnoreCase)) Prefabs.CheckSourceIdle(path);
                summary = "Move this exact asset and its metadata to OS Trash: " + path + ". References may become missing; no automatic repair.";
                return Prefabs.FileDigest(path);
            }
            Objects.Editable(target, "sceneInstance");
            if (method == "unity_scene" && (string)args["action"] == "delete" && !(target is GameObject)) throw new BridgeException("gameobject_required", "Delete requires one GameObject and its subtree");
            if (method == "unity_scene" && (string)args["action"] == "remove_component" && (!(target is Component) || target is Transform)) throw new BridgeException("component_required", "Remove requires a non-Transform component");
            if (method == "unity_prefab" && (!(args["propertyPaths"] is JArray paths) || paths.Count < 1 || paths.Count > 32)) throw new BridgeException("invalid_arguments", "Exact override property paths required");
            summary = method + "/" + (string)args["action"] + " on " + target.name + " (" + target.GetType().FullName + "). ObjectRef: " + args["target"].ToString(Formatting.None) + ". Scene is not automatically saved. References may break; Undo is limited to Unity authoring state.";
            return Prefabs.TreeDigest(target);
        }
        internal static JObject Call(JObject args) {
            foreach (var e in Entries.Values) if (e.Until <= DateTime.UtcNow && (e.State == "pending" || e.State == "approved")) e.State = "expired";
            if ((string)args["action"] == "status") {
                if (!Entries.TryGetValue((string)args["approvalId"] ?? "", out var found)) throw new BridgeException("approval_unknown", "Approval absent or Editor/domain session ended");
                return Result(found);
            }
            if ((string)args["action"] != "request" || !Bridge.EditAllowed) throw new BridgeException("permission_denied", "Enable Editor Edit before requesting approval");
            if (Entries.Count >= 32) { foreach (var k in Entries.Where(p => p.Value.State != "pending" && p.Value.State != "approved").Select(p => p.Key).ToArray()) Entries.Remove(k); }
            if (Entries.Count >= 32) throw new BridgeException("approval_capacity", "Maximum 32 outstanding requests");
            string method = (string)args["method"]; var operation = args["arguments"] as JObject;
            if (operation == null || operation["approvalId"] != null || operation.ToString(Formatting.None).Length > 16000) throw new BridgeException("invalid_arguments", "Provide bounded final operation arguments without approvalId");
            string state = Validate(method, operation, out var summary);
            var entry = new Entry { Id = Guid.NewGuid().ToString("N"), Method = method, Args = (JObject)operation.DeepClone(), Digest = Binding(method, operation), StateDigest = state, Summary = summary, State = "pending", Until = DateTime.UtcNow.AddMinutes(2) };
            Entries.Add(entry.Id, entry); return Result(entry);
        }
        static JObject Result(Entry e) => new JObject { ["status"] = e.State, ["approvalId"] = e.Id, ["expiresAt"] = e.Until.ToString("O"), ["summary"] = e.Summary, ["approvalSurface"] = "Unity menu: Tools/Evidence First/Review destructive requests", ["oneUse"] = true };
        internal static IEnumerable<Entry> Pending() => Entries.Values.Where(e => (e.State == "pending" || e.State == "approved") && e.Until > DateTime.UtcNow).ToArray();
        // Called only from the trusted Editor window, never dispatched from MCP.
        internal static void Decide(string id, bool approve) {
            if (!Entries.TryGetValue(id, out var entry) || entry.State != "pending" || entry.Until <= DateTime.UtcNow) throw new BridgeException("approval_expired", "Approval no longer pending");
            if (!approve) { entry.State = "denied"; return; }
            if (!Bridge.EditAllowed || Validate(entry.Method, entry.Args, out _) != entry.StateDigest) throw new BridgeException("approval_conflict", "Target changed; request fresh approval");
            entry.State = "approved";
        }
        internal static void Check(string method, JObject args) {
            if (!Entries.TryGetValue((string)args["approvalId"] ?? "", out var e) || e.State != "approved" || e.Until <= DateTime.UtcNow) throw new BridgeException("approval_required", "User approval for this exact operation is required");
            if (e.Digest != Binding(method, args) || Validate(method, args, out _) != e.StateDigest) throw new BridgeException("approval_conflict", "Approval content or target state changed");
        }
        internal static void Consume(string method, JObject args) {
            Check(method, args); Entries[(string)args["approvalId"]].State = "consumed";
        }
        internal static JObject Delete(string method, JObject args) {
            Validate(method, args, out _); var target = Objects.Resolve(args["target"]); Consume(method, args);
            if (method == "unity_asset") {
                string path = AssetDatabase.GetAssetPath(target); bool moved = AssetDatabase.MoveAssetToTrash(path);
                return new JObject { ["status"] = moved ? "applied" : "outcome_unknown", ["deleted"] = moved, ["assetPath"] = path, ["saved"] = moved, ["recovery"] = "OS Trash; not automatic rollback" };
            }
            var go = target as GameObject ?? ((Component)target).gameObject; var scene = go.scene;
            return Operations.WithUndo(target, () => { Undo.DestroyObjectImmediate(target); EditorSceneManager.MarkSceneDirty(scene); return new JObject { ["status"] = "applied", ["deleted"] = true, ["target"] = args["target"], ["dirty"] = true, ["saved"] = false, ["recovery"] = "Unity Undo; external callbacks not reversible" }; });
        }
    }
    internal sealed class DestructiveApprovalWindow : EditorWindow
    {
        Vector2 scroll;
        [MenuItem("Tools/Evidence First/Review destructive requests")]
        static void Open() { GetWindow<DestructiveApprovalWindow>("Destructive approvals"); }
        void OnGUI() {
            EditorGUILayout.HelpBox("Only approve operations you recognize. Approval is bound to the exact request, target state, Editor/domain and expires after 2 minutes. This window does not execute the operation.", MessageType.Warning);
            scroll = EditorGUILayout.BeginScrollView(scroll);
            foreach (var entry in Approvals.Pending()) {
                EditorGUILayout.LabelField(entry.Summary, EditorStyles.wordWrappedLabel);
                EditorGUILayout.SelectableLabel(entry.Args.ToString(Formatting.Indented), GUILayout.Height(180));
                EditorGUILayout.LabelField(entry.State + " / " + entry.Until.ToString("O"));
                if (entry.State == "pending") { EditorGUILayout.BeginHorizontal();
                    if (GUILayout.Button("Approve this exact request once")) { try { Approvals.Decide(entry.Id, true); } catch (Exception e) { ShowNotification(new GUIContent(e.Message)); } }
                    if (GUILayout.Button("Deny")) Approvals.Decide(entry.Id, false); EditorGUILayout.EndHorizontal(); }
                EditorGUILayout.Space();
            }
            EditorGUILayout.EndScrollView();
        }
    }
}
