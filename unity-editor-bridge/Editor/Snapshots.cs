using System;
using System.Linq;
using System.Collections.Generic;
using System.IO;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEngine;
namespace EvidenceFirst.UnityBridge
{
    internal static class Snapshots
    {
        static readonly EvidenceStore Store = new EvidenceStore("snapshots");
        static JObject recording; static double until, next; static int endFrame, maxSamples, maxBytes, bytes; static JArray samples;
        static string RecordingPath => Path.Combine(Bridge.StateDirectory, "last-recording.json");
        internal static void Recover() {
            PathSafety.CheckInternal(RecordingPath);
            if (!File.Exists(RecordingPath)) return;
            if (new FileInfo(RecordingPath).Length > 16384) return;
            recording = JObject.Parse(File.ReadAllText(RecordingPath)); samples = recording["snapshotIds"] as JArray ?? new JArray();
            if ((string)recording["status"] == "recording") { recording["status"] = "stopped"; recording["reason"] = "domain_or_editor_ended"; }
            SaveRecording();
        }
        static void SaveRecording() {
            var saved = (JObject)recording.DeepClone(); saved.Remove("selection"); saved["snapshotIds"] = samples.DeepClone();
            PathSafety.WritePrivate(RecordingPath, saved.ToString(Formatting.None));
        }
        internal static void Tick()
        {
            if (recording == null || (string)recording["status"] != "recording") return;
            if ((string)recording["playSessionId"] != Bridge.PlaySession || (int)recording["domainGeneration"] != Bridge.Generation) { recording["status"] = "stopped"; recording["reason"] = "session_changed"; SaveRecording(); return; }
            if (EditorApplication.timeSinceStartup >= until || Time.frameCount > endFrame || samples.Count >= maxSamples) { recording["status"] = "stopped"; recording["reason"] = "configured_limit"; SaveRecording(); return; }
            if (EditorApplication.timeSinceStartup < next) return;
            next = EditorApplication.timeSinceStartup + (int)recording["intervalMs"] / 1000.0;
            try {
                var sample = CaptureData((JObject)recording["selection"]); int size = System.Text.Encoding.UTF8.GetByteCount(sample.ToString(Formatting.None));
                if (bytes + size > maxBytes) { recording["status"] = "stopped"; recording["reason"] = "byte_limit"; SaveRecording(); return; }
                samples.Add(Store.Put(sample)); bytes += size; recording["sampleCount"] = samples.Count; recording["bytes"] = bytes;
            } catch (Exception e) { recording["status"] = "stopped"; recording["reason"] = e is BridgeException b ? b.Code : "capture_failed"; }
            try { SaveRecording(); } catch { recording["status"] = "stopped"; recording["reason"] = "record_metadata_write_failed"; }
        }
        internal static JObject Call(JObject args)
        {
            string action = (string)args["action"];
            if (action == "capture") {
                var data = CaptureData(args); string id = Store.Put(data); var result = Metadata(data, id);
                // Persistence has succeeded. Never lose its ID or report not_applied just
                // because the caller selected a small response budget. Full metadata is
                // retained and can be paged separately with read(section=...).
                int budget = Math.Min((int?)args["byteBudget"] ?? 65536, 60000) - 384;
                if (System.Text.Encoding.UTF8.GetByteCount(result.ToString(Formatting.None)) > budget)
                    return new JObject { ["status"] = "stored", ["snapshotId"] = id, ["targetCount"] = ((JArray)data["objects"]).Count,
                        ["metadataOmitted"] = true, ["metadataRead"] = "read with section or targetIndex", ["atomic"] = false };
                return result;
            }
            if (action == "release") return Store.Release((string)args["snapshotId"]);
            if (action == "read") {
                var s = Store.Get((string)args["snapshotId"]);
                if (args["section"] != null) {
                    string section = (string)args["section"];
                    var rows = section == "scope" ? ((JArray)s["scope"]).OfType<JObject>().ToList() : section == "failures" ? ((JArray)s["objects"]).OfType<JObject>().Where(o => (string)o["state"] != "collected").ToList() :
                        section == "metadata" ? s.Properties().Where(p => p.Name != "objects" && p.Name != "scope" && p.Name != "scopeStates").Select(p => new JObject { ["name"] = p.Name, ["value"] = p.Value.DeepClone() }).ToList() : throw new BridgeException("invalid_arguments", "Unknown snapshot section");
                    var page = Objects.Page(rows, args, (string)s["id"] + section); page["snapshotId"] = s["id"]; return page;
                }
                var result = args["targetIndex"] != null ? new JObject { ["status"] = "stored", ["snapshotId"] = s["id"], ["editorSessionId"] = s["editorSessionId"], ["playSessionId"] = s["playSessionId"], ["atomic"] = false } : Metadata(s, (string)s["id"]);
                if (args["targetIndex"] != null) {
                    int i = (int)args["targetIndex"]; var targets = (JArray)s["objects"]; if (i < 0 || i >= targets.Count) throw new BridgeException("invalid_target_index", "Outside stored scope");
                    var row = (JObject)targets[i]; result["target"] = row["target"]; result["state"] = row["state"];
                    var fields = ((row["fields"] as JArray) ?? new JArray()).OfType<JObject>().Where(f => !(args["propertyPaths"] is JArray paths) || paths.Count == 0 || paths.Values<string>().Contains((string)f["propertyPath"])).ToList();
                    result["values"] = Objects.Page(fields, args, Bridge.Digest((string)s["id"] + i + args["propertyPaths"]));
                }
                return result;
            }
            if (action == "diff") return Diff(Store.Get((string)args["a"]), Store.Get((string)args["b"]), args);
            if (action == "record_start") {
                if (recording != null && (string)recording["status"] == "recording") throw new BridgeException("recording_busy", "Only one bounded recording");
                int ms = (int?)args["maxDurationMs"] ?? 0, frames = (int?)args["maxFrames"] ?? 0, interval = (int?)args["intervalMs"] ?? 0;
                maxSamples = (int?)args["maxSamples"] ?? 0; maxBytes = (int?)args["maxBytes"] ?? 0;
                if (ms < 1 || ms > 60000 || frames < 1 || frames > 3600 || interval < 50 || interval > 60000 || maxSamples < 1 || maxSamples > 32 || maxBytes < 1024 || maxBytes > 4194304) throw new BridgeException("invalid_bounds", "All explicit recording bounds are required");
                ValidateSelection(args); samples = new JArray(); bytes = 0; until = EditorApplication.timeSinceStartup + ms / 1000.0; next = 0; endFrame = Time.frameCount + frames;
                recording = new JObject { ["recordingId"] = Guid.NewGuid().ToString("N"), ["status"] = "recording", ["playSessionId"] = Bridge.PlaySession, ["domainGeneration"] = Bridge.Generation,
                    ["selection"] = args.DeepClone(), ["intervalMs"] = interval, ["sampleCount"] = 0, ["bytes"] = 0, ["startedAt"] = DateTime.UtcNow.ToString("O"), ["maxDurationMs"] = ms, ["maxFrames"] = frames };
                SaveRecording(); return RecordingStatus();
            }
            if (action == "record_status" || action == "record_stop") {
                if (recording == null || (string)args["recordingId"] != (string)recording["recordingId"]) throw new BridgeException("recording_unavailable", "Only the latest recording metadata is retained; explicitly retained snapshot IDs remain readable");
                if (action == "record_stop") { recording["status"] = "stopped"; recording["reason"] = "explicit_stop"; }
                SaveRecording(); return RecordingStatus();
            }
            throw new BridgeException("invalid_arguments", "Unknown snapshot action");
        }
        static JObject RecordingStatus() { var result = (JObject)recording.DeepClone(); result.Remove("selection"); result["snapshotIds"] = samples.DeepClone(); result["atomic"] = false; return result; }
        static JArray ValidateSelection(JObject args) {
            var selections = args["targets"] as JArray;
            if (selections == null || selections.Count < 1 || selections.Count > 32) throw new BridgeException("invalid_scope", "Explicit 1..32 target/field selections required");
            foreach (var s in selections) {
                if (!(s["target"] is JObject) || !(s["propertyPaths"] is JArray fields) || fields.Count < 1 || fields.Count > 32 || fields.Any(f => f.Type != JTokenType.String || ((string)f).Length > 4096) || fields.Values<string>().Distinct().Count() != fields.Count) throw new BridgeException("invalid_scope", "Each target needs 1..32 unique exact propertyPaths");
                if (s["descendantDepth"] != null && ((int)s["descendantDepth"] < 1 || (int)s["descendantDepth"] > 6)) throw new BridgeException("invalid_scope", "descendantDepth must be 1..6");
            }
            if (selections.Select(s => Key(s["target"])).Distinct().Count() != selections.Count) throw new BridgeException("invalid_scope", "Duplicate targets are not allowed");
            return selections;
        }
        static JObject CaptureData(JObject args)
        {
            var selections = ValidateSelection(args); int frame = Time.frameCount; var started = DateTime.UtcNow.ToString("O"); var rows = new JArray(); var expanded = new JArray(); var scopeStates = new JObject();
            foreach (var selection in selections) {
                string scopeId = Bridge.Digest(Key(selection)); var root = (JObject)selection.DeepClone(); root["collectionScopeId"] = scopeId; expanded.Add(root);
                scopeStates[scopeId] = new JObject { ["complete"] = true, ["descendantDepth"] = selection["descendantDepth"] };
                if (selection["descendantDepth"] != null) {
                    try {
                        var go = Objects.Resolve(selection["target"]) as GameObject; if (go == null) throw new BridgeException("gameobject_required", "Descendant capture requires an explicit GameObject root");
                        var queue = new Queue<KeyValuePair<Transform, int>>(); queue.Enqueue(new KeyValuePair<Transform, int>(go.transform, 0));
                        while (queue.Count > 0) {
                            var current = queue.Dequeue(); if (current.Value >= (int)selection["descendantDepth"]) continue;
                            for (int i = 0; i < current.Key.childCount; i++) {
                                if (expanded.Count >= 32) throw new BridgeException("snapshot_target_capacity", "Explicit descendant scope exceeds 32 captured objects; narrow root/depth");
                                var child = current.Key.GetChild(i); var childSelection = (JObject)root.DeepClone(); childSelection["target"] = Objects.Ref(child.gameObject); childSelection.Remove("descendantDepth"); expanded.Add(childSelection);
                                queue.Enqueue(new KeyValuePair<Transform, int>(child, current.Value + 1));
                            }
                        }
                    } catch (BridgeException e) { if (e.Code == "snapshot_target_capacity") throw; scopeStates[scopeId]["complete"] = false; scopeStates[scopeId]["errorCode"] = e.Code; }
                }
                if (expanded.Count > 32) throw new BridgeException("snapshot_target_capacity", "Maximum 32 total captured objects");
            }
            if (expanded.Select(s => Key(s["target"])).Distinct().Count() != expanded.Count) throw new BridgeException("invalid_scope", "Overlapping object selections are not allowed");
            foreach (var selection in expanded) {
                var row = new JObject { ["target"] = selection["target"].DeepClone(), ["collectionScopeId"] = selection["collectionScopeId"], ["requestedFields"] = selection["propertyPaths"].DeepClone(), ["state"] = "collected" };
                try { var read = Objects.Read(new JObject { ["target"] = selection["target"].DeepClone(), ["propertyPaths"] = selection["propertyPaths"].DeepClone(), ["limit"] = 200, ["depth"] = 0 }, false); row["fields"] = read["items"]; row["frame"] = read["observedFrame"]; }
                catch (BridgeException e) { row["state"] = e.Code == "expired_object_ref" ? "expired_handle" : e.Code == "object_not_found" ? "object_missing" : "read_failed"; row["errorCode"] = e.Code; }
                rows.Add(row);
            }
            return new JObject { ["projectIdentity"] = Bridge.ProjectId, ["editorSessionId"] = Bridge.Session, ["domainGeneration"] = Bridge.Generation, ["playSessionId"] = Bridge.PlaySession,
                ["startedAt"] = started, ["endedAt"] = DateTime.UtcNow.ToString("O"), ["frameStart"] = frame, ["frameEnd"] = Time.frameCount, ["compilationId"] = Observations.CompilationId,
                ["compiling"] = EditorApplication.isCompiling, ["sourceStateVerification"] = "unknown_not_hashed_by_capture", ["schemaVersion"] = "1", ["adapterVersion"] = Bridge.Version,
                ["atomic"] = false, ["paused"] = EditorApplication.isPaused, ["scope"] = selections.DeepClone(), ["scopeStates"] = scopeStates, ["objects"] = rows,
                ["omissions"] = new JArray("Only explicit fields collected; arrays require explicit element paths; no arbitrary getters; capture is not rollback") };
        }
        static JObject Metadata(JObject s, string id)
        {
            var result = (JObject)s.DeepClone(); result.Remove("objects"); result["snapshotId"] = id; result["status"] = "stored"; result["targetCount"] = ((JArray)s["objects"]).Count;
            result["failures"] = new JArray(((JArray)s["objects"]).OfType<JObject>().Where(o => (string)o["state"] != "collected").Select(o => new JObject { ["target"] = o["target"], ["state"] = o["state"], ["errorCode"] = o["errorCode"] })); return result;
        }
        static string Key(JToken token) => token is JObject o ? "{" + String.Join(",", o.Properties().OrderBy(p => p.Name, StringComparer.Ordinal).Select(p => p.Name + ":" + Key(p.Value))) + "}" : token?.ToString(Formatting.None);
        static JObject Diff(JObject a, JObject b, JObject args)
        {
            var left = ((JArray)a["objects"]).OfType<JObject>().ToDictionary(o => Key(o["target"])); var right = ((JArray)b["objects"]).OfType<JObject>().ToDictionary(o => Key(o["target"]));
            var changes = new List<JObject>();
            foreach (var key in left.Keys.Union(right.Keys)) {
                left.TryGetValue(key, out var x); right.TryGetValue(key, out var y); var target = (x ?? y)["target"];
                void Change(string kind, string field, JToken oldValue, JToken newValue) => changes.Add(new JObject { ["kind"] = kind, ["target"] = target.DeepClone(), ["propertyPath"] = field, ["before"] = oldValue?.DeepClone(), ["after"] = newValue?.DeepClone() });
                bool sessionBound = (string)target["kind"] == "runtime" || (string)target["kind"] == "temporary";
                if (sessionBound && (!JToken.DeepEquals(a["editorSessionId"], b["editorSessionId"]) || !JToken.DeepEquals(a["domainGeneration"], b["domainGeneration"]) || !JToken.DeepEquals(a["playSessionId"], b["playSessionId"]))) { Change("incomparable", null, a["playSessionId"], b["playSessionId"]); continue; }
                if (x == null || y == null) {
                    string scopeId = (string)(x ?? y)["collectionScopeId"];
                    bool sameObservedScope = scopeId != null && (bool?)a["scopeStates"]?[scopeId]?["complete"] == true && (bool?)b["scopeStates"]?[scopeId]?["complete"] == true && a["scopeStates"][scopeId]["descendantDepth"]?.Type == JTokenType.Integer;
                    Change(sameObservedScope ? x == null ? "object_added_in_scope" : "object_removed_in_scope" : "not_collected", null, x?["state"], y?["state"]); continue;
                }
                if ((string)x["state"] != "collected" || (string)y["state"] != "collected") { Change((string)x["state"] == "collected" && (string)y["state"] == "object_missing" ? "object_removed_in_scope" : (string)x["state"] == "object_missing" && (string)y["state"] == "collected" ? "object_added_in_scope" : "incomparable", null, x["state"], y["state"]); continue; }
                var xf = ((JArray)x["fields"]).OfType<JObject>().ToDictionary(f => (string)f["propertyPath"]); var yf = ((JArray)y["fields"]).OfType<JObject>().ToDictionary(f => (string)f["propertyPath"]);
                foreach (var p in xf.Keys.Union(yf.Keys)) {
                    xf.TryGetValue(p, out var f); yf.TryGetValue(p, out var g);
                    if (f == null || g == null) { Change("not_collected", p, f, g); continue; }
                    if ((string)f["state"] == "inaccessible" || (string)g["state"] == "inaccessible" || (string)f["state"] == "unsupported" || (string)g["state"] == "unsupported") { Change("incomparable", p, f, g); continue; }
                    // Editing metadata is not an observed value (Edit -> Play changes editable).
                    var fv = (JObject)f.DeepClone(); var gv = (JObject)g.DeepClone();
                    fv.Remove("editable"); gv.Remove("editable");
                    if (JToken.DeepEquals(fv, gv)) continue;
                    string kind = (string)f["state"] == "inaccessible" || (string)g["state"] == "inaccessible" || (string)f["state"] == "unsupported" || (string)g["state"] == "unsupported" ? "incomparable" : f["arraySize"] != null || g["arraySize"] != null ? "collection_structure_changed" : (string)f["propertyType"] == "ObjectReference" || (string)f["propertyType"] == "ManagedReference" ? "reference_changed" : "value_changed";
                    Change(kind, p, f, g);
                }
            }
            var result = Objects.Page(changes, args, (string)a["id"] + (string)b["id"]); result["a"] = a["id"]; result["b"] = b["id"]; result["causation"] = "not_inferred"; result["matching"] = "exact ObjectRefs only; explicit target membership only"; return result;
        }
    }
}
