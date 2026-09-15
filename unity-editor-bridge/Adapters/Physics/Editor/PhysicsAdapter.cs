using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEngine;
using EvidenceFirst.Debugging;
using EvidenceFirst.UnityBridge;
using EvidenceFirst.Adapters;
[InitializeOnLoad]
public static class EvidencePhysicsAdapter
{
    static EvidencePhysicsAdapter()
    {
        DebugAdapters.Register("physics.state", "query", DebugSchemas.TargetInput(), DebugSchemas.Object(new JObject { ["hasRigidbody"] = DebugSchemas.Bool(), ["isKinematic"] = DebugSchemas.Bool(), ["speed"] = DebugSchemas.Number(0), ["colliderCount"] = DebugSchemas.Number(0) }, "hasRigidbody", "isKinematic", "speed", "colliderCount"), a => {
            var o = DebugAdapters.ResolveTarget(a["target"]); var go = o as GameObject ?? (o as Component)?.gameObject;
            var rb = go?.GetComponent<Rigidbody>();
            return new JObject { ["hasRigidbody"] = rb != null, ["isKinematic"] = rb != null && rb.isKinematic, ["speed"] = rb == null ? 0 : rb.velocity.magnitude, ["colliderCount"] = go == null ? 0 : go.GetComponents<Collider>().Length };
        });
        DebugAdapters.Register("physics.observe", "action", DebugSchemas.Object(new JObject { ["target"] = DebugSchemas.Target(), ["maxDurationMs"] = DebugSchemas.Number(1, 60000) }, "target", "maxDurationMs"), DebugSchemas.Object(new JObject { ["attachedProbe"] = DebugSchemas.Bool() }, "attachedProbe"), a => {
            var go = DebugAdapters.ResolveRuntimeGameObject(a["target"]);
            var p = OwnedProbes.Add<CollisionObservationProbe>(go, (double)a["maxDurationMs"]); p.Until = Time.realtimeSinceStartupAsDouble + (double)a["maxDurationMs"] / 1000;
            return new JObject { ["attachedProbe"] = true };
        });
    }
}
