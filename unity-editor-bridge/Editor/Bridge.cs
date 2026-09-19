using System;
using System.Collections.Concurrent;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Net;
using System.Net.Sockets;
using System.Security.Cryptography;
using System.Text;
using System.Threading;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEditor.Compilation;
using UnityEngine;

namespace EvidenceFirst.UnityBridge
{
    internal sealed class BridgeException : Exception
    {
        internal readonly string Code;
        internal BridgeException(string code, string message) : base(message) { Code = code; }
    }

    [InitializeOnLoad]
    public static class Bridge
    {
        public const string Version = "1.4.0-beta.1";
        internal static string Root, ProjectId, Session, PlaySession, StateDirectory;
        internal static int Generation;
        static string token;
        static TcpListener listener;
        static Thread listenerThread;
        static volatile bool stopping;
        static readonly ConcurrentQueue<Pending> Queue = new ConcurrentQueue<Pending>();
        static readonly HashSet<TcpClient> Connections = new HashSet<TcpClient>();
        static int clients;
        internal static bool EditAllowed => SessionState.GetBool("EvidenceFirst.Edit", false);
        internal static bool ExecuteAllowed => SessionState.GetBool("EvidenceFirst.Execute", false);
        sealed class Pending
        {
            internal JObject Request, Response;
            internal readonly ManualResetEventSlim Ready = new ManualResetEventSlim(false);
            internal volatile bool Expired;
        }
        static Bridge()
        {
            EditorApplication.delayCall += Start;
            AssemblyReloadEvents.beforeAssemblyReload += Stop;
            EditorApplication.quitting += Stop;
        }
        static void Start()
        {
            if (listener != null) return;
            try
            {
                Root = PathSafety.Canonical(Path.GetDirectoryName(Application.dataPath));
                ProjectId = Digest(PathSafety.Identity(Root));
                StateDirectory = Path.Combine(Root, "Library", "EvidenceFirst");
                PathSafety.CheckInternal(StateDirectory);
                Directory.CreateDirectory(StateDirectory);
                PathSafety.Private(StateDirectory, true);
                Session = SessionState.GetString("EvidenceFirst.Session", "");
                if (Session.Length == 0) { Session = Guid.NewGuid().ToString("N"); SessionState.SetString("EvidenceFirst.Session", Session); }
                Generation = SessionState.GetInt("EvidenceFirst.Generation", 0) + 1;
                SessionState.SetInt("EvidenceFirst.Generation", Generation);
                PlaySession = SessionState.GetString("EvidenceFirst.PlaySession", "");
                if (EditorApplication.isPlaying && PlaySession.Length == 0) NewPlaySession();
                token = RandomToken();
                stopping = false;
                listener = new TcpListener(IPAddress.Loopback, 0);
                listener.Start(16);
                EditorApplication.update -= Pump;
                EditorApplication.update += Pump;
                EditorApplication.playModeStateChanged -= PlayChanged;
                EditorApplication.playModeStateChanged += PlayChanged;
                Observations.Start();
                DebugAdapters.Start();
                Snapshots.Recover();
                Operations.Recover();
                var discovery = new JObject {
                    ["protocolVersion"] = 1, ["bridgeVersion"] = Version, ["projectIdentity"] = ProjectId,
                    ["canonicalProjectRoot"] = Root, ["editorSessionId"] = Session, ["domainGeneration"] = Generation,
                    ["processId"] = System.Diagnostics.Process.GetCurrentProcess().Id,
                    ["port"] = ((IPEndPoint)listener.LocalEndpoint).Port, ["token"] = token
                };
                PathSafety.WritePrivate(Path.Combine(StateDirectory, "bridge.json"), discovery.ToString(Formatting.None));
                listenerThread = new Thread(Accept) { IsBackground = true, Name = "EvidenceFirst RPC" };
                listenerThread.Start();
            }
            catch (Exception error) { Stop(); Debug.LogError("Evidence First Bridge unavailable: " + error.GetType().Name); }
        }
        static void Accept()
        {
            while (!stopping)
            {
                try
                {
                    var client = listener.AcceptTcpClient();
                    if (Interlocked.Increment(ref clients) > 16) { Interlocked.Decrement(ref clients); client.Close(); continue; }
                    lock (Connections) Connections.Add(client);
                    ThreadPool.QueueUserWorkItem(_ => Receive(client));
                }
                catch { if (stopping) return; }
            }
        }
        static void Receive(TcpClient client)
        {
            try
            {
                client.ReceiveTimeout = 5000; client.SendTimeout = 5000;
                using (client)
                using (var stream = client.GetStream())
                using (var bytes = new MemoryStream())
                {
                    while (true)
                    {
                        int b = stream.ReadByte();
                        if (b < 0) return;
                        if (b == 10) break;
                        bytes.WriteByte((byte)b);
                        if (bytes.Length > 131072) return;
                    }
                    JObject request;
                    using (var reader = new JsonTextReader(new StringReader(new UTF8Encoding(false, true).GetString(bytes.ToArray()))) { MaxDepth = 32, DateParseHandling = DateParseHandling.None })
                        request = JObject.Load(reader, new JsonLoadSettings { DuplicatePropertyNameHandling = DuplicatePropertyNameHandling.Error });
                    if (!TokenEquals((string)request["token"], token)) return;
                    if (Queue.Count >= 32) return;
                    var pending = new Pending { Request = request };
                    Queue.Enqueue(pending);
                    if (!pending.Ready.Wait(12000)) { pending.Expired = true; return; }
                    var response = Encoding.UTF8.GetBytes(pending.Response.ToString(Formatting.None) + "\n");
                    stream.Write(response, 0, response.Length);
                }
            }
            catch { /* Peer disconnect is not an instruction to retry an operation. */ }
            finally { lock (Connections) Connections.Remove(client); Interlocked.Decrement(ref clients); }
        }
        static void Pump()
        {
            Observations.Drain();
            Snapshots.Tick();
            for (int i = 0; i < 4 && Queue.TryDequeue(out var pending); i++)
            {
                if (pending.Expired) continue;
                var request = pending.Request;
                JObject result;
                try
                {
                    if ((int?)request["protocolVersion"] != 1 || (string)request["projectIdentity"] != ProjectId ||
                        PathSafety.Identity((string)request["canonicalProjectRoot"] ?? "") != PathSafety.Identity(Root) ||
                        (string)request["editorSessionId"] != Session || (int?)request["domainGeneration"] != Generation)
                        throw new BridgeException("binding_mismatch", "Project, Editor or domain changed");
                    if (!(request["args"] is JObject args)) throw new BridgeException("invalid_arguments", "args must be an object");
                    result = Operations.Dispatch((string)request["method"], args);
                }
                catch (Exception error) { result = Error(error); }
                result["requestId"] = request["requestId"];
                result["editorSessionId"] = Session;
                result["domainGeneration"] = Generation;
                result["observedAt"] = DateTime.UtcNow.ToString("O");
                if (Encoding.UTF8.GetByteCount(result.ToString(Formatting.None)) > 65000)
                    result = new JObject { ["status"] = request["args"]?["operationId"] != null ? "outcome_unknown" : "not_applied", ["errorCode"] = "response_budget_exceeded",
                        ["requestId"] = request["requestId"], ["editorSessionId"] = Session, ["operationId"] = request["args"]?["operationId"] };
                pending.Response = result;
                pending.Ready.Set();
            }
        }
        internal static JObject Error(Exception error, string status = "not_applied") => new JObject {
            ["status"] = status, ["errorCode"] = error is BridgeException known ? known.Code : "unity_error",
            ["message"] = error.Message.Length > 1000 ? error.Message.Substring(0, 1000) : error.Message
        };
        internal static JObject Status() => new JObject {
            ["status"] = "observed", ["connection"] = "connected", ["protocolVersion"] = 1, ["bridgeVersion"] = Version,
            ["projectIdentity"] = ProjectId, ["canonicalProjectRoot"] = Root, ["editorVersion"] = Application.unityVersion,
            ["editorSessionId"] = Session, ["domainGeneration"] = Generation, ["playSessionId"] = PlaySession,
            ["compilationId"] = Observations.CompilationId, ["compiling"] = EditorApplication.isCompiling,
            ["importing"] = EditorApplication.isUpdating, ["playing"] = EditorApplication.isPlaying, ["paused"] = EditorApplication.isPaused,
            ["sourceAssemblyVerification"] = "unknown", ["permissions"] = new JObject { ["observe"] = true, ["edit"] = EditAllowed, ["execute"] = ExecuteAllowed, ["destructive"] = "per_request_editor_approval" },
            ["capabilities"] = new JObject { ["objectRead"] = true, ["serializedPatch"] = true, ["sceneEdit"] = true, ["scriptableObjects"] = true,
                ["prefabRead"] = true, ["prefabSourceEdit"] = true, ["prefabApplyRevert"] = true, ["managedReferenceReplace"] = false,
                ["runtimeRead"] = true, ["runtimeWrite"] = false, ["testExecution"] = TestExecution.Available, ["debugExtensions"] = true, ["capture"] = false, ["snapshots"] = true, ["boundedRecording"] = true, ["references"] = true, ["compilationManifest"] = true,
                ["destructive"] = true, ["logs"] = true, ["scriptCompilation"] = true, ["operations"] = true }
        };
        static void NewPlaySession() { PlaySession = Guid.NewGuid().ToString("N"); SessionState.SetString("EvidenceFirst.PlaySession", PlaySession); Objects.ClearHandles(); }
        static void PlayChanged(PlayModeStateChange state)
        {
            if (state == PlayModeStateChange.ExitingEditMode) NewPlaySession();
            if (state == PlayModeStateChange.EnteredEditMode) { PlaySession = ""; SessionState.SetString("EvidenceFirst.PlaySession", ""); Objects.ClearHandles(); }
        }
        static void Stop()
        {
            stopping = true;
            EditorApplication.update -= Pump;
            EditorApplication.playModeStateChanged -= PlayChanged;
            Observations.Stop();
            DebugAdapters.Stop();
            try { listener?.Stop(); } catch { }
            listener = null;
            lock (Connections) foreach (var client in Connections.ToArray()) client.Close();
            while (Queue.TryDequeue(out var pending)) { pending.Expired = true; pending.Response = new JObject(); pending.Ready.Set(); }
            if (listenerThread != null && listenerThread.IsAlive) listenerThread.Join(200);
            if (StateDirectory != null)
            {
                try { var file = Path.Combine(StateDirectory, "bridge.json"); if (File.Exists(file) && (string)JObject.Parse(File.ReadAllText(file))["token"] == token) File.Delete(file); } catch { }
            }
        }
        internal static string Digest(string value) { using (var sha = SHA256.Create()) return BitConverter.ToString(sha.ComputeHash(Encoding.UTF8.GetBytes(value))).Replace("-", "").ToLowerInvariant(); }
        static string RandomToken() { var bytes = new byte[32]; using (var rng = RandomNumberGenerator.Create()) rng.GetBytes(bytes); return BitConverter.ToString(bytes).Replace("-", "").ToLowerInvariant(); }
        static bool TokenEquals(string a, string b) { if (a == null || b == null || a.Length != b.Length) return false; int diff = 0; for (int i = 0; i < a.Length; i++) diff |= a[i] ^ b[i]; return diff == 0; }
        [MenuItem("Tools/Evidence First/Allow Edit for this Editor session")]
        static void ToggleEdit() { SessionState.SetBool("EvidenceFirst.Edit", !EditAllowed); }
        [MenuItem("Tools/Evidence First/Allow Edit for this Editor session", true)]
        static bool EditMenu() { Menu.SetChecked("Tools/Evidence First/Allow Edit for this Editor session", EditAllowed); return true; }
        [MenuItem("Tools/Evidence First/Allow Execute for this Editor session")]
        static void ToggleExecute() { SessionState.SetBool("EvidenceFirst.Execute", !ExecuteAllowed); }
        [MenuItem("Tools/Evidence First/Allow Execute for this Editor session", true)]
        static bool ExecuteMenu() { Menu.SetChecked("Tools/Evidence First/Allow Execute for this Editor session", ExecuteAllowed); return true; }
    }
}
