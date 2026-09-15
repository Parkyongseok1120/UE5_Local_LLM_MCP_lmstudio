using System;
using System.Collections.Generic;
using System.Linq;
using System.Threading;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEditor.PackageManager;
using PackageInfo = UnityEditor.PackageManager.PackageInfo;
using UnityEngine;
using EvidenceFirst.Debugging;

namespace EvidenceFirst.UnityBridge
{
    public static class DebugAdapters
    {
        static readonly List<JObject> Events = new List<JObject>();
        static long sequence; static int bytes, thread; static double until; static int endFrame; static string collection, play, started;
        static HashSet<string> kinds;
        internal static void Start() { thread = Thread.CurrentThread.ManagedThreadId; DebugRegistry.Observed -= OnEvent; DebugRegistry.Observed += OnEvent; }
        internal static void Stop() { DebugRegistry.Observed -= OnEvent; }
        public static UnityEngine.Object ResolveTarget(JToken target) => Objects.Resolve(target);
        public static GameObject ResolveRuntimeGameObject(JToken target) {
            var go = Objects.Resolve(target) as GameObject;
            if (!EditorApplication.isPlaying || go == null || EditorUtility.IsPersistent(go) || (string)target["kind"] != "runtime") throw new BridgeException("runtime_target_required", "Instrumentation only accepts a live runtime GameObject handle, never an asset/prefab source");
            return go;
        }
        public static JObject ObjectReference(UnityEngine.Object target) => Objects.Ref(target);
        public static void Register(string id, string kind, JObject inputSchema, JObject outputSchema, Func<JObject, JObject> callback)
        {
            // Runtime registry validates the same schemas and invocation for optional adapters.
            DebugRegistry.Register(id, kind, "1", inputSchema, outputSchema, callback);
        }
        internal static JObject Catalog()
        {
            var installed = new HashSet<string>(PackageInfo.GetAllRegisteredPackages().Select(p => p.name));
            var catalog = DebugRegistry.Catalog();
            catalog.Add(new JObject { ["id"] = "object.state", ["kind"] = "query", ["version"] = "1", ["permission"] = "observe", ["inputSchema"] = DebugSchemas.TargetInput(),
                ["outputSchema"] = new JObject { ["type"] = "object", ["required"] = new JArray("target", "status", "name", "type", "frame", "playSessionId", "activeSelf", "activeInHierarchy"), ["properties"] = new JObject { ["target"] = DebugSchemas.Target(), ["status"] = DebugSchemas.String(16), ["name"] = DebugSchemas.String(), ["type"] = DebugSchemas.String(), ["frame"] = DebugSchemas.Number(), ["playSessionId"] = DebugSchemas.String(64), ["activeSelf"] = new JObject { ["type"] = new JArray("boolean", "null") }, ["activeInHierarchy"] = new JObject { ["type"] = new JArray("boolean", "null") } }, ["additionalProperties"] = false } });
            foreach (var id in new[] { "events", "events.start", "events.stop" }) catalog.Add(new JObject { ["id"] = id, ["kind"] = id == "events" ? "query" : "action", ["version"] = "1", ["permission"] = id == "events" ? "observe" : "execute",
                ["inputSchema"] = id == "events.start" ? DebugSchemas.Object(new JObject { ["kinds"] = new JObject { ["type"] = "array", ["items"] = DebugSchemas.String(160), ["maxItems"] = 32, ["minItems"] = 1 }, ["maxDurationMs"] = DebugSchemas.Number(1, 60000), ["maxFrames"] = DebugSchemas.Number(1, 3600) }, "kinds", "maxDurationMs", "maxFrames") : id == "events" ? DebugSchemas.Object(new JObject { ["afterSequence"] = DebugSchemas.Number(0) }) : DebugRegistry.EmptySchema,
                ["outputSchema"] = new JObject { ["type"] = "object", ["required"] = new JArray("status", "collectionId"), ["properties"] = new JObject { ["status"] = DebugSchemas.String(32), ["collectionId"] = new JObject { ["type"] = new JArray("string", "null") }, ["items"] = new JObject { ["type"] = "array", ["maxItems"] = 200, ["items"] = new JObject { ["type"] = "object", ["required"] = new JArray("sequence", "kind", "data", "source", "target", "observedAt", "frame") } } } } });
            var optional = new JObject();
            foreach (var entry in new[] { new[] { "input", "com.unity.inputsystem", "input.state" }, new[] { "ui", "com.unity.ugui", "ui.state" }, new[] { "physics", "com.unity.modules.physics", "physics.state" } })
                optional[entry[0]] = new JObject { ["implementationPresent"] = true, ["package"] = entry[1], ["availability"] = !installed.Contains(entry[1]) ? "package_absent" : catalog.Any(c => (string)c["id"] == entry[2]) ? "active" : "package_present_adapter_not_registered" };
            return new JObject { ["status"] = "observed", ["adapters"] = catalog, ["optionalPackages"] = optional, ["objectState"] = "unity_object_read_serialized_and_object.state", ["schemaDialect"] = "bounded_closed_json_subset_v1" };
        }
        internal static JObject Query(JObject args)
        {
            string id = (string)args["adapter"];
            if (id == "catalog") return Catalog();
            if (id == "events") {
                if (((JObject)args["input"]).Properties().Any(p => p.Name != "afterSequence") || args["input"]?["afterSequence"] != null && (long)args["input"]["afterSequence"] < 0) throw new BridgeException("invalid_arguments", "events accepts only afterSequence >= 0");
                var rows = Events.Where(e => (long)e["sequence"] > ((long?)args["input"]?["afterSequence"] ?? 0)).ToList();
                var result = Objects.Page(rows, args, Bridge.Digest(collection + sequence));
                result["collectionId"] = collection; result["startedAt"] = started; result["active"] = Active(); result["retention"] = "latest 500 events / 256 KiB; observations after explicit start only";
                result["firstRetainedSequence"] = Events.Count > 0 ? Events[0]["sequence"] : null; return result;
            }
            if (id == "object.state") {
                if (((JObject)args["input"]).Properties().Any(p => p.Name != "target")) throw new BridgeException("invalid_arguments", "object.state accepts only target");
                var o = Objects.Resolve(args["input"]?["target"]); var result = Objects.Summary(o);
                var go = o as GameObject ?? (o as Component)?.gameObject;
                result["status"] = "observed"; result["activeSelf"] = go == null ? (JToken)JValue.CreateNull() : go.activeSelf; result["activeInHierarchy"] = go == null ? (JToken)JValue.CreateNull() : go.activeInHierarchy;
                result["frame"] = Time.frameCount; result["playSessionId"] = Bridge.PlaySession; return result;
            }
            return new JObject { ["status"] = "observed", ["adapter"] = id, ["version"] = "1", ["data"] = DebugRegistry.Invoke(id, "query", (JObject)args["input"]) };
        }
        internal static JObject Execute(JObject args)
        {
            string id = (string)args["adapter"];
            if (id == "events.start") {
                var input = (JObject)args["input"]; int duration = (int?)input["maxDurationMs"] ?? 0, frames = (int?)input["maxFrames"] ?? 0;
                if (input.Properties().Any(p => !new[] { "maxDurationMs", "maxFrames", "kinds" }.Contains(p.Name))) throw new BridgeException("invalid_arguments", "Undeclared event collection option");
                if (duration < 1 || duration > 60000 || frames < 1 || frames > 3600 || !(input["kinds"] is JArray filter) || filter.Count < 1 || filter.Count > 32) throw new BridgeException("invalid_bounds", "Explicit kinds, 1..60000 ms and 1..3600 frames required");
                Events.Clear(); bytes = 0; collection = Guid.NewGuid().ToString("N"); started = DateTime.UtcNow.ToString("O"); play = Bridge.PlaySession;
                kinds = new HashSet<string>(filter.Values<string>()); until = EditorApplication.timeSinceStartup + duration / 1000.0; endFrame = Time.frameCount + frames;
                return new JObject { ["status"] = "applied", ["collectionId"] = collection, ["startedAt"] = started };
            }
            if (id == "events.stop") { if (((JObject)args["input"]).Count != 0) throw new BridgeException("invalid_arguments", "events.stop requires empty input"); until = 0; return new JObject { ["status"] = "applied", ["collectionId"] = collection }; }
            if (!EditorApplication.isPlaying) throw new BridgeException("play_required", "Registered debug actions are Play-session operations");
            try { return new JObject { ["status"] = "applied", ["adapter"] = id, ["data"] = DebugRegistry.Invoke(id, "action", (JObject)args["input"], (string)args["operationId"]), ["verification"] = "not_run" }; }
            catch (Exception e) { return Bridge.Error(e, "outcome_unknown"); } // Callback may have changed runtime state before throwing/output validation.
        }
        static bool Active() => collection != null && EditorApplication.timeSinceStartup <= until && Time.frameCount <= endFrame && play == Bridge.PlaySession;
        static void OnEvent(string kind, JObject data, UnityEngine.Object source, UnityEngine.Object target, string correlation)
        {
            // Worker-thread observations cannot touch Unity objects and are explicitly not collected.
            if (Thread.CurrentThread.ManagedThreadId != thread || !Active() || !kinds.Contains(kind)) return;
            var item = new JObject { ["sequence"] = ++sequence, ["kind"] = kind, ["data"] = data, ["source"] = Objects.Ref(source), ["target"] = Objects.Ref(target), ["correlationId"] = correlation,
                ["collectionId"] = collection, ["editorSessionId"] = Bridge.Session, ["playSessionId"] = Bridge.PlaySession, ["frame"] = Time.frameCount, ["observedAt"] = DateTime.UtcNow.ToString("O") };
            int size = item.ToString(Formatting.None).Length * 2; if (size > 32768) return;
            Events.Add(item); bytes += size;
            while (Events.Count > 500 || bytes > 262144) { bytes -= Events[0].ToString(Formatting.None).Length * 2; Events.RemoveAt(0); }
        }
        internal static List<JObject> RuntimeReferences() => Events.Where(e => e["source"] is JObject && e["target"] is JObject).Select(e => new JObject {
            ["kind"] = "observed_runtime_reference", ["from"] = e["source"].DeepClone(), ["to"] = e["target"].DeepClone(), ["evidence"] = e.DeepClone() }).ToList();
    }
}
