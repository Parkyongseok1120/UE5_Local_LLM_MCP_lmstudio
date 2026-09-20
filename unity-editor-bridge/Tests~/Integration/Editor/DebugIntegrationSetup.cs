using System;
using System.IO;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEngine;
using UnityEngine.UI;
using UnityEngine.EventSystems;
using UnityEngine.InputSystem;
using UnityEngine.InputSystem.LowLevel;
using EvidenceFirst.Debugging;
using EvidenceFirst.Fixture;

[InitializeOnLoad]
public static class DebugIntegrationSetup
{
    static GameObject spawned;
    static DebugIntegrationSetup()
    {
        EditorApplication.update += Setup;
        DebugRegistry.Register("fixture.spawn_child", "action", "1", DebugRegistry.EmptySchema, DebugRegistry.EmptySchema, _ => {
            if (spawned != null) throw new InvalidOperationException("Fixture child already exists"); spawned = new GameObject("ObservedChild"); spawned.transform.SetParent(GameObject.Find("DebugGroup").transform); return new JObject();
        });
        DebugRegistry.Register("fixture.remove_child", "action", "1", DebugRegistry.EmptySchema, DebugRegistry.EmptySchema, _ => { UnityEngine.Object.Destroy(spawned); return new JObject(); });
        DebugRegistry.Register("fixture.ui_click", "action", "1", DebugRegistry.EmptySchema, DebugRegistry.EmptySchema, _ => {
            var button = GameObject.Find("DebugButton"); ExecuteEvents.Execute(button, new PointerEventData(EventSystem.current), ExecuteEvents.pointerClickHandler); return new JObject();
        });
        DebugRegistry.Register("fixture.input", "action", "1", DebugRegistry.EmptySchema, DebugRegistry.EmptySchema, _ => {
            var keyboard = Keyboard.current ?? InputSystem.AddDevice<Keyboard>(); InputSystem.QueueStateEvent(keyboard, new KeyboardState(Key.Space)); InputSystem.Update(); return new JObject();
        });
        DebugRegistry.Register("fixture.collision", "action", "1", DebugRegistry.EmptySchema, DebugRegistry.EmptySchema, _ => {
            var rb = GameObject.Find("DebugBody").GetComponent<Rigidbody>(); rb.isKinematic = false; rb.useGravity = true; rb.position = new Vector3(0, 1, 0); rb.velocity = Vector3.down; return new JObject();
        });
    }
    static void Setup()
    {
        string root = Path.GetDirectoryName(Application.dataPath);
        if (!File.Exists(Path.Combine(root, "smoke-result.json")) || EditorApplication.isCompiling || EditorApplication.isUpdating) return;
        EditorApplication.update -= Setup;
        if (AssetDatabase.LoadAssetAtPath<DebugFixtureConfig>("Assets/DebugConfig.asset") != null) return;
        var config = ScriptableObject.CreateInstance<DebugFixtureConfig>(); AssetDatabase.CreateAsset(config, "Assets/DebugConfig.asset");
        var controller = new GameObject("DebugFSM").AddComponent<DebugFixtureController>(); controller.config = config;
        new GameObject("DebugGroup");
        new GameObject("DebugEventSystem", typeof(EventSystem));
        new GameObject("DebugButton", typeof(RectTransform), typeof(Button));
        var floor = new GameObject("DebugFloor"); floor.transform.position = new Vector3(0, -1, 0); floor.AddComponent<BoxCollider>().size = new Vector3(10, 1, 10);
        var body = new GameObject("DebugBody"); body.transform.position = new Vector3(0, 3, 0); body.AddComponent<BoxCollider>(); body.AddComponent<Rigidbody>().isKinematic = true;
        Keyboard keyboard = Keyboard.current ?? InputSystem.AddDevice<Keyboard>();
        File.WriteAllText(Path.Combine(root, "debug-fixture-ready.json"), new JObject { ["status"] = "ready", ["controlPath"] = keyboard.spaceKey.path }.ToString());
    }
}
