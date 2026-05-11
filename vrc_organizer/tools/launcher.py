from __future__ import annotations

import json
import os
import subprocess
import tempfile
from pathlib import Path

from PySide6.QtCore import QObject, Signal

from vrc_organizer.tools.registry import ToolConfig
from vrc_organizer.unity_windows import bring_to_front

UNITY_JSON_PATH = Path(tempfile.gettempdir()) / "vrc_thumb_latest.json"


class ToolLauncher(QObject):
    tool_launched = Signal(str, str)   # tool_name, filepath
    tool_error = Signal(str, str)      # tool_name, error_message

    def write_unity_json(self, filepath: Path, asset_tags: list[str],
                         asset_filetype: str):
        """Write the import instruction JSON for the Unity plugin."""
        UNITY_JSON_PATH.write_text(json.dumps({
            "path": str(filepath),
            "tags": asset_tags,
            "filetype": asset_filetype,
        }))

    def launch(self, tool: ToolConfig, filepath: Path,
               unity_project: str = "",
               asset_tags: list[str] | None = None,
               asset_filetype: str = "") -> bool:
        tags = asset_tags or []
        ft = asset_filetype

        if tool.name == "Unity Editor":
            self.write_unity_json(filepath, tags, ft)

        args = tool.args.replace("{file}", str(filepath))
        args = args.replace("{dir}", str(filepath.parent))
        args = args.replace("{filename}", filepath.name)
        args = args.replace("{project}", unity_project)

        exe = tool.executable
        if not exe:
            self.tool_error.emit(tool.name, f"Executable path not configured for {tool.name}")
            return False

        try:
            cmd = [exe] + args.split()
            creationflags = 0x00000008 if os.name == "nt" else 0
            subprocess.Popen(cmd, creationflags=creationflags)
            self.tool_launched.emit(tool.name, str(filepath))
            return True
        except FileNotFoundError:
            self.tool_error.emit(tool.name, f"Could not find {exe}")
            return False
        except Exception as e:
            self.tool_error.emit(tool.name, str(e))
            return False

    def launch_unity_fresh(self, filepath: Path, unity_exe: str,
                           unity_project: str, asset_tags: list[str],
                           asset_filetype: str):
        """Launch Unity with -executeMethod to process a single file."""
        self.write_unity_json(filepath, asset_tags, asset_filetype)
        cmd = [
            unity_exe,
            "-projectPath", unity_project,
            "-executeMethod", "VrcThumbnail.ProcessSingle",
        ]
        try:
            creationflags = 0x00000008 if os.name == "nt" else 0
            subprocess.Popen(cmd, creationflags=creationflags)
            self.tool_launched.emit("Unity Editor", str(filepath))
            return True
        except Exception as e:
            self.tool_error.emit("Unity Editor", str(e))
            return False

    def open_in_running_unity(self, hwnd: int, filepath: Path,
                              asset_tags: list[str], asset_filetype: str):
        """Target a running Unity Editor. Writes JSON and brings window to front."""
        self.write_unity_json(filepath, asset_tags, asset_filetype)
        bring_to_front(hwnd)
        self.tool_launched.emit("Unity Editor", str(filepath))

