using System;
using System.Collections.Generic;
using System.Linq;
using System.Reflection;
using System.Text;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEditor.TestTools.TestRunner.Api;
using UnityEngine;
using EvidenceFirst.UnityBridge;
namespace EvidenceFirst.Adapters
{
    [InitializeOnLoad]
    public sealed class FrameworkAdapter : ICallbacks
    {
        const string ActiveKey = "EvidenceFirst.ActiveTestOperation";
        static TestRunnerApi api; static readonly FrameworkAdapter Callbacks = new FrameworkAdapter();
        static JObject active; static bool initialized;
        // Fixed framework status methods only, never user-selected reflection/getters.
        static readonly MethodInfo IsActive = typeof(TestRunnerApi).GetMethod("IsRunActive", BindingFlags.Static | BindingFlags.Public | BindingFlags.NonPublic);
        static readonly MethodInfo IsRunning = typeof(TestRunnerApi).GetMethod("IsRunning", BindingFlags.Static | BindingFlags.Public | BindingFlags.NonPublic, null, new[] { typeof(string) }, null);
        static FrameworkAdapter() { EditorApplication.delayCall += Init; }
        static void Init() {
            if (TestExecution.Session == null) { EditorApplication.delayCall += Init; return; }
            if (initialized) return; initialized = true;
            api = ScriptableObject.CreateInstance<TestRunnerApi>(); api.RegisterCallbacks(Callbacks);
            TestExecution.Register(Call); EditorApplication.update += Tick;
            AssemblyReloadEvents.beforeAssemblyReload += () => { api.UnregisterCallbacks(Callbacks); EditorApplication.update -= Tick; };
            string id = SessionState.GetString(ActiveKey, "");
            if (id.Length > 0) { try { active = TestExecution.Load(id); if ((string)active["editorSessionId"] != TestExecution.Session) { active["status"] = "outcome_unknown"; Persist(); active = null; } } catch { active = null; } }
        }
        static bool FrameworkBusy() => IsActive == null || (bool)IsActive.Invoke(null, null);
        static bool OwnRunning() => active != null && active["runId"] != null && IsRunning != null && (bool)IsRunning.Invoke(null, new object[] { (string)active["runId"] });
        static bool Pending => active != null && new[] { "preparing", "accepted", "running", "cancel_requested" }.Contains((string)active["status"]);
        static void Persist() { if (active != null) TestExecution.Save(active); }
        static void Finish(string status, string error = null) { active["status"] = status; active["endedAt"] = DateTime.UtcNow.ToString("O"); if (error != null) active["errorCode"] = error; Persist(); SessionState.EraseString(ActiveKey); }
        static string[] Strings(JObject args, string field) => (args[field] as JArray)?.Values<string>().ToArray() ?? Array.Empty<string>();
        static JObject Call(JObject args) {
            string action = (string)args["action"], id = (string)args["operationId"];
            if (action == "status" && id == null) return new JObject { ["status"] = "observed", ["availability"] = IsActive != null && IsRunning != null ? "active" : "package_api_unsupported", ["implemented"] = true, ["busy"] = Pending || OwnRunning() || FrameworkBusy(), ["activeOperationId"] = Pending ? active["operationId"] : null, ["modes"] = new JArray("EditMode", "PlayMode") };
            if (action == "status" || action == "results") return TestExecution.Page(TestExecution.Load(id), args);
            if (action == "release") { if ((Pending || OwnRunning()) && id == (string)active["operationId"]) throw new InvalidOperationException("Cannot release active test run"); return TestExecution.Release(id); }
            if (action == "cancel") {
                if (!Pending || id != (string)active["operationId"]) return new JObject { ["status"] = "not_cancelled", ["operationId"] = id, ["reason"] = "No active owned run" };
                RequestCancel("explicit_request"); return TestExecution.Page(active, args);
            }
            if (action != "run") throw new InvalidOperationException("Unknown test action");
            if (Pending || OwnRunning() || FrameworkBusy()) throw new InvalidOperationException("Test runner busy or status API unavailable");
            if (EditorApplication.isPlayingOrWillChangePlaymode || EditorApplication.isCompiling || EditorApplication.isUpdating) throw new InvalidOperationException("Start tests from idle Edit Mode");
            var names = Strings(args, "testNames"); var categories = Strings(args, "categories");
            if (names.Length + categories.Length == 0 || names.Length > 32 || categories.Length > 32) throw new InvalidOperationException("Provide exact test names or categories, max 32 each");
            if ((bool?)args["acknowledgeSceneChanges"] != true) throw new InvalidOperationException("Test Framework may switch scenes/Play state; explicit acknowledgement required");
            for (int i = 0; i < UnityEngine.SceneManagement.SceneManager.sceneCount; i++) if (UnityEngine.SceneManagement.SceneManager.GetSceneAt(i).isDirty) throw new InvalidOperationException("Save or discard dirty scenes explicitly before tests");
            string modeName = (string)args["mode"]; if (modeName != "EditMode" && modeName != "PlayMode") throw new InvalidOperationException("Exact EditMode or PlayMode required");
            int ms = (int?)args["maxDurationMs"] ?? 0, maxTests = (int?)args["maxTests"] ?? 0;
            if (ms < 100 || ms > 600000 || maxTests < 1 || maxTests > 256) throw new InvalidOperationException("Explicit time/test bounds required (100..600000 ms, 1..256 tests)");
            var next = new JObject { ["operationId"] = id, ["status"] = "preparing", ["testStatus"] = "not_run", ["mode"] = modeName, ["startedAt"] = DateTime.UtcNow.ToString("O"),
                ["deadline"] = DateTime.UtcNow.AddMilliseconds(ms).ToString("O"), ["maxDurationMs"] = ms, ["maxTests"] = maxTests, ["results"] = new JArray(), ["resultCount"] = 0, ["droppedResults"] = 0, ["correlation"] = "owned_framework_run_and_selected_test_names" };
            TestExecution.Save(next); active = next; SessionState.SetString(ActiveKey, id); var mode = modeName == "EditMode" ? TestMode.EditMode : TestMode.PlayMode;
            api.RetrieveTestList(mode, root => {
                if (active == null || (string)active["operationId"] != id || (string)active["status"] != "preparing") return;
                try {
                    var matches = new List<string>(); var queue = new Queue<ITestAdaptor>(); queue.Enqueue(root); int visited = 0;
                    while (queue.Count > 0) { var t = queue.Dequeue(); if (++visited > 20000) throw new InvalidOperationException("Test discovery exceeds 20000 nodes"); if (t.HasChildren) foreach (var child in t.Children) { if (queue.Count + visited >= 20000) throw new InvalidOperationException("Test discovery exceeds 20000 nodes"); queue.Enqueue(child); }
                        if (!t.IsSuite && (names.Length == 0 || names.Contains(t.FullName)) && (categories.Length == 0 || (t.Categories ?? Array.Empty<string>()).Intersect(categories).Any())) matches.Add(t.FullName); }
                    if (matches.Count == 0) { Finish("not_run", "no_tests_matched"); return; }
                    if (matches.Count > maxTests || matches.Distinct().Count() != matches.Count) { Finish("not_run", "test_selection_exceeds_bound_or_ambiguous"); return; }
                    if (matches.Any(n => n.Length > 4096) || Encoding.UTF8.GetByteCount(new JArray(matches).ToString(Formatting.None)) > 131072) { Finish("not_run", "test_names_exceed_byte_bound"); return; }
                    if (FrameworkBusy()) { Finish("not_run", "test_runner_became_busy"); return; }
                    active["selectedTests"] = new JArray(matches); active["selectedCount"] = matches.Count; active["status"] = "accepted"; Persist();
                    string runId = api.Execute(new ExecutionSettings(new Filter { testMode = mode, testNames = matches.ToArray() }) { runSynchronously = false });
                    active["runId"] = runId; Persist();
                } catch (Exception e) { Finish("execution_failed", e.Message); }
            });
            return TestExecution.Page(active, args);
        }
        static void RequestCancel(string reason) {
            active["cancelReason"] = reason;
            if ((string)active["status"] == "preparing") { Finish("not_run", "cancelled_before_execution"); return; }
            bool accepted = active["runId"] != null && TestRunnerApi.CancelTestRun((string)active["runId"]);
            active["status"] = "cancel_requested"; active["cancelRequestAccepted"] = accepted; active["cancelRequestedAt"] = DateTime.UtcNow.ToString("O"); Persist();
        }
        static void Tick() {
            if (!Pending) return;
            if (DateTime.UtcNow > DateTime.Parse((string)active["deadline"]).ToUniversalTime() && (string)active["status"] != "cancel_requested") RequestCancel("time_limit");
            if ((string)active["status"] == "cancel_requested" && DateTime.UtcNow > DateTime.Parse((string)active["cancelRequestedAt"]).ToUniversalTime().AddSeconds(10)) Finish("outcome_unknown", "cancel_completion_not_observed");
            if (Pending && active["runId"] != null && !OwnRunning() && DateTime.UtcNow > DateTime.Parse((string)active["startedAt"]).ToUniversalTime().AddSeconds(5)) {
                if ((string)active["status"] == "cancel_requested" && (bool?)active["cancelRequestAccepted"] == true) {
                    active["testStatus"] = "incomplete"; active["completionEvidence"] = "owned_framework_job_no_longer_running";
                    active["limitations"] = "Cancellation does not reverse test side effects or guarantee termination of test-created external work"; Finish("cancelled");
                } else Finish("outcome_unknown", "run_ended_without_matching_callback");
            }
        }
        public void RunStarted(ITestAdaptor testsToRun) {
            if (!Pending) return;
            if ((string)active["status"] != "accepted") { Finish("outcome_unknown", "unexpected_framework_run_started"); return; }
            if (active["runId"] != null && !OwnRunning()) { Finish("outcome_unknown", "unowned_framework_start"); return; }
            active["rootTestId"] = testsToRun.Id; active["rootFullName"] = testsToRun.FullName; active["status"] = "running"; active["testStatus"] = "running"; Persist();
        }
        public void TestStarted(ITestAdaptor test) { }
        public void TestFinished(ITestResultAdaptor result) {
            if (!Pending || result.Test.IsSuite || !(active["selectedTests"] as JArray ?? new JArray()).Values<string>().Contains(result.FullName)) return;
            var rows = (JArray)active["results"]; if (rows.Count >= 256) { active["droppedResults"] = (int)active["droppedResults"] + 1; Persist(); return; }
            string Clip(string s) => s == null ? null : s.Substring(0, Math.Min(s.Length, 800));
            var row = new JObject { ["name"] = result.FullName, ["resultState"] = result.ResultState, ["duration"] = result.Duration, ["message"] = Clip(result.Message), ["stackTrace"] = Clip(result.StackTrace), ["output"] = Clip(result.Output), ["outputTruncated"] = (result.Output?.Length ?? 0) > 800 || (result.Message?.Length ?? 0) > 800 || (result.StackTrace?.Length ?? 0) > 800 };
            // Reserve room for terminal metadata, including multibyte output.
            if (Encoding.UTF8.GetByteCount(active.ToString(Formatting.None)) + Encoding.UTF8.GetByteCount(row.ToString(Formatting.None)) > 900000) {
                active["droppedResults"] = (int)active["droppedResults"] + 1; active["omission"] = "retained byte capacity"; Persist(); return;
            }
            rows.Add(row);
            active["resultCount"] = rows.Count; Persist();
        }
        public void RunFinished(ITestResultAdaptor result) {
            if (!Pending || active["rootTestId"] == null) return;
            // PlayMode reconstructs NUnit trees and their numeric IDs. Correlate the
            // single owned run, observed start and exact selected leaf names instead.
            active["finishedRootTestId"] = result.Test.Id; active["finishedRootFullName"] = result.FullName;
            var selected = new HashSet<string>(((JArray)active["selectedTests"]).Values<string>());
            var queue = new Queue<ITestResultAdaptor>(); queue.Enqueue(result); int visited = 0, leaves = 0;
            while (queue.Count > 0) {
                var r = queue.Dequeue();
                if (++visited > 20000) { Finish("outcome_unknown", "result_tree_exceeds_bound"); return; }
                if (r.HasChildren) foreach (var c in r.Children) queue.Enqueue(c);
                if (!r.Test.IsSuite) { if (!selected.Remove(r.FullName)) { Finish("outcome_unknown", "unexpected_or_duplicate_test_result"); return; } leaves++; }
            }
            if ((string)active["rootFullName"] != result.FullName || (leaves == 0 && (string)active["status"] != "cancel_requested")) { Finish("outcome_unknown", "test_result_correlation_incomplete"); return; }
            active["correlation"] = "single_owned_run_observed_start_root_name_and_exact_result_leaf_names";
            active["testStatus"] = result.ResultState; active["passed"] = result.PassCount; active["failed"] = result.FailCount; active["skipped"] = result.SkipCount; active["inconclusive"] = result.InconclusiveCount;
            Finish("completed");
        }
    }
}
