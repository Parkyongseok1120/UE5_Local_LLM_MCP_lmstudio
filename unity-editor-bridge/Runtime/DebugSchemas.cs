using Newtonsoft.Json.Linq;
namespace EvidenceFirst.Debugging
{
    public static class DebugSchemas
    {
        public static JObject String(int max = 4096) => new JObject { ["type"] = "string", ["maxLength"] = max };
        public static JObject Number(double min = -1e15, double max = 1e15) => new JObject { ["type"] = "number", ["minimum"] = min, ["maximum"] = max };
        public static JObject Bool() => new JObject { ["type"] = "boolean" };
        public static JObject Object(JObject properties, params string[] required) => new JObject { ["type"] = "object", ["properties"] = properties, ["required"] = new JArray(required), ["additionalProperties"] = false };
        public static JObject Target() => Object(new JObject {
            ["kind"] = String(16), ["projectIdentity"] = String(64), ["guid"] = String(64), ["localFileId"] = String(64),
            ["globalObjectId"] = String(), ["scenePath"] = String(), ["handle"] = String(64), ["editorSessionId"] = String(64),
            ["domainGeneration"] = Number(1, int.MaxValue), ["playSessionId"] = String(64) }, "kind", "projectIdentity");
        public static JObject TargetInput() => Object(new JObject { ["target"] = Target() }, "target");
    }
}
