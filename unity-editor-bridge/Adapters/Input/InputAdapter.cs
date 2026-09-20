using System;
using System.Linq;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEngine.InputSystem;
using UnityEngine.InputSystem.LowLevel;
using EvidenceFirst.Debugging;
using EvidenceFirst.UnityBridge;

[InitializeOnLoad]
public static class EvidenceInputAdapter
{
    static string controlPath; static double until;
    static EvidenceInputAdapter()
    {
        DebugAdapters.Register("input.state", "query", DebugSchemas.Object(new JObject { ["controlPath"] = DebugSchemas.String(256) }, "controlPath"),
            DebugSchemas.Object(new JObject { ["found"] = DebugSchemas.Bool(), ["path"] = DebugSchemas.String(256), ["value"] = DebugSchemas.String(4096), ["deviceId"] = DebugSchemas.Number() }, "found", "path", "value", "deviceId"), a => {
                var c = InputSystem.FindControl((string)a["controlPath"]);
                return new JObject { ["found"] = c != null, ["path"] = c?.path ?? "", ["value"] = c?.ReadValueAsObject()?.ToString() ?? "", ["deviceId"] = c?.device.deviceId ?? 0 };
            });
        DebugAdapters.Register("input.observe", "action", DebugSchemas.Object(new JObject { ["controlPath"] = DebugSchemas.String(256), ["maxDurationMs"] = DebugSchemas.Number(1, 60000) }, "controlPath", "maxDurationMs"),
            DebugSchemas.Object(new JObject { ["subscribed"] = DebugSchemas.Bool() }, "subscribed"), a => {
                controlPath = (string)a["controlPath"]; until = EditorApplication.timeSinceStartup + (double)a["maxDurationMs"] / 1000;
                InputSystem.onEvent -= Observe; InputSystem.onEvent += Observe;
                return new JObject { ["subscribed"] = true };
            });
        EditorApplication.update += () => { if (until > 0 && EditorApplication.timeSinceStartup > until) { InputSystem.onEvent -= Observe; until = 0; } };
        AssemblyReloadEvents.beforeAssemblyReload += () => InputSystem.onEvent -= Observe;
    }
    static void Observe(InputEventPtr e, InputDevice device)
    {
        if (EditorApplication.timeSinceStartup > until || !EditorApplication.isPlaying) return;
        var c = InputSystem.FindControl(controlPath); if (c == null || c.device != device) return;
        // Only record a control that the actual event reports as changed. Do not infer a value from an unrelated event.
        if (!e.IsA<StateEvent>() && !e.IsA<DeltaStateEvent>()) return;
        if (e.EnumerateChangedControls(device).Any(changed => changed == c))
            DebugRegistry.Emit("input.control_event", new JObject { ["controlPath"] = c.path, ["deviceId"] = device.deviceId, ["eventId"] = e.id, ["eventTime"] = e.time, ["eventType"] = e.type.ToString() });
    }
}
