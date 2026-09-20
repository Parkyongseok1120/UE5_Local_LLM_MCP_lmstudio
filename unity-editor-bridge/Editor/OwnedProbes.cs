using System;
using System.Collections.Generic;
using System.Linq;
using UnityEditor;
using UnityEngine;
namespace EvidenceFirst.UnityBridge
{
    // Count only our explicit allocations, including inactive/DontSave components.
    // Editor update runs while Play is paused; cleanup never scans/deletes user objects.
    [InitializeOnLoad]
    public static class OwnedProbes
    {
        static readonly Dictionary<Component, double> Entries = new Dictionary<Component, double>();
        static OwnedProbes() {
            EditorApplication.update += Tick;
            AssemblyReloadEvents.beforeAssemblyReload += Clear;
            EditorApplication.quitting += Clear;
            EditorApplication.playModeStateChanged += s => { if (s == PlayModeStateChange.ExitingPlayMode || s == PlayModeStateChange.EnteredEditMode) Clear(); };
        }
        public static T Add<T>(GameObject target, double durationMs) where T : Component {
            Tick();
            if (!EditorApplication.isPlaying || target == null || EditorUtility.IsPersistent(target)) throw new BridgeException("runtime_target_required", "Only a live runtime target can be observed");
            if (durationMs < 1 || durationMs > 60000) throw new BridgeException("invalid_bounds", "Probe duration must be 1..60000 ms");
            if (Entries.Keys.Count(p => p is T) >= 16 || Entries.Count >= 48) throw new BridgeException("probe_capacity", "Maximum 16 owned probes per adapter, 48 total");
            var probe = target.AddComponent<T>(); probe.hideFlags = HideFlags.DontSave;
            Entries.Add(probe, EditorApplication.timeSinceStartup + durationMs / 1000); return probe;
        }
        static void Tick() {
            foreach (var pair in Entries.ToArray()) if (pair.Key == null || !EditorApplication.isPlaying || EditorApplication.timeSinceStartup >= pair.Value) {
                Entries.Remove(pair.Key); if (pair.Key != null) UnityEngine.Object.DestroyImmediate(pair.Key);
            }
        }
        static void Clear() { foreach (var component in Entries.Keys.ToArray()) if (component != null) UnityEngine.Object.DestroyImmediate(component); Entries.Clear(); }
    }
}
