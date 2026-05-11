#if UNITY_EDITOR
using System;
using System.Collections.Generic;
using UnityEditor;
using UnityEngine;

/// <summary>
/// Editor window for configuring genre → folder mappings.
/// Open via Window > Booth Thumbnail Settings.
/// Settings persist to ProjectSettings/BoothThumbnail.json.
/// </summary>
public class BoothThumbnailSettingsWindow : EditorWindow
{
    private BoothThumbnail.BoothSettings _settings;
    private Vector2 _scrollPos;

    [MenuItem("Window/Booth Thumbnail Settings")]
    public static void ShowWindow()
    {
        var win = GetWindow<BoothThumbnailSettingsWindow>("Booth Thumbnail");
        win.minSize = new Vector2(420, 320);
    }

    private void OnEnable()
    {
        _settings = BoothThumbnail.GetSettings();
    }

    private void OnGUI()
    {
        if (_settings == null)
        {
            EditorGUILayout.HelpBox("Settings could not be loaded.", MessageType.Error);
            return;
        }

        _scrollPos = EditorGUILayout.BeginScrollView(_scrollPos);

        GUILayout.Label("Genre → Folder Mapping", EditorStyles.boldLabel);
        EditorGUILayout.HelpBox(
            "When an asset is imported, its Booth Organizer tags are matched against " +
            "the Genre column below (case-insensitive). The first matching genre " +
            "determines which folder the asset lands in.",
            MessageType.Info);

        EditorGUILayout.Space();

        // Ensure array is not null
        if (_settings.genreFolders == null)
            _settings.genreFolders = new BoothThumbnail.GenreEntry[0];

        int removeIndex = -1;

        for (int i = 0; i < _settings.genreFolders.Length; i++)
        {
            var entry = _settings.genreFolders[i];
            if (entry == null)
                entry = _settings.genreFolders[i] = new BoothThumbnail.GenreEntry();

            EditorGUILayout.BeginHorizontal();

            EditorGUILayout.LabelField($"{i + 1}.", GUILayout.Width(24));

            EditorGUILayout.LabelField("Tag:", GUILayout.Width(32));
            entry.genre = EditorGUILayout.TextField(entry.genre ?? "", GUILayout.Width(110));

            GUILayout.Label("→", GUILayout.Width(16));

            EditorGUILayout.LabelField("Folder:", GUILayout.Width(44));
            entry.folder = EditorGUILayout.TextField(entry.folder ?? "");

            if (GUILayout.Button("✕", GUILayout.Width(24), GUILayout.Height(18)))
                removeIndex = i;

            EditorGUILayout.EndHorizontal();
        }

        if (removeIndex >= 0)
        {
            var list = new List<BoothThumbnail.GenreEntry>(_settings.genreFolders);
            list.RemoveAt(removeIndex);
            _settings.genreFolders = list.ToArray();
        }

        EditorGUILayout.Space();

        if (GUILayout.Button("+ Add Genre Mapping"))
        {
            var list = new List<BoothThumbnail.GenreEntry>(_settings.genreFolders)
            {
                new BoothThumbnail.GenreEntry { genre = "", folder = "Assets/" }
            };
            _settings.genreFolders = list.ToArray();
        }

        EditorGUILayout.Space();
        EditorGUILayout.Space();

        // Default folder
        GUILayout.Label("Fallback", EditorStyles.boldLabel);
        EditorGUILayout.HelpBox(
            "Assets that don't match any genre tag land here.",
            MessageType.None);

        EditorGUILayout.BeginHorizontal();
        EditorGUILayout.LabelField("Default Folder:", GUILayout.Width(100));
        _settings.defaultFolder = EditorGUILayout.TextField(_settings.defaultFolder ?? "Assets/Imported");
        EditorGUILayout.EndHorizontal();

        EditorGUILayout.EndScrollView();

        EditorGUILayout.Space();

        // Save button
        EditorGUILayout.BeginHorizontal();
        GUILayout.FlexibleSpace();

        if (GUILayout.Button("Save", GUILayout.Width(100), GUILayout.Height(28)))
        {
            BoothThumbnail.SaveSettings(_settings);
            EditorUtility.DisplayDialog("Booth Thumbnail", "Settings saved.", "OK");
        }

        if (GUILayout.Button("Reset to Defaults", GUILayout.Width(130), GUILayout.Height(28)))
        {
            if (EditorUtility.DisplayDialog(
                "Reset Settings",
                "Reset genre mapping to factory defaults?",
                "Reset", "Cancel"))
            {
                _settings.genreFolders = new BoothThumbnail.GenreEntry[]
                {
                    new BoothThumbnail.GenreEntry { genre = "Avatar Base", folder = "Assets/1. Avatar Base" },
                    new BoothThumbnail.GenreEntry { genre = "Outfit & Acce", folder = "Assets/2. Outfit & Acce" },
                    new BoothThumbnail.GenreEntry { genre = "Gimmick", folder = "Assets/3. Gimmick" },
                    new BoothThumbnail.GenreEntry { genre = "Tools", folder = "Assets/4. Tools" },
                };
                _settings.defaultFolder = "Assets/Imported";
                BoothThumbnail.SaveSettings(_settings);
            }
        }

        EditorGUILayout.EndHorizontal();
    }
}
#endif
