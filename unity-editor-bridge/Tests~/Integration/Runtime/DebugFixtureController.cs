using System;
using Newtonsoft.Json.Linq;
using UnityEngine;
using EvidenceFirst.Debugging;
namespace EvidenceFirst.Fixture
{
    public interface ITestDamage { void ApplyDamage(int amount); }
    public sealed class DebugFixtureController : MonoBehaviour, ITestDamage
    {
        public DebugFixtureConfig config;
        public int health = 100;
        public string state = "idle";
        IDisposable query, damage, reset;
        void OnEnable()
        {
            if (!Application.isPlaying) return;
            var output = DebugSchemas.Object(new JObject { ["health"] = DebugSchemas.Number(), ["state"] = DebugSchemas.String(64) }, "health", "state");
            query = DebugRegistry.Register("fixture.fsm", "query", "1", DebugRegistry.EmptySchema, output, _ => State());
            damage = DebugRegistry.Register("fixture.damage", "action", "1", DebugSchemas.Object(new JObject { ["amount"] = DebugSchemas.Number(0, 100) }, "amount"), output, a => { ApplyDamage((int)a["amount"]); return State(); });
            reset = DebugRegistry.Register("fixture.reset", "action", "1", DebugRegistry.EmptySchema, output, _ => { health = 100; state = "idle"; return State(); });
        }
        void OnDisable() { query?.Dispose(); damage?.Dispose(); reset?.Dispose(); }
        JObject State() => new JObject { ["health"] = health, ["state"] = state };
        public void ApplyDamage(int amount)
        {
            string before = state; health -= amount * config.damageScale; state = "damaged";
            DebugRegistry.Emit("fixture.transition", new JObject { ["from"] = before, ["to"] = state, ["amount"] = amount, ["scaleRead"] = config.damageScale }, this, config);
        }
    }
}
