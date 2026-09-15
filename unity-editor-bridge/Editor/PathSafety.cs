using System;
using System.IO;
using System.Runtime.InteropServices;

namespace EvidenceFirst.UnityBridge
{
    internal static class PathSafety
    {
        [DllImport("libc", SetLastError = true)] static extern int chmod(string path, uint mode);
        [DllImport("libc", SetLastError = true)] static extern IntPtr realpath(string path, IntPtr buffer);
        [DllImport("libc")] static extern void free(IntPtr pointer);
        internal static string Identity(string value) => Path.DirectorySeparatorChar == '\\' ? value.Replace('\\', '/').ToLowerInvariant() : value;
        internal static string Canonical(string value)
        {
            var full = Path.GetFullPath(value);
            if (Path.DirectorySeparatorChar == '\\') { RejectLinks(full); return full; }
            var pointer = realpath(full, IntPtr.Zero);
            if (pointer == IntPtr.Zero) throw new BridgeException("invalid_path", "Cannot canonicalize project root");
            try { return Marshal.PtrToStringAnsi(pointer); } finally { free(pointer); }
        }
        internal static void RejectLinks(string full)
        {
            var current = Path.GetPathRoot(full);
            foreach (var part in full.Substring(current.Length).Split(Path.DirectorySeparatorChar))
            {
                current = Path.Combine(current, part);
                if (File.Exists(current) || Directory.Exists(current))
                    if ((File.GetAttributes(current) & FileAttributes.ReparsePoint) != 0) throw new BridgeException("symlink_denied", "Symlink/reparse paths are forbidden");
            }
        }
        internal static void CheckInternal(string full)
        {
            // Check only below the already canonical root (macOS /var can itself be an OS symlink).
            var relative = Path.GetRelativePath(Bridge.Root, full);
            if (relative.StartsWith("..") || Path.IsPathRooted(relative)) throw new BridgeException("path_denied", "Internal path escapes project");
            var current = Bridge.Root;
            foreach (var part in relative.Split(Path.DirectorySeparatorChar))
            {
                current = Path.Combine(current, part);
                if (File.Exists(current) || Directory.Exists(current)) if ((File.GetAttributes(current) & FileAttributes.ReparsePoint) != 0) throw new BridgeException("symlink_denied", "State path is a link");
            }
        }
        internal static string Asset(string relative, string extension = null)
        {
            if (String.IsNullOrEmpty(relative) || !relative.StartsWith("Assets/", StringComparison.Ordinal) || relative.Contains("\\") || relative.Contains(":")) throw new BridgeException("path_denied", "Explicit Assets-relative path required");
            foreach (var part in relative.Split('/')) if (part.Length == 0 || part.StartsWith(".")) throw new BridgeException("path_denied", "Hidden/traversal segments are forbidden");
            if (extension != null && !relative.EndsWith(extension, StringComparison.OrdinalIgnoreCase)) throw new BridgeException("path_denied", "Wrong asset extension");
            if (relative.EndsWith(".meta", StringComparison.OrdinalIgnoreCase)) throw new BridgeException("path_denied", "Meta files are not writable");
            var full = Path.Combine(Bridge.Root, relative);
            CheckInternal(full);
            if (!Directory.Exists(Path.GetDirectoryName(full))) throw new BridgeException("missing_parent", "Parent directory must already exist");
            return full;
        }
        internal static void Private(string full, bool directory = false)
        {
            if (Path.DirectorySeparatorChar != '\\' && chmod(full, directory ? 448u : 384u) != 0) throw new IOException("Could not restrict Bridge state permissions");
        }
        internal static void WritePrivate(string file, string value)
        {
            CheckInternal(file);
            var temporary = file + "." + Guid.NewGuid().ToString("N") + ".tmp";
            try
            {
                using (var stream = new FileStream(temporary, FileMode.CreateNew, FileAccess.Write, FileShare.None))
                {
                    Private(temporary);
                    var bytes = System.Text.Encoding.UTF8.GetBytes(value); stream.Write(bytes, 0, bytes.Length); stream.Flush(true);
                }
                if (File.Exists(file)) File.Replace(temporary, file, null); else File.Move(temporary, file);
            }
            finally { if (File.Exists(temporary)) File.Delete(temporary); }
        }
    }
}
