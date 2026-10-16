"""Compile/run the pure Bridge response contract without starting an Editor."""
import os
import re
import shutil
import subprocess
from pathlib import Path
from xml.sax.saxutils import escape

import pytest

ROOT = Path(__file__).resolve().parents[1]


def compile_contract(tmp_path, source_names, program):
    dotnet = shutil.which("dotnet")
    if not dotnet:
        pytest.skip(".NET SDK unavailable; Editor integration remains a separate gate")
    installed = subprocess.check_output([dotnet, "--list-sdks"], text=True, timeout=30)
    sdks = re.findall(r"^(\d+\.[^ ]+) \[(.+)\]$", installed, re.MULTILINE)
    if not sdks:
        pytest.skip(".NET SDK unavailable")
    version, base = sdks[-1]
    json_dll = Path(base) / version / "Newtonsoft.Json.dll"
    if not json_dll.is_file():
        pytest.skip("SDK does not provide Newtonsoft.Json for the isolated contract test")
    sources = "".join(f'<Compile Include="{escape(str(ROOT / "unity-editor-bridge/Editor" / name))}" Link="{name}" />'
                      for name in source_names)
    (tmp_path / "Contract.csproj").write_text(f"""<Project Sdk="Microsoft.NET.Sdk">
<PropertyGroup><OutputType>Exe</OutputType><TargetFramework>net{version.split('.')[0]}.0</TargetFramework>
<ImplicitUsings>disable</ImplicitUsings><Nullable>disable</Nullable></PropertyGroup>
<ItemGroup>{sources}
<Reference Include="Newtonsoft.Json"><HintPath>{escape(str(json_dll))}</HintPath></Reference></ItemGroup></Project>""", encoding="utf-8")
    (tmp_path / "NuGet.Config").write_text('<configuration><packageSources><clear /></packageSources></configuration>', encoding="utf-8")
    (tmp_path / "Program.cs").write_text(program, encoding="utf-8")
    env = {**os.environ, "DOTNET_CLI_HOME": str(tmp_path / "dotnet"), "NUGET_PACKAGES": str(tmp_path / "packages"),
           "DOTNET_SKIP_FIRST_TIME_EXPERIENCE": "1", "DOTNET_CLI_TELEMETRY_OPTOUT": "1",
           "DOTNET_GENERATE_ASPNET_CERTIFICATE": "false", "DOTNET_NOLOGO": "1"}
    result = subprocess.run([dotnet, "run", "--project", str(tmp_path / "Contract.csproj"), "--configuration", "Release"],
        cwd=tmp_path, env=env, text=True, capture_output=True, encoding="utf-8", timeout=90)
    assert result.returncode == 0, result.stdout + result.stderr
    return result.stdout


def test_recorded_origin_survives_new_delivery_and_legacy_origin_stays_unknown(tmp_path):
    output = compile_contract(tmp_path, ["ResponseMetadata.cs"], """using System;
using Newtonsoft.Json.Linq;
using EvidenceFirst.UnityBridge;
class Program {
 static void Check(bool ok) { if (!ok) throw new Exception("origin contract failed"); }
 static void Main() {
  var current = JObject.Parse("{ 'editorSessionId':'new', 'domainGeneration':9, 'observedAt':'now' }");
  var old = JObject.Parse("{ 'editorSessionId':'old', 'domainGeneration':1, 'observedAt':'then', 'status':'stored' }");
  var delivered = ResponseMetadata.Deliver(old, "request", current);
  Check((string)delivered["editorSessionId"] == "old" && (int)delivered["domainGeneration"] == 1);
  Check((string)delivered["observedAt"] == "then" && (string)delivered["delivery"]["editorSessionId"] == "new");
  Check(old["delivery"] == null && old["origin"] == null);
  var legacy = JObject.Parse("{ 'status':'applied' }"); legacy["origin"] = ResponseMetadata.Origin(legacy);
  var unknown = ResponseMetadata.Deliver(legacy, "other", current);
  Check((string)unknown["origin"]["state"] == "unknown" && unknown["editorSessionId"] == null);
  var fresh = ResponseMetadata.Deliver(new JObject(), "fresh", current);
  Check((string)fresh["editorSessionId"] == "new");
  var again = ResponseMetadata.Deliver(delivered, "again", current);
  Check(JToken.DeepEquals(again["origin"], delivered["origin"]));
  Console.WriteLine("origin-contract-passed");
 }
}""")
    assert "origin-contract-passed" in output


def test_recording_state_is_truthful_when_metadata_persistence_fails(tmp_path):
    # Actual Snapshots owner; only unavailable Editor/IO boundaries are replaced.
    output = compile_contract(tmp_path, ["ResponseMetadata.cs", "Snapshots.cs"], """using System;
using System.Collections.Generic;
using Newtonsoft.Json.Linq;
using EvidenceFirst.UnityBridge;
namespace UnityEditor { public static class EditorApplication {
 public static double timeSinceStartup; public static bool isCompiling, isPaused;
} }
namespace UnityEngine {
 public static class Time { public static int frameCount; }
 public class GameObject { public Transform transform; }
 public class Transform { public int childCount; public GameObject gameObject; public Transform GetChild(int i) => null; }
}
namespace EvidenceFirst.UnityBridge {
 internal class BridgeException : Exception { internal string Code; internal BridgeException(string code, string text) : base(text) { Code = code; } }
 internal static class Bridge {
  internal static string StateDirectory = ".", PlaySession = "play", Session = "editor", ProjectId = "project", Version = "test";
  internal static int Generation = 1;
  internal static string Digest(string text) => text;
  internal static JObject CurrentOrigin() => JObject.Parse("{ 'editorSessionId':'editor','domainGeneration':1,'observedAt':'before' }");
 }
 internal static class PathSafety {
  internal static bool Fail;
  internal static void CheckInternal(string path) {}
  internal static void WritePrivate(string path, string text) { if (Fail) throw new System.IO.IOException("fixture failure"); }
 }
 internal sealed class EvidenceStore {
  internal EvidenceStore(string kind) {}
  internal string Put(JObject data) => "sample";
  internal JObject Get(string id) => new JObject();
  internal JObject Release(string id) => new JObject();
 }
 internal static class Objects {
  internal static JObject Page(List<JObject> rows, JObject args, string digest) => new JObject();
  internal static object Resolve(JToken value) => null;
  internal static JObject Ref(object value) => new JObject();
  internal static JObject Read(JObject args, bool flag) => new JObject { ["items"] = new JArray(), ["observedFrame"] = 0 };
 }
 internal static class Observations { internal static string CompilationId = "compile"; }
}
class Program {
 static void Check(bool ok) { if (!ok) throw new Exception("recording persistence contract failed"); }
 static JObject Start() => JObject.Parse("{ 'action':'record_start','maxDurationMs':1000,'maxFrames':10,'intervalMs':50,'maxSamples':2,'maxBytes':4096,'targets':[{'target':{},'propertyPaths':['value']}] }");
 static JObject Status(JObject value, string action) => Snapshots.Call(new JObject { ["action"] = action, ["recordingId"] = value["recordingId"] });
 static void Main() {
  PathSafety.Fail = true;
  var failed = Snapshots.Call(Start());
  Check((string)failed["status"] == "stopped" && (string)failed["metadataPersistence"]["status"] == "unavailable");
  Check((string)failed["origin"]["editorSessionId"] == "editor" && (int)failed["sampleCount"] == 0);
  Snapshots.Tick(); Check((int)Status(failed,"record_status")["sampleCount"] == 0);
  PathSafety.Fail = false; var running = Snapshots.Call(Start()); Check((string)running["status"] == "recording");
  PathSafety.Fail = true; var stopped = Status(running,"record_stop");
  Check((string)stopped["status"] == "stopped" && stopped["metadataPersistence"] != null);
  PathSafety.Fail = false; Check(Status(stopped,"record_status")["metadataPersistence"] == null);
  running = Snapshots.Call(Start()); PathSafety.Fail = true; UnityEditor.EditorApplication.timeSinceStartup = 2;
  Snapshots.Tick(); var limited = Status(running,"record_status");
  Check((string)limited["status"] == "stopped" && (string)limited["reason"] == "configured_limit");
  Console.WriteLine("recording-contract-passed");
 }
}""")
    assert "recording-contract-passed" in output
