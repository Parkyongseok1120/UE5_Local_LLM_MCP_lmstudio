using System;
using System.Collections.Concurrent;
using System.Collections.Generic;
using System.Linq;
using System.Text;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEditor.Compilation;
using UnityEngine;

namespace EvidenceFirst.UnityBridge
{
    internal static class Observations
    {
        internal static string CompilationId => SessionState.GetString("EvidenceFirst.CompilationId", "");
        static readonly ConcurrentQueue<JObject> Pending = new ConcurrentQueue<JObject>();
        static readonly List<JObject> Events = new List<JObject>();
        static long sequence;
        static int bytes, dropped;
        static string subscribedAt;
        static string Clip(string text, int length) => text == null ? "" : text.Length <= length ? text : text.Substring(0, length);
        internal static void Start()
        {
            Stop();
            Events.Clear(); bytes = 0;
            var stored = SessionState.GetString("EvidenceFirst.CompilerEvents", "[]");
            try { foreach (JObject row in JArray.Parse(stored)) Add(row); } catch { }
            sequence = SessionState.GetInt("EvidenceFirst.LogSequence", 0);
            subscribedAt = DateTime.UtcNow.ToString("O");
            Application.logMessageReceivedThreaded += Log;
            CompilationPipeline.compilationStarted += CompilationStarted;
            CompilationPipeline.assemblyCompilationFinished += AssemblyFinished;
            CompilationPipeline.compilationFinished += CompilationFinished;
        }
        internal static void Stop()
        {
            Application.logMessageReceivedThreaded -= Log;
            CompilationPipeline.compilationStarted -= CompilationStarted;
            CompilationPipeline.assemblyCompilationFinished -= AssemblyFinished;
            CompilationPipeline.compilationFinished -= CompilationFinished;
        }
        static void Log(string message, string stack, LogType kind)
        {
            // This callback can run outside the main thread: only bounded data enqueue.
            if (Pending.Count >= 200) { System.Threading.Interlocked.Increment(ref dropped); return; }
            Pending.Enqueue(new JObject { ["source"] = "console", ["severity"] = kind.ToString(), ["message"] = Clip(message, 4000),
                ["stack"] = Clip(stack, 4000), ["timestamp"] = DateTime.UtcNow.ToString("O"), ["truncated"] = (message?.Length ?? 0) > 4000 || (stack?.Length ?? 0) > 4000 });
        }
        static void Add(JObject row)
        {
            Events.Add(row); bytes += Encoding.UTF8.GetByteCount(row.ToString(Formatting.None));
            while (Events.Count > 500 || bytes > 262144)
            { bytes -= Encoding.UTF8.GetByteCount(Events[0].ToString(Formatting.None)); Events.RemoveAt(0); dropped++; }
        }
        internal static void Drain()
        {
            int count = 0;
            while (count++ < 100 && Pending.TryDequeue(out var row))
            {
                row["sequence"] = ++sequence; row["editorSessionId"] = Bridge.Session; row["domainGeneration"] = Bridge.Generation;
                row["compilationId"] = CompilationId; Add(row);
            }
            SessionState.SetInt("EvidenceFirst.LogSequence", (int)Math.Min(sequence, Int32.MaxValue));
        }
        static void CompilationStarted(object context)
        {
            SessionState.SetString("EvidenceFirst.CompilationId", Guid.NewGuid().ToString("N"));
            SessionState.SetBool("EvidenceFirst.CompileHadErrors", false);
            SessionState.SetString("EvidenceFirst.CompilationOutcome", "compiling");
        }
        static void AssemblyFinished(string assemblyPath, CompilerMessage[] messages)
        {
            foreach (var message in messages)
            {
                if (message.type == CompilerMessageType.Error) SessionState.SetBool("EvidenceFirst.CompileHadErrors", true);
                var row = new JObject { ["source"] = "compiler", ["severity"] = message.type.ToString(), ["message"] = Clip(message.message, 4000),
                    ["file"] = message.file, ["line"] = message.line, ["column"] = message.column, ["assembly"] = System.IO.Path.GetFileName(assemblyPath),
                    ["timestamp"] = DateTime.UtcNow.ToString("O"), ["sequence"] = ++sequence, ["compilationId"] = CompilationId,
                    ["editorSessionId"] = Bridge.Session, ["truncated"] = message.message.Length > 4000 };
                Add(row);
            }
            PersistCompiler();
        }
        static void CompilationFinished(object context)
        {
            SessionState.SetString("EvidenceFirst.CompilationOutcome", SessionState.GetBool("EvidenceFirst.CompileHadErrors", false) ? "failed" : "completed_without_observed_errors");
            PersistCompiler();
        }
        static void PersistCompiler()
        {
            SessionState.SetString("EvidenceFirst.CompilerEvents", new JArray(Events.Where(e => (string)e["source"] == "compiler")).ToString(Formatting.None));
            SessionState.SetInt("EvidenceFirst.LogSequence", (int)Math.Min(sequence, Int32.MaxValue));
        }
        internal static JObject Read(JObject args)
        {
            Drain();
            long after = (long?)args["afterSequence"] ?? 0;
            var rows = Events.Where(e => (long)e["sequence"] > after && (args["source"] == null || (string)e["source"] == (string)args["source"]) &&
                (args["compilationId"] == null || (string)e["compilationId"] == (string)args["compilationId"])).ToList();
            var revision = Bridge.Digest(new JArray(rows).ToString(Formatting.None) + Bridge.Session);
            var result = Objects.Page(rows, args, revision);
            result["collectionScope"] = "subscribed_events_only"; result["subscribedAt"] = subscribedAt; result["droppedCount"] = dropped;
            result["compilationId"] = CompilationId; result["compilationOutcome"] = SessionState.GetString("EvidenceFirst.CompilationOutcome", "unknown");
            result["sourceAssemblyVerification"] = "unknown"; return result;
        }
    }
}
