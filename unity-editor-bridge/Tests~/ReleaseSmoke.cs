using System;
using System.IO;
using System.Linq;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;
using EvidenceFirst.UnityBridge;

namespace EvidenceFirst.Tests
{
    // Development fixture only. Never included in the product Editor assembly.
    public static class ReleaseSmoke
    {
        static readonly JArray Results = new JArray();
        static string Id() => Guid.NewGuid().ToString("N");
        static void Check(bool ok, string label) { if (!ok) throw new Exception(label); Results.Add(label); }
        static JObject Call(string method, JObject args) {
            var r = Operations.Dispatch(method, args); if (r["errorCode"] != null) throw new Exception(method + ": " + r); return r;
        }
        static void Denied(string method, JObject args, params string[] codes) {
            string code; try { code = (string)Operations.Dispatch(method, args)["errorCode"]; } catch (BridgeException e) { code = e.Code; }
            Check(codes.Contains(code), "rejected " + method + "/" + args["action"] + ": " + code);
        }
        static JObject Read(GameObject go) => Call("unity_prefab", new JObject { ["action"] = "read", ["target"] = Objects.Ref(go), ["propertyPaths"] = new JArray("m_Layer") });
        static JObject Patch(GameObject go, int layer, bool source) {
            var a = new JObject { ["target"] = Objects.Ref(go), ["operationId"] = Id(), ["patches"] = new JArray(new JObject { ["op"] = "set", ["propertyPath"] = "m_Layer", ["value"] = layer.ToString() }) };
            if (source) { a["action"] = "patch"; a["save"] = true; a["sourceReceipt"] = Read(go)["sourceReceipt"]; }
            else { a["scope"] = "sceneInstance"; a["receipt"] = Objects.Issue(go); }
            return Call(source ? "unity_prefab" : "unity_object_patch", a);
        }
        static JObject Approval(string method, JObject args) => Call("unity_approval", new JObject { ["action"] = "request", ["method"] = method, ["arguments"] = args });
        static void Approve(string method, JObject args) { var a = Approval(method, args); Approvals.Decide((string)a["approvalId"], true); args["approvalId"] = a["approvalId"]; }
        public static void Run() {
            if (!Path.GetFileName(Bridge.Root).StartsWith("unity-bridge-test-") || Environment.GetEnvironmentVariable("UNITY_TEST_RELEASE") != "1") throw new Exception("Isolated release fixture required");
            var report = new JObject();
            try {
                var scene = UnityEngine.SceneManagement.SceneManager.GetSceneByPath("Assets/TestScene.unity");
                var root = new GameObject("ReleaseRoot"); UnityEngine.SceneManagement.SceneManager.MoveGameObjectToScene(root, scene);
                var first = new GameObject("SameName"); first.transform.SetParent(root.transform); first.layer = 1;
                var second = new GameObject("SameName"); second.transform.SetParent(root.transform); second.layer = 2;
                var created = Call("unity_prefab", new JObject { ["action"] = "create", ["target"] = Objects.Ref(root), ["receipt"] = Objects.Issue(root), ["assetPath"] = "Assets/ReleaseBase.prefab", ["save"] = true, ["mustNotExist"] = true, ["operationId"] = Id() });
                Check((bool)created["saved"], "create explicitly saves absent source");
                var asset = AssetDatabase.LoadAssetAtPath<GameObject>("Assets/ReleaseBase.prefab"); var source = asset.transform.GetChild(1).gameObject;
                var stale = Read(source); Patch(source, 3, true);
                Check(source.layer == 3 && asset.transform.GetChild(0).gameObject.layer == 1, "source child patch uses identity, not duplicate names");
                Denied("unity_prefab", new JObject { ["action"] = "patch", ["target"] = Objects.Ref(source), ["sourceReceipt"] = stale["sourceReceipt"], ["save"] = true, ["operationId"] = Id(), ["patches"] = new JArray(new JObject { ["op"] = "set", ["propertyPath"] = "m_Layer", ["value"] = "8" }) }, "receipt_conflict");
                var instanceResult = Call("unity_prefab", new JObject { ["action"] = "instantiate", ["target"] = Objects.Ref(asset), ["scenePath"] = scene.path, ["operationId"] = Id() });
                var instance = (GameObject)Objects.Resolve(instanceResult["target"]); var child = instance.transform.GetChild(1).gameObject;
                Patch(child, 4, false);
                var apply = new JObject { ["action"] = "apply", ["target"] = Objects.Ref(child), ["receipt"] = Objects.Issue(child), ["sourceTarget"] = Objects.Ref(source), ["sourceReceipt"] = Read(source)["sourceReceipt"], ["assetPath"] = "Assets/ReleaseBase.prefab", ["save"] = true, ["propertyPaths"] = new JArray("m_Layer"), ["operationId"] = Id() };
                Call("unity_prefab", apply); Check(source.layer == 4, "apply exact scalar override to explicit source");
                Patch(child, 5, false);
                var revert = new JObject { ["action"] = "revert", ["target"] = Objects.Ref(child), ["receipt"] = Objects.Issue(child), ["propertyPaths"] = new JArray("m_Layer"), ["operationId"] = Id() };
                Denied("unity_prefab", revert, "approval_required"); Approve("unity_prefab", revert); Call("unity_prefab", revert);
                Check(child.layer == 4 && source.layer == 4, "approved revert modifies instance only");
                Patch(child, 6, false);
                Call("unity_prefab", new JObject { ["action"] = "create", ["target"] = Objects.Ref(instance), ["receipt"] = Objects.Issue(instance), ["assetPath"] = "Assets/ReleaseVariant.prefab", ["save"] = true, ["mustNotExist"] = true, ["operationId"] = Id() });
                var variant = AssetDatabase.LoadAssetAtPath<GameObject>("Assets/ReleaseVariant.prefab");
                Check(PrefabUtility.GetPrefabAssetType(variant) == PrefabAssetType.Variant, "explicit variant source created");
                Patch(variant.transform.GetChild(1).gameObject, 7, true);
                Check(variant.transform.GetChild(1).gameObject.layer == 7 && source.layer == 4, "variant source patch leaves base source unchanged");
                var outer = new GameObject("Outer"); UnityEngine.SceneManagement.SceneManager.MoveGameObjectToScene(outer, scene);
                var nested = (GameObject)PrefabUtility.InstantiatePrefab(asset, scene); nested.transform.SetParent(outer.transform);
                var outerAsset = PrefabUtility.SaveAsPrefabAsset(outer, "Assets/ReleaseNested.prefab");
                Patch(outerAsset.transform.GetChild(0).GetChild(1).gameObject, 8, true);
                Check(outerAsset.transform.GetChild(0).GetChild(1).gameObject.layer == 8 && source.layer == 4, "nested destination patch leaves inner source unchanged");
                var victim = new GameObject("ApprovalVictim"); UnityEngine.SceneManagement.SceneManager.MoveGameObjectToScene(victim, scene);
                var deletion = new JObject { ["action"] = "delete", ["target"] = Objects.Ref(victim), ["receipt"] = Objects.Issue(victim), ["operationId"] = Id() };
                Denied("unity_scene", deletion, "approval_required");
                var no = Approval("unity_scene", deletion); Approvals.Decide((string)no["approvalId"], false); deletion["approvalId"] = no["approvalId"];
                Denied("unity_scene", deletion, "approval_required"); deletion.Remove("approvalId");
                Approve("unity_scene", deletion); var altered = (JObject)deletion.DeepClone(); altered["operationId"] = Id(); Denied("unity_scene", altered, "approval_conflict");
                var deleted = Call("unity_scene", deletion); Check(victim == null && (bool)deleted["deleted"], "approved scene deletion executed");
                Check(JToken.DeepEquals(deleted, Call("unity_scene", deletion)), "same operation replay returns retained result without consuming again");
                Denied("unity_scene", altered, "approval_required");
                var tree = new GameObject("StaleApproval"); UnityEngine.SceneManagement.SceneManager.MoveGameObjectToScene(tree, scene);
                var childToChange = new GameObject("Child"); childToChange.transform.SetParent(tree.transform);
                var treeDelete = new JObject { ["action"] = "delete", ["target"] = Objects.Ref(tree), ["receipt"] = Objects.Issue(tree), ["operationId"] = Id() };
                Approve("unity_scene", treeDelete); childToChange.layer = 2; Denied("unity_scene", treeDelete, "approval_conflict", "receipt_conflict");
                var expiring = new JObject { ["action"] = "delete", ["target"] = Objects.Ref(tree), ["receipt"] = Objects.Issue(tree), ["operationId"] = Id() };
                Approve("unity_scene", expiring); Approvals.Pending().Single(e => e.Id == (string)expiring["approvalId"]).Until = DateTime.UtcNow.AddSeconds(-1); Denied("unity_scene", expiring, "approval_required");
                var collider = tree.AddComponent(Type.GetType("UnityEngine.BoxCollider, UnityEngine.PhysicsModule", true)); var remove = new JObject { ["action"] = "remove_component", ["target"] = Objects.Ref(collider), ["receipt"] = Objects.Issue(collider), ["operationId"] = Id() };
                Approve("unity_scene", remove); Call("unity_scene", remove); Check(collider == null && tree != null, "remove only approved non-Transform component");
                var trash = ScriptableObject.CreateInstance<SampleData>(); AssetDatabase.CreateAsset(trash, "Assets/ReleaseTrash.asset");
                var trashRequest = new JObject { ["action"] = "delete", ["target"] = Objects.Ref(trash), ["receipt"] = Objects.Issue(trash), ["operationId"] = Id() };
                Approve("unity_asset", trashRequest); Call("unity_asset", trashRequest); Check(!File.Exists(Path.Combine(Bridge.Root, "Assets/ReleaseTrash.asset")), "approved fixture asset moved to OS Trash");
                var history = new JObject { ["operationId"] = Id(), ["status"] = "running", ["editorSessionId"] = "previous-editor", ["results"] = new JArray() };
                TestExecution.Save(history); Check((string)TestExecution.Load((string)history["operationId"])["status"] == "outcome_unknown", "interrupted old Editor test history never resumes"); TestExecution.Release((string)history["operationId"]);
                PrefabDestinations.Run();
                report["status"] = "passed";
            } catch (Exception e) { report["status"] = "failed"; report["error"] = e.ToString(); throw; }
            finally { report["results"] = Results; report["passed"] = Results.Count; File.WriteAllText(Path.Combine(Bridge.Root, "release-smoke-result.json"), report.ToString()); }
        }
    }
}
