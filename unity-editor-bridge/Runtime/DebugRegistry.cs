using System;
using System.Collections.Generic;
using System.Linq;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using UnityEngine;

namespace EvidenceFirst.Debugging
{
    // Explicit registrations only. A callback is trusted project code; never discovered
    // via reflection. Queries must be side-effect-free by the registration contract.
    public static class DebugRegistry
    {
        sealed class Entry : IDisposable
        {
            internal string Id, Kind, Version;
            internal JObject Input, Output;
            internal Func<JObject, JObject> Callback;
            public void Dispose() { if (Entries.TryGetValue(Id, out var e) && ReferenceEquals(e, this)) Entries.Remove(Id); }
        }
        static readonly Dictionary<string, Entry> Entries = new Dictionary<string, Entry>();
        public static event Action<string, JObject, UnityEngine.Object, UnityEngine.Object, string> Observed;
        public static string CorrelationId { get; private set; }
        public static JObject EmptySchema => new JObject { ["type"] = "object", ["properties"] = new JObject(), ["additionalProperties"] = false };
        public static IDisposable Register(string id, string kind, string version, JObject input, JObject output, Func<JObject, JObject> callback)
        {
            if (String.IsNullOrEmpty(id) || id.Length > 160 || (kind != "query" && kind != "action") || Entries.ContainsKey(id) || Entries.Count >= 64 || callback == null) throw new ArgumentException("Invalid/duplicate registration or registry full");
            ValidateSchema(input, 0); ValidateSchema(output, 0);
            var e = new Entry { Id = id, Kind = kind, Version = version, Input = (JObject)input.DeepClone(), Output = (JObject)output.DeepClone(), Callback = callback };
            Entries.Add(id, e); return e;
        }
        public static JArray Catalog() => new JArray(Entries.Values.Select(e => new JObject { ["id"] = e.Id, ["kind"] = e.Kind, ["version"] = e.Version,
            ["permission"] = e.Kind == "action" ? "execute" : "observe", ["inputSchema"] = e.Input.DeepClone(), ["outputSchema"] = e.Output.DeepClone(), ["availability"] = "registered" }));
        public static JObject Invoke(string id, string kind, JObject input, string correlationId = null)
        {
            if (!Entries.TryGetValue(id, out var e) || e.Kind != kind) throw new ArgumentException("Adapter/kind not registered");
            Validate(input, e.Input, 0);
            string old = CorrelationId; CorrelationId = correlationId;
            try { var result = e.Callback((JObject)input.DeepClone()); Validate(result, e.Output, 0); return (JObject)result.DeepClone(); }
            finally { CorrelationId = old; }
        }
        public static void Emit(string kind, JObject data, UnityEngine.Object source = null, UnityEngine.Object target = null)
        {
            if (data == null || data.ToString(Formatting.None).Length > 8192 || kind == null || kind.Length > 160) return;
            Observed?.Invoke(kind, (JObject)data.DeepClone(), source, target, CorrelationId);
        }
        static readonly HashSet<string> Keywords = new HashSet<string> { "type", "properties", "required", "additionalProperties", "items", "maxItems", "maxLength", "minimum", "maximum", "enum" };
        static void ValidateSchema(JObject s, int depth)
        {
            if (s == null || depth > 8 || s.Properties().Any(p => !Keywords.Contains(p.Name))) throw new ArgumentException("Unsupported schema; only the documented bounded subset is accepted");
            var type = (string)s["type"];
            if (!new[] { "object", "array", "string", "number", "integer", "boolean", "null" }.Contains(type)) throw new ArgumentException("Schema requires a supported type");
            if (type == "object") {
                if (!(s["properties"] is JObject props) || (bool?)s["additionalProperties"] != false || props.Count > 64) throw new ArgumentException("Closed object schema required");
                foreach (var p in props.Properties()) ValidateSchema(p.Value as JObject, depth + 1);
                if (s["required"] is JArray req && req.Any(r => props[(string)r] == null)) throw new ArgumentException("Unknown required property");
            }
            if (type == "array") { if ((int?)s["maxItems"] == null || (int)s["maxItems"] < 0 || (int)s["maxItems"] > 200) throw new ArgumentException("Bounded array required"); ValidateSchema(s["items"] as JObject, depth + 1); }
            if (type == "string" && ((int?)s["maxLength"] == null || (int)s["maxLength"] < 0 || (int)s["maxLength"] > 8192)) throw new ArgumentException("Bounded string required");
        }
        static void Validate(JToken value, JObject s, int depth)
        {
            if (value == null || depth > 8 || value.ToString(Formatting.None).Length > 32768) throw new ArgumentException("Value missing or exceeds schema budget");
            string t = (string)s["type"];
            bool valid = t == "object" ? value is JObject : t == "array" ? value is JArray : t == "string" ? value.Type == JTokenType.String : t == "integer" ? value.Type == JTokenType.Integer : t == "number" ? value.Type == JTokenType.Integer || value.Type == JTokenType.Float : t == "boolean" ? value.Type == JTokenType.Boolean : value.Type == JTokenType.Null;
            if (!valid) throw new ArgumentException("Schema type mismatch");
            if (s["enum"] is JArray choices && !choices.Any(c => JToken.DeepEquals(c, value))) throw new ArgumentException("Schema enum mismatch");
            if (value is JObject obj) {
                var props = (JObject)s["properties"];
                foreach (var required in (s["required"] as JArray ?? new JArray())) if (obj[(string)required] == null) throw new ArgumentException("Required input missing: " + required);
                foreach (var p in obj.Properties()) { if (!(props[p.Name] is JObject child)) throw new ArgumentException("Undeclared field: " + p.Name); Validate(p.Value, child, depth + 1); }
            }
            if (value is JArray array) { if (array.Count > (int)s["maxItems"]) throw new ArgumentException("Array budget"); foreach (var item in array) Validate(item, (JObject)s["items"], depth + 1); }
            if (t == "string" && ((string)value).Length > (int)s["maxLength"]) throw new ArgumentException("String budget");
            if (t == "number" || t == "integer") { double n = (double)value; if (Double.IsNaN(n) || Double.IsInfinity(n) || s["minimum"] != null && n < (double)s["minimum"] || s["maximum"] != null && n > (double)s["maximum"]) throw new ArgumentException("Numeric bounds"); }
        }
    }
}
