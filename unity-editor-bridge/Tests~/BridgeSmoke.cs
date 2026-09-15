using System;
using System.IO;
using System.Linq;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;
using EvidenceFirst.UnityBridge;

namespace EvidenceFirst.Tests
{
    public static class BridgeSmoke
    {
        static int passed;
        static void Check(bool condition, string name)
        { if (!condition) throw new Exception("FAIL: " + name); passed++; }
        static JObject Call(string method, JObject args)
        {
            var result = Operations.Dispatch(method, args);
            if (result["errorCode"] != null) throw new Exception(result.ToString());
            return result;
        }
        static JObject PatchArgs(JObject target, string receipt, params JObject[] edits) => new JObject {
            ["target"] = target, ["scope"] = "asset", ["receipt"] = receipt, ["operationId"] = Guid.NewGuid().ToString("N"), ["patches"] = new JArray(edits)
        };
        public static void Run()
        {
            EditorApplication.delayCall += Test;
        }
        static void Test()
        {
            var report = new JObject();
            try
            {
                Check(Bridge.Root != null, "bootstrap");
                Check(!Bridge.EditAllowed && !Bridge.ExecuteAllowed, "permissions default off");
                SessionState.SetBool("EvidenceFirst.Edit", true); SessionState.SetBool("EvidenceFirst.Execute", true);
                bool frameworkPresent = UnityEditor.PackageManager.PackageInfo.GetAllRegisteredPackages().Any(p => p.name == "com.unity.test-framework");
                Check(Bridge.Status()["capabilities"]["testExecution"].Value<bool>() == frameworkPresent, "test capability follows installed package");
                var scene = EditorSceneManager.NewScene(NewSceneSetup.EmptyScene, NewSceneMode.Single);
                EditorSceneManager.SaveScene(scene, "Assets/TestScene.unity");
                var request = new JObject { ["action"] = "create", ["name"] = "ReceiptTarget", ["scenePath"] = scene.path, ["operationId"] = "create-object-0001" };
                var created = Call("unity_scene", request);
                var again = Call("unity_scene", request);
                Check(JToken.DeepEquals(created, again) && scene.rootCount == 1, "idempotent create");
                var target = (JObject)created["target"];
                var go = (GameObject)Objects.Resolve(target);
                go.name = "UserInspectorChange";
                bool conflict = false;
                try { Objects.CheckReceipt(go, (string)created["receipt"]); } catch (BridgeException e) { conflict = e.Code == "receipt_conflict"; }
                Check(conflict && go.name == "UserInspectorChange", "stale receipt rejected");
                request["name"] = "different";
                try { Operations.Dispatch("unity_scene", request); Check(false, "different id conflict"); } catch (BridgeException e) { Check(e.Code == "operation_conflict", "different id conflict"); }
                var so = ScriptableObject.CreateInstance<SampleData>();
                so.graph = new Node { value = 7 }; so.graph.next = so.graph;
                AssetDatabase.CreateAsset(so, "Assets/Data.asset");
                var asset = Objects.Ref(so);
                var read = Call("unity_object_read", new JObject { ["target"] = asset });
                var changed = Call("unity_object_patch", PatchArgs(asset, (string)read["receipt"],
                    new JObject { ["op"] = "set", ["propertyPath"] = "number", ["value"] = "42" },
                    new JObject { ["op"] = "array_move", ["propertyPath"] = "values", ["index"] = 0, ["toIndex"] = 2 }));
                Check(so.number == 42 && so.values[0] == 20 && so.values[2] == 10, "scalar and array move");
                Check((bool)changed["saved"] == false && EditorUtility.IsDirty(so), "SO remains dirty");
                Check(so.graph != null && ReferenceEquals(so.graph, so.graph.next), "managed cycle retained");
                changed = Call("unity_object_patch", PatchArgs(asset, (string)changed["receipt"],
                    new JObject { ["op"] = "array_insert", ["propertyPath"] = "values", ["index"] = 1, ["value"] = "99" },
                    new JObject { ["op"] = "array_remove", ["propertyPath"] = "values", ["index"] = 0 },
                    new JObject { ["op"] = "set", ["propertyPath"] = "linked", ["value"] = asset }));
                Check(so.values.Length == 3 && so.values[0] == 99 && so.linked == so, "array insert/delete and reference");
                var saved = Call("unity_asset", new JObject { ["action"] = "save", ["target"] = asset, ["receipt"] = changed["receipt"], ["acknowledgeExistingDirty"] = true, ["operationId"] = "save-so-0001" });
                Check((bool)saved["saved"] && scene.isDirty, "saving SO did not save dirty scene");
                Check(Objects.Ref(so)["localFileId"].Type == JTokenType.String, "asset local ID is string");
                var prefab = PrefabUtility.SaveAsPrefabAsset(go, "Assets/Original.prefab");
                var instance = (GameObject)PrefabUtility.InstantiatePrefab(prefab);
                Check(Objects.Ref(prefab)["kind"].Value<string>() == "asset", "prefab source ref");
                Objects.Editable(instance, "sceneInstance");
                bool blocked = false;
                try { Objects.Editable(prefab, "sceneInstance"); } catch (BridgeException) { blocked = true; }
                Check(blocked, "prefab source cannot use scene mutation");
                Call("unity_object_read", new JObject { ["target"] = Objects.Ref(prefab), ["limit"] = 10 });
                var firstPage = Call("unity_object_read", new JObject { ["target"] = asset, ["limit"] = 1 });
                so.number++;
                try { Objects.Read(new JObject { ["target"] = asset, ["limit"] = 1, ["cursor"] = firstPage["nextCursor"] }); Check(false, "changed cursor"); }
                catch (BridgeException e) { Check(e.Code == "snapshot_changed", "changed cursor"); }
                var temp = new GameObject("Temporary");
                var emptyScene = EditorSceneManager.NewScene(NewSceneSetup.EmptyScene, NewSceneMode.Additive);
                UnityEngine.SceneManagement.SceneManager.MoveGameObjectToScene(temp, emptyScene);
                var tempRef = Objects.Ref(temp); Objects.ClearHandles();
                try { Objects.Resolve(tempRef); Check(false, "expired temporary handle"); } catch (BridgeException) { Check(true, "expired temporary handle"); }
                EditorSettings.enterPlayModeOptionsEnabled = true;
                EditorSettings.enterPlayModeOptions = EnterPlayModeOptions.DisableDomainReload | EnterPlayModeOptions.DisableSceneReload;
                if (Environment.GetEnvironmentVariable("UNITY_TEST_RELEASE") == "1") ReleaseSmoke.Run();
                report["status"] = "passed"; report["passed"] = passed; report["editorVersion"] = Application.unityVersion;
                report["projectRoot"] = Bridge.Root;
            }
            catch (Exception e) { report["status"] = "failed"; report["passed"] = passed; report["error"] = e.ToString(); }
            File.WriteAllText(Path.Combine(Directory.GetParent(Application.dataPath).FullName, "smoke-result.json"), report.ToString(Formatting.Indented));
            // Leave this isolated Editor running for RPC/reload/Play integration tests.
            Debug.Log("EvidenceFirst smoke " + report["status"] + " (" + passed + ")");
        }
    }
}
