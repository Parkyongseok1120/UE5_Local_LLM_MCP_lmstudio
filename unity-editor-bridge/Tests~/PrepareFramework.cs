using System;
using System.IO;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;
using UnityEngine.SceneManagement;

namespace EvidenceFirst.Tests
{
    [InitializeOnLoad]
    public static class PrepareFramework
    {
        static PrepareFramework() { EditorApplication.update += Prepare; }
        static void Prepare() {
            string root = Path.GetDirectoryName(Application.dataPath);
            if (Environment.GetEnvironmentVariable("UNITY_TEST_RELEASE") != "1" || !Path.GetFileName(root).StartsWith("unity-bridge-test-")) { EditorApplication.update -= Prepare; return; }
            if (!File.Exists(Path.Combine(root, "debug-fixture-ready.json")) || EditorApplication.isCompiling || EditorApplication.isUpdating) return;
            EditorApplication.update -= Prepare;
            if (SessionState.GetBool("EvidenceFirst.FixtureScenesPrepared", false)) return;
            // Only this harness-created project's fixtures; never product code or user scenes.
            for (int i = 0; i < SceneManager.sceneCount; i++) {
                var scene = SceneManager.GetSceneAt(i);
                if (scene.isLoaded && !EditorSceneManager.IsPreviewScene(scene)) EditorSceneManager.SaveScene(scene, String.IsNullOrEmpty(scene.path) ? "Assets/FixtureScene" + i + ".unity" : scene.path);
            }
            SessionState.SetBool("EvidenceFirst.FixtureScenesPrepared", true);
            File.WriteAllText(Path.Combine(root, "framework-fixture-ready.json"), "{\"status\":\"ready\"}");
        }
    }
}
