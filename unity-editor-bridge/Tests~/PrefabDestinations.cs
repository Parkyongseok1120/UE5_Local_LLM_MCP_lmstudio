using System;
using System.IO;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;
using UnityEngine.SceneManagement;
using EvidenceFirst.UnityBridge;
namespace EvidenceFirst.Tests
{
    public static class PrefabDestinations
    {
        static JObject Call(string name, JObject a) { var r = Operations.Dispatch(name, a); if (r["errorCode"] != null) throw new Exception(r.ToString()); return r; }
        public static void Run() {
            if (!Path.GetFileName(Bridge.Root).StartsWith("unity-bridge-test-")) throw new Exception("Isolated fixture required");
            var result = new JObject(); var checks = new JArray();
            try {
                var scene = SceneManager.GetSceneByPath("Assets/TestScene.unity");
                var baseAsset = AssetDatabase.LoadAssetAtPath<GameObject>("Assets/ReleaseBase.prefab"); int baseLayer = baseAsset.transform.GetChild(1).gameObject.layer;
                foreach (var path in new[] { "Assets/ReleaseVariant.prefab", "Assets/ReleaseNested.prefab" }) {
                    var asset = AssetDatabase.LoadAssetAtPath<GameObject>(path); var instance = (GameObject)PrefabUtility.InstantiatePrefab(asset, scene);
                    var source = path.Contains("Nested") ? asset.transform.GetChild(0).GetChild(1).gameObject : asset.transform.GetChild(1).gameObject;
                    var target = path.Contains("Nested") ? instance.transform.GetChild(0).GetChild(1).gameObject : instance.transform.GetChild(1).gameObject;
                    Call("unity_object_patch", new JObject { ["target"] = Objects.Ref(target), ["scope"] = "sceneInstance", ["receipt"] = Objects.Issue(target), ["operationId"] = Guid.NewGuid().ToString("N"), ["patches"] = new JArray(new JObject { ["op"] = "set", ["propertyPath"] = "m_Layer", ["value"] = "10" }) });
                    var sourceRead = Call("unity_prefab", new JObject { ["action"] = "read", ["target"] = Objects.Ref(source), ["propertyPaths"] = new JArray("m_Layer") });
                    var apply = new JObject { ["action"] = "apply", ["target"] = Objects.Ref(target), ["receipt"] = Objects.Issue(target), ["sourceTarget"] = Objects.Ref(baseAsset), ["sourceReceipt"] = sourceRead["sourceReceipt"], ["assetPath"] = path, ["save"] = true, ["propertyPaths"] = new JArray("m_Layer"), ["operationId"] = Guid.NewGuid().ToString("N") };
                    var wrong = Operations.Dispatch("unity_prefab", apply); if ((string)wrong["errorCode"] != "source_mismatch") throw new Exception("Wrong explicit destination was not rejected: " + wrong);
                    apply["operationId"] = Guid.NewGuid().ToString("N"); apply["sourceTarget"] = Objects.Ref(source); Call("unity_prefab", apply);
                    if (source.layer != 10 || baseAsset.transform.GetChild(1).gameObject.layer != baseLayer) throw new Exception("Destination scope leaked");
                    checks.Add(path + ": wrong source rejected; selected override saved without modifying base");
                }
                PrefabStageUtility.OpenPrefab("Assets/ReleaseBase.prefab");
                try {
                    var read = Call("unity_prefab", new JObject { ["action"] = "read", ["target"] = Objects.Ref(baseAsset) });
                    var patch = Operations.Dispatch("unity_prefab", new JObject { ["action"] = "patch", ["target"] = Objects.Ref(baseAsset), ["sourceReceipt"] = read["sourceReceipt"], ["save"] = true, ["operationId"] = Guid.NewGuid().ToString("N"), ["patches"] = new JArray(new JObject { ["op"] = "set", ["propertyPath"] = "m_Layer", ["value"] = "9" }) });
                    if ((string)patch["errorCode"] != "prefab_stage_open") throw new Exception("Open stage was not rejected: " + patch);
                    checks.Add("open target Prefab Stage rejects isolated source mutation");
                } finally { StageUtility.GoToMainStage(); }
                result["status"] = "passed";
            } catch (Exception e) { result["status"] = "failed"; result["error"] = e.ToString(); throw; }
            finally { result["results"] = checks; result["passed"] = checks.Count; File.WriteAllText(Path.Combine(Bridge.Root, "prefab-destinations-result.json"), result.ToString()); }
        }
    }
}
