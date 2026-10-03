using Newtonsoft.Json.Linq;

namespace EvidenceFirst.UnityBridge
{
    // Evidence origin belongs to its producer. Delivery belongs to the RPC.
    // This helper only projects recorded fields; missing historical fields stay unknown.
    internal static class ResponseMetadata
    {
        static readonly string[] OriginFields = {
            "projectIdentity", "canonicalProjectRoot", "editorSessionId", "domainGeneration",
            "playSessionId", "observedAt"
        };

        internal static JObject Origin(JObject value)
        {
            if (value["origin"] is JObject origin) return (JObject)origin.DeepClone();
            var result = new JObject { ["schemaVersion"] = 1, ["state"] = "unknown" };
            foreach (string key in OriginFields)
                if (value[key] != null) result[key] = value[key].DeepClone();
            if (result["domainGeneration"] == null && value["domainGenerationAtRequest"] != null)
                result["domainGeneration"] = value["domainGenerationAtRequest"].DeepClone();
            if (result["observedAt"] == null && value["endedAt"] != null)
                result["observedAt"] = value["endedAt"].DeepClone();
            if (result["editorSessionId"] != null && result["domainGeneration"] != null)
                result["state"] = "recorded";
            return result;
        }

        internal static JObject Deliver(JObject value, JToken requestId, JObject currentOrigin)
        {
            var result = (JObject)value.DeepClone();
            bool hasOrigin = value["origin"] != null;
            foreach (string key in OriginFields) hasOrigin |= value[key] != null;
            result["origin"] = hasOrigin ? Origin(value) : currentOrigin.DeepClone();
            foreach (string key in OriginFields)
                if (result[key] == null && result["origin"][key] != null)
                    result[key] = result["origin"][key].DeepClone();
            result["requestId"] = requestId?.DeepClone();
            result["delivery"] = new JObject {
                ["editorSessionId"] = currentOrigin["editorSessionId"]?.DeepClone(),
                ["domainGeneration"] = currentOrigin["domainGeneration"]?.DeepClone(),
                ["deliveredAt"] = currentOrigin["observedAt"]?.DeepClone()
            };
            return result;
        }
    }
}
