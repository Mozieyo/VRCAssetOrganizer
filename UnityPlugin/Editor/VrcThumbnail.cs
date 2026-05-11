#if UNITY_EDITOR
using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using UnityEditor;
using UnityEngine;

/// <summary>
/// VRC Asset Organizer thumbnail generator and asset organizer.
///
/// Reads asset paths + genre metadata from JSON temp files written by the
/// desktop app, imports assets, and routes them to genre-specific folders.
/// </summary>
public static class VrcThumbnail
{
    private const string TempFilePattern = "vrc_thumb_*.txt";
    private const string SettingsPath = "ProjectSettings/VrcThumbnail.json";
    private const string ImportDir = "Assets/VrcImport";

    // Default genre → folder mapping (matched case-insensitively against asset tags)
    private static readonly GenreEntry[] DefaultGenres =
    {
        new GenreEntry { genre = "Avatar Base", folder = "Assets/1. Avatar Base" },
        new GenreEntry { genre = "Outfit & Acce", folder = "Assets/2. Outfit & Acce" },
        new GenreEntry { genre = "Gimmick", folder = "Assets/3. Gimmick" },
        new GenreEntry { genre = "Tools", folder = "Assets/4. Tools" },
    };

    private enum State { Idle, WaitingForImport, WaitingForPreview }

    private static readonly Queue<VrcThumbData> Queue = new Queue<VrcThumbData>();
    private static State _state = State.Idle;
    private static VrcThumbData _current;
    private static string _currentSourcePath;
    private static string _currentImportedPath;
    private static int _waitFrames;
    private static bool _isBatch;

    // ---------------------------------------------------------------
    // Public entry points (called via -executeMethod)
    // ---------------------------------------------------------------

    public static void ProcessSingle()
    {
        string tempFile = FindLatestTempFile();
        if (tempFile == null)
        {
            EditorUtility.DisplayDialog(
                "VRC Thumbnail",
                "No pending asset.\n\nDrop a file onto VRC Asset Organizer first, then try again.",
                "OK");
            return;
        }

        var data = ReadTempFile(tempFile);
        File.Delete(tempFile);

        if (data == null || string.IsNullOrEmpty(data.path))
        {
            EditorUtility.DisplayDialog("VRC Thumbnail", "Invalid temp file data.", "OK");
            return;
        }

        if (!File.Exists(data.path) && !Directory.Exists(data.path))
        {
            EditorUtility.DisplayDialog("VRC Thumbnail",
                $"Asset not found:\n{data.path}", "OK");
            return;
        }

        _isBatch = false;
        Queue.Enqueue(data);
        EditorApplication.update += Tick;
    }

    public static void ProcessAll()
    {
        string[] tempFiles = Directory.GetFiles(Path.GetTempPath(), TempFilePattern);
        if (tempFiles.Length == 0)
        {
            Debug.Log("[VrcThumbnail] No pending assets.");
            EditorApplication.Exit(0);
            return;
        }

        _isBatch = true;
        foreach (string file in tempFiles)
        {
            var data = ReadTempFile(file);
            File.Delete(file);
            if (data != null && !string.IsNullOrEmpty(data.path))
                Queue.Enqueue(data);
        }

        Debug.Log($"[VrcThumbnail] Processing {Queue.Count} asset(s) in batch...");
        EditorApplication.update += Tick;
    }

    // ---------------------------------------------------------------
    // Per-frame state machine
    // ---------------------------------------------------------------

    private static void Tick()
    {
        switch (_state)
        {
            case State.Idle:
                if (Queue.Count == 0)
                {
                    EditorApplication.update -= Tick;
                    CleanupImportFolder();
                    if (_isBatch)
                    {
                        Debug.Log("[VrcThumbnail] Batch complete.");
                        EditorApplication.Exit(0);
                    }
                    return;
                }
                _current = Queue.Dequeue();
                _currentSourcePath = _current.path;
                ImportAsset(_currentSourcePath, _current);
                break;

            case State.WaitingForImport:
                _waitFrames++;
                if (_waitFrames < 2) return;

                _currentImportedPath = FindImportedAssetPath(_currentSourcePath);
                if (_currentImportedPath == null)
                {
                    Debug.LogWarning($"[VrcThumbnail] Import did not produce a loadable asset: {_currentSourcePath}");
                    _state = State.Idle;
                    return;
                }

                var obj = AssetDatabase.LoadAssetAtPath<UnityEngine.Object>(_currentImportedPath);
                if (obj == null)
                {
                    Debug.LogWarning($"[VrcThumbnail] Could not load asset at: {_currentImportedPath}");
                    _state = State.Idle;
                    return;
                }

                AssetPreview.GetAssetPreview(obj);
                _waitFrames = 0;
                _state = State.WaitingForPreview;
                break;

            case State.WaitingForPreview:
                _waitFrames++;
                var asset = AssetDatabase.LoadAssetAtPath<UnityEngine.Object>(_currentImportedPath);
                if (asset == null) { _state = State.Idle; return; }

                var thumb = AssetPreview.GetAssetPreview(asset);
                if (thumb != null)
                {
                    SaveReadableThumbnail(thumb, _currentSourcePath);
                    if (!_isBatch)
                        EditorGUIUtility.PingObject(asset);
                    _state = State.Idle;
                }
                else if (_waitFrames > 120)
                {
                    Debug.LogWarning($"[VrcThumbnail] Thumbnail timed out for: {_currentSourcePath}");
                    _state = State.Idle;
                }
                break;
        }
    }

    // ---------------------------------------------------------------
    // Asset import + genre routing
    // ---------------------------------------------------------------

    private static void ImportAsset(string sourcePath, VrcThumbData data)
    {
        string ext = Path.GetExtension(sourcePath).ToLowerInvariant();

        if (ext == ".unitypackage")
        {
            ImportPackageToGenreFolder(sourcePath, data);
            return;
        }

        // Non-package file: copy directly to genre folder
        string targetDir = ResolveGenreFolder(data);
        EnsureAssetFolder(targetDir);

        string fileName = Path.GetFileName(sourcePath);
        string destPath = AssetDatabase.GenerateUniqueAssetPath($"{targetDir}/{fileName}");

        try
        {
            File.Copy(sourcePath, destPath, overwrite: false);
        }
        catch (Exception e)
        {
            Debug.LogError($"[VrcThumbnail] Copy failed: {e.Message}");
            _state = State.Idle;
            return;
        }

        AssetDatabase.Refresh();
        _waitFrames = 0;
        _state = State.WaitingForImport;
    }

    /// <summary>
    /// Import a .unitypackage, then move everything it created into the
    /// genre folder.  Unity 2022 does not support importing to a target
    /// folder, so we snapshot GUIDs before/after and relocate.
    /// </summary>
    private static void ImportPackageToGenreFolder(string packagePath, VrcThumbData data)
    {
        string targetDir = ResolveGenreFolder(data);
        EnsureAssetFolder(targetDir);

        // Snapshot before import
        var before = new HashSet<string>(AssetDatabase.GetAllAssetPaths());

        AssetDatabase.ImportPackage(packagePath, interactive: false);

        // Snapshot after import — find what's new
        var after = new HashSet<string>(AssetDatabase.GetAllAssetPaths());
        after.ExceptWith(before);

        // Only care about paths under Assets/ (ignore Packages/, ProjectSettings/ etc.)
        var newPaths = after.Where(p => p.StartsWith("Assets/")).ToList();
        if (newPaths.Count == 0)
        {
            Debug.LogWarning($"[VrcThumbnail] Package imported nothing detectable: {packagePath}");
            _state = State.Idle;
            return;
        }

        // Find the common root of all imported paths
        string commonRoot = FindCommonRoot(newPaths);
        bool isSingleRoot = commonRoot != "Assets" && newPaths.All(p => p == commonRoot || p.StartsWith(commonRoot + "/"));

        string movedRoot;
        if (isSingleRoot)
        {
            // Move the single root folder into the target
            string folderName = Path.GetFileName(commonRoot);
            string dest = AssetDatabase.GenerateUniqueAssetPath($"{targetDir}/{folderName}");
            MoveAssetWithParents(commonRoot, dest);
            movedRoot = dest;
        }
        else
        {
            // Multiple disconnected roots — move each under target
            foreach (string p in newPaths)
            {
                string relative = p.Substring("Assets/".Length);
                string dest = AssetDatabase.GenerateUniqueAssetPath($"{targetDir}/{relative}");
                MoveAssetWithParents(p, dest);
            }
            movedRoot = targetDir;
        }

        AssetDatabase.Refresh();

        // Flatten single-child folder chains (e.g. "PackName/PackName/Contents" → "PackName/Contents")
        FlattenSingleChildFolders(targetDir);

        AssetDatabase.Refresh();

        // Select the imported folder in the project window
        var folderObj = AssetDatabase.LoadAssetAtPath<UnityEngine.Object>(movedRoot);
        if (folderObj != null)
        {
            EditorGUIUtility.PingObject(folderObj);
        }

        _currentSourcePath = packagePath;
        _state = State.Idle;
        Debug.Log($"[VrcThumbnail] Imported to: {movedRoot}");
    }

    // ---------------------------------------------------------------
    // Folder flattening
    // ---------------------------------------------------------------

    /// <summary>
    /// Recursively merge folders that contain exactly one child folder and
    /// no files.  This cleans up the "folder-in-folder" packaging common
    /// with marketplace assets.
    /// </summary>
    private static void FlattenSingleChildFolders(string rootPath)
    {
        if (!AssetDatabase.IsValidFolder(rootPath))
            return;

        string[] subDirs = AssetDatabase.GetSubFolders(rootPath);
        foreach (string sub in subDirs)
            FlattenSingleChildFolders(sub);

        // Re-check after recursion
        subDirs = AssetDatabase.GetSubFolders(rootPath);
        string[] files = Directory.GetFiles(rootPath)
            .Select(f => f.Replace('\\', '/'))
            .Where(f => !f.EndsWith(".meta"))
            .ToArray();

        // If this folder has exactly one child folder and no files, merge up
        if (files.Length == 0 && subDirs.Length == 1)
        {
            string childFolder = subDirs[0];
            string parentFolder = Path.GetDirectoryName(rootPath.Replace('/', Path.DirectorySeparatorChar))
                                    ?.Replace(Path.DirectorySeparatorChar, '/');

            if (string.IsNullOrEmpty(parentFolder) || parentFolder == rootPath)
                return;

            string childName = Path.GetFileName(childFolder);
            string[] grandChildren = AssetDatabase.GetSubFolders(childFolder);
            string[] childFiles = Directory.GetFiles(childFolder)
                .Select(f => f.Replace('\\', '/'))
                .Where(f => !f.EndsWith(".meta"))
                .ToArray();

            // Move child's contents to parent folder
            foreach (string gc in grandChildren)
            {
                string gcName = Path.GetFileName(gc);
                string dest = AssetDatabase.GenerateUniqueAssetPath($"{parentFolder}/{gcName}");
                AssetDatabase.MoveAsset(gc, dest);
            }
            foreach (string cf in childFiles)
            {
                string cfName = Path.GetFileName(cf);
                string dest = AssetDatabase.GenerateUniqueAssetPath($"{parentFolder}/{cfName}");
                AssetDatabase.MoveAsset(cf, dest);
            }

            // Delete the now-empty child folder
            AssetDatabase.DeleteAsset(childFolder);

            // If the rootPath is now empty (everything moved to parent), delete it too
            string[] remaining = AssetDatabase.GetSubFolders(rootPath);
            string[] remainingFiles = Directory.GetFiles(rootPath)
                .Select(f => f.Replace('\\', '/'))
                .Where(f => !f.EndsWith(".meta"))
                .ToArray();

            if (remaining.Length == 0 && remainingFiles.Length == 0)
            {
                AssetDatabase.DeleteAsset(rootPath);
            }
        }
    }

    // ---------------------------------------------------------------
    // Genre resolution
    // ---------------------------------------------------------------

    /// <summary>
    /// Determine the target asset folder from tags + filetype.
    /// Tags are matched case-insensitively against configured genre names.
    /// Falls back to the configured default folder.
    /// </summary>
    private static string ResolveGenreFolder(VrcThumbData data)
    {
        var settings = LoadSettings();
        var genres = settings.genreFolders ?? DefaultGenres;

        // Match asset tags against genre names (case-insensitive)
        if (data.tags != null)
        {
            foreach (string tag in data.tags)
            {
                foreach (var entry in genres)
                {
                    if (string.IsNullOrEmpty(entry.genre) || string.IsNullOrEmpty(entry.folder))
                        continue;
                    if (string.Equals(tag, entry.genre, StringComparison.OrdinalIgnoreCase))
                        return entry.folder;
                }
            }
        }

        // Filetype-based fallback guess
        string ft = data.filetype ?? "";
        if (ft == "asset_zip" || ft == "unitypackage")
        {
            foreach (var entry in genres)
            {
                if (entry.genre == "Avatar Base")
                    return entry.folder;
            }
        }

        return settings.defaultFolder ?? "Assets/Imported";
    }

    // ---------------------------------------------------------------
    // Settings persistence
    // ---------------------------------------------------------------

    private static VrcThumbnailSettings LoadSettings()
    {
        try
        {
            if (File.Exists(SettingsPath))
            {
                string json = File.ReadAllText(SettingsPath);
                var settings = JsonUtility.FromJson<VrcThumbnailSettings>(json);
                if (settings != null)
                    return settings;
            }
        }
        catch (Exception e)
        {
            Debug.LogWarning($"[VrcThumbnail] Failed to load settings: {e.Message}");
        }

        return new VrcThumbnailSettings
        {
            genreFolders = DefaultGenres,
            defaultFolder = "Assets/Imported",
        };
    }

    public static void SaveSettings(VrcThumbnailSettings settings)
    {
        try
        {
            string dir = Path.GetDirectoryName(SettingsPath);
            if (!Directory.Exists(dir))
                Directory.CreateDirectory(dir);
            string json = JsonUtility.ToJson(settings, prettyPrint: true);
            File.WriteAllText(SettingsPath, json);
            Debug.Log($"[VrcThumbnail] Settings saved to {SettingsPath}");
        }
        catch (Exception e)
        {
            Debug.LogError($"[VrcThumbnail] Failed to save settings: {e.Message}");
        }
    }

    public static VrcThumbnailSettings GetSettings()
    {
        return LoadSettings();
    }

    // ---------------------------------------------------------------
    // Helpers
    // ---------------------------------------------------------------

    private static VrcThumbData ReadTempFile(string path)
    {
        try
        {
            string json = File.ReadAllText(path);
            return JsonUtility.FromJson<VrcThumbData>(json);
        }
        catch (Exception e)
        {
            Debug.LogWarning($"[VrcThumbnail] Failed to read temp file {path}: {e.Message}");
            // Fallback: treat contents as a plain path
            try
            {
                string plain = File.ReadAllText(path).Trim();
                if (!string.IsNullOrEmpty(plain))
                    return new VrcThumbData { path = plain, tags = new string[0], filetype = "" };
            }
            catch { }
            return null;
        }
    }

    private static string FindCommonRoot(List<string> paths)
    {
        if (paths.Count == 0) return "Assets";
        if (paths.Count == 1) return paths[0];

        string[] segments = paths[0].Split('/');
        int commonLen = segments.Length;

        for (int i = 1; i < paths.Count; i++)
        {
            string[] other = paths[i].Split('/');
            int j = 0;
            while (j < commonLen && j < other.Length && segments[j] == other[j])
                j++;
            commonLen = j;
            if (commonLen == 0) break;
        }

        return commonLen <= 1 ? "Assets" : string.Join("/", segments, 0, commonLen);
    }

    private static void MoveAssetWithParents(string src, string dst)
    {
        if (src == dst) return;

        // Ensure parent directories exist
        string dstParent = Path.GetDirectoryName(dst.Replace('/', Path.DirectorySeparatorChar))
                             ?.Replace(Path.DirectorySeparatorChar, '/');
        if (!string.IsNullOrEmpty(dstParent) && dstParent != "Assets" && !AssetDatabase.IsValidFolder(dstParent))
        {
            EnsureAssetFolder(dstParent);
        }

        string result = AssetDatabase.MoveAsset(src, dst);
        if (!string.IsNullOrEmpty(result))
            Debug.LogWarning($"[VrcThumbnail] MoveAsset error: {result}");
    }

    private static void EnsureAssetFolder(string path)
    {
        if (AssetDatabase.IsValidFolder(path))
            return;

        // Build parent chain
        string[] parts = path.Split('/');
        string current = "";
        for (int i = 0; i < parts.Length; i++)
        {
            current = i == 0 ? parts[i] : $"{current}/{parts[i]}";
            if (current == "Assets") continue;
            if (!AssetDatabase.IsValidFolder(current))
            {
                string parent = Path.GetDirectoryName(current.Replace('/', Path.DirectorySeparatorChar))
                                  ?.Replace(Path.DirectorySeparatorChar, '/');
                string folderName = Path.GetFileName(current);
                AssetDatabase.CreateFolder(parent, folderName);
            }
        }
    }

    private static void CleanupImportFolder()
    {
        if (AssetDatabase.IsValidFolder(ImportDir))
        {
            AssetDatabase.DeleteAsset(ImportDir);
            AssetDatabase.Refresh();
        }
    }

    private static string FindImportedAssetPath(string sourcePath)
    {
        string name = Path.GetFileName(sourcePath);
        string[] guids = AssetDatabase.FindAssets(name, new[] { ImportDir });
        if (guids.Length == 0)
        {
            // Also search genre folders
            var settings = LoadSettings();
            var searchDirs = new List<string>();
            if (settings.genreFolders != null)
                searchDirs.AddRange(settings.genreFolders.Select(g => g.folder).Where(f => !string.IsNullOrEmpty(f)));
            if (!string.IsNullOrEmpty(settings.defaultFolder))
                searchDirs.Add(settings.defaultFolder);

            foreach (string dir in searchDirs)
            {
                guids = AssetDatabase.FindAssets(name, new[] { dir });
                if (guids.Length > 0) break;
            }
        }
        if (guids.Length == 0)
            return null;
        return AssetDatabase.GUIDToAssetPath(guids[0]);
    }

    private static void SaveReadableThumbnail(Texture2D source, string originalAssetPath)
    {
        string dir = Path.GetDirectoryName(originalAssetPath);
        string stem = Path.GetFileNameWithoutExtension(originalAssetPath);
        string outPath = Path.Combine(dir, $"{stem}_preview.png");

        var rt = RenderTexture.GetTemporary(
            source.width, source.height, 0, RenderTextureFormat.ARGB32);
        RenderTexture.active = rt;
        Graphics.Blit(source, rt);

        var readable = new Texture2D(source.width, source.height, TextureFormat.RGBA32, false);
        readable.ReadPixels(new Rect(0, 0, source.width, source.height), 0, 0);
        readable.Apply();

        RenderTexture.active = null;
        RenderTexture.ReleaseTemporary(rt);

        byte[] png = readable.EncodeToPNG();
        File.WriteAllBytes(outPath, png);
        UnityEngine.Object.DestroyImmediate(readable);

        Debug.Log($"[VrcThumbnail] Thumbnail saved: {outPath}");
    }

    private static string FindLatestTempFile()
    {
        string[] files = Directory.GetFiles(Path.GetTempPath(), TempFilePattern);
        if (files.Length == 0)
            return null;

        string best = files[0];
        DateTime bestTime = File.GetLastWriteTime(best);
        for (int i = 1; i < files.Length; i++)
        {
            DateTime t = File.GetLastWriteTime(files[i]);
            if (t > bestTime)
            {
                bestTime = t;
                best = files[i];
            }
        }
        return best;
    }

    // ---------------------------------------------------------------
    // Data types
    // ---------------------------------------------------------------

    [System.Serializable]
    public class VrcThumbData
    {
        public string path;
        public string[] tags;
        public string filetype;
    }

    [System.Serializable]
    public class GenreEntry
    {
        public string genre;
        public string folder;
    }

    [System.Serializable]
    public class VrcThumbnailSettings
    {
        public GenreEntry[] genreFolders;
        public string defaultFolder = "Assets/Imported";
    }
}
#endif
