using Newtonsoft.Json.Linq;
using UnityEngine;
using EvidenceFirst.Debugging;
namespace EvidenceFirst.Adapters
{
    public sealed class CollisionObservationProbe : MonoBehaviour
    {
        public double Until;
        void Update() { if (Time.realtimeSinceStartupAsDouble > Until) Destroy(this); }
        void OnCollisionEnter(Collision e) { if (Time.realtimeSinceStartupAsDouble <= Until) DebugRegistry.Emit("physics.collision_enter", new JObject { ["contactCount"] = e.contactCount, ["relativeSpeed"] = e.relativeVelocity.magnitude }, gameObject, e.gameObject); }
        void OnTriggerEnter(Collider e) { if (Time.realtimeSinceStartupAsDouble <= Until) DebugRegistry.Emit("physics.trigger_enter", new JObject(), gameObject, e.gameObject); }
    }
}
