using System;
using System.IO;
using System.Linq;
using System.Text;
using System.Text.RegularExpressions;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using UnityEditor.PackageManager;
namespace EvidenceFirst.UnityBridge
{
    // Optional package API is injected by its own assembly. No Test Framework reference
    // in the base Bridge and no automatic package installation.
    public static class TestExecution
    {
        static Func<JObject, JObject> adapter;
        public static void Register(Func<JObject, JObject> handler) { adapter = handler; }
        public static bool Available => adapter != null;
        internal static JObject Call(JObject args) {
            if (adapter == null) return new JObject { ["status"] = "unavailable", ["errorCode"] = "capability_unavailable", ["testStatus"] = "not_run",
                ["availability"] = PackageInfo.GetAllRegisteredPackages().Any(p => p.name == "com.unity.test-framework") ? "package_present_adapter_unavailable" : "package_absent", ["implemented"] = true };
            try { return adapter(args); }
            catch (InvalidOperationException e) { return new JObject { ["status"] = "not_applied", ["errorCode"] = "test_request_rejected", ["message"] = e.Message }; }
        }
        static string Folder { get { var path = Path.Combine(Bridge.StateDirectory, "test-runs"); PathSafety.CheckInternal(path); Directory.CreateDirectory(path); PathSafety.Private(path, true); return path; } }
        static string PathFor(string id) { if (id == null || !Regex.IsMatch(id, "^[A-Za-z0-9_-]{8,100}$")) throw new BridgeException("invalid_operation_id", "Invalid test operation ID"); var path = Path.Combine(Folder, id + ".json"); PathSafety.CheckInternal(path); return path; }
        public static JObject Load(string id) {
            string path = PathFor(id); if (!File.Exists(path)) throw new BridgeException("test_run_unknown", "No retained test run");
            if (new FileInfo(path).Length > 1048576) throw new BridgeException("evidence_corrupt", "Test result exceeds bound");
            var value = JObject.Parse(File.ReadAllText(path));
            if ((string)value["projectIdentity"] != Bridge.ProjectId) throw new BridgeException("binding_mismatch", "Wrong test project");
            if ((string)value["editorSessionId"] != Bridge.Session && new[] { "preparing", "accepted", "running", "cancel_requested" }.Contains((string)value["status"])) {
                value["status"] = "outcome_unknown"; value["errorCode"] = "editor_session_ended";
                value["recovery"] = "retained observations only; execution is never resumed or replayed"; Save(value);
            }
            return value;
        }
        public static void Save(JObject value) {
            string path = PathFor((string)value["operationId"]); value["projectIdentity"] = Bridge.ProjectId;
            if (value["editorSessionId"] == null) { value["editorSessionId"] = Bridge.Session; value["domainGenerationAtRequest"] = Bridge.Generation; }
            string json = value.ToString(Formatting.None); if (Encoding.UTF8.GetByteCount(json) > 1048576) throw new BridgeException("test_result_capacity", "1 MiB/run bound exceeded");
            var files = new DirectoryInfo(Folder).GetFiles("*.json"); if (!File.Exists(path) && files.Length >= 16) throw new BridgeException("test_history_capacity", "16 retained runs; release an old result explicitly");
            PathSafety.WritePrivate(path, json);
        }
        public static JObject Page(JObject run, JObject args) {
            var copy = (JObject)run.DeepClone(); copy.Remove("results"); copy.Remove("selectedTests");
            if ((string)args["action"] == "results") {
                var rows = (run["results"] as JArray ?? new JArray()).OfType<JObject>().ToList();
                copy["page"] = Objects.Page(rows, args, Bridge.Digest(run.ToString(Formatting.None)));
            }
            return copy;
        }
        public static JObject Release(string id) { Load(id); File.Delete(PathFor(id)); return new JObject { ["status"] = "released", ["operationId"] = id }; }
        public static string Session => Bridge.Session;
    }
}
