using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEngine;
using UnityEngine.UI;
using UnityEngine.EventSystems;
using EvidenceFirst.Debugging;
using EvidenceFirst.UnityBridge;
using EvidenceFirst.Adapters;
[InitializeOnLoad]
public static class EvidenceUIAdapter
{
    static EvidenceUIAdapter()
    {
        DebugAdapters.Register("ui.state", "query", DebugSchemas.TargetInput(), DebugSchemas.Object(new JObject { ["hasSelectable"] = DebugSchemas.Bool(), ["interactable"] = DebugSchemas.Bool(), ["selected"] = DebugSchemas.Bool() }, "hasSelectable", "interactable", "selected"), a => {
            var o = DebugAdapters.ResolveTarget(a["target"]); var go = o as GameObject ?? (o as Component)?.gameObject;
            var s = go?.GetComponent<Selectable>();
            return new JObject { ["hasSelectable"] = s != null, ["interactable"] = s != null && s.IsInteractable(), ["selected"] = go != null && EventSystem.current != null && EventSystem.current.currentSelectedGameObject == go };
        });
        DebugAdapters.Register("ui.observe", "action", DebugSchemas.Object(new JObject { ["target"] = DebugSchemas.Target(), ["maxDurationMs"] = DebugSchemas.Number(1, 60000) }, "target", "maxDurationMs"), DebugSchemas.Object(new JObject { ["attachedProbe"] = DebugSchemas.Bool() }, "attachedProbe"), a => {
            var go = DebugAdapters.ResolveRuntimeGameObject(a["target"]);
            var p = OwnedProbes.Add<UIObservationProbe>(go, (double)a["maxDurationMs"]); p.Until = Time.realtimeSinceStartupAsDouble + (double)a["maxDurationMs"] / 1000;
            return new JObject { ["attachedProbe"] = true };
        });
    }
}
