using System;
using System.IO;
using System.Linq;
using System.Text;
using System.Text.RegularExpressions;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
namespace EvidenceFirst.UnityBridge
{
    // Private per-project immutable evidence. Explicit release only; no automatic eviction.
    internal sealed class EvidenceStore
    {
        readonly string kind;
        internal EvidenceStore(string kind) { this.kind = kind; }
        string DirectoryPath { get { var p = Path.Combine(Bridge.StateDirectory, kind); PathSafety.CheckInternal(p); Directory.CreateDirectory(p); PathSafety.Private(p, true); return p; } }
        string FilePath(string id) { if (id == null || !Regex.IsMatch(id, "^[a-f0-9]{32}$")) throw new BridgeException("invalid_evidence_id", "Invalid retained evidence ID"); var p = Path.Combine(DirectoryPath, id + ".json"); PathSafety.CheckInternal(p); return p; }
        internal string Put(JObject data)
        {
            string id = Guid.NewGuid().ToString("N"); data["id"] = id; string text = data.ToString(Formatting.None);
            int size = Encoding.UTF8.GetByteCount(text);
            var files = new DirectoryInfo(DirectoryPath).GetFiles("*.json");
            if (size > 1048576 || files.Length >= 64 || files.Sum(f => f.Length) + size > 16 * 1048576) throw new BridgeException("evidence_capacity", "Limits: 1 MiB/item, 64 items, 16 MiB/store; explicitly release retained evidence");
            PathSafety.WritePrivate(FilePath(id), text); return id;
        }
        internal JObject Get(string id) { var p = FilePath(id); if (!File.Exists(p)) throw new BridgeException("evidence_not_found", "Unknown or released evidence"); if (new FileInfo(p).Length > 1048576) throw new BridgeException("evidence_corrupt", "Stored item exceeds bound"); var o = JObject.Parse(File.ReadAllText(p)); if ((string)o["projectIdentity"] != Bridge.ProjectId) throw new BridgeException("evidence_binding", "Wrong project"); return o; }
        internal JObject Release(string id) { Get(id); File.Delete(FilePath(id)); return new JObject { ["status"] = "released", ["id"] = id, ["recoverable"] = false }; }
    }
}
