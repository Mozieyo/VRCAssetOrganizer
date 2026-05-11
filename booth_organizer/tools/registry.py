from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from PySide6.QtCore import QSettings


@dataclass
class ToolConfig:
    name: str
    executable: str = ""
    args: str = "{file}"
    filetypes: list[str] = field(default_factory=list)
    enabled: bool = True


DEFAULT_TOOLS: list[ToolConfig] = [
    ToolConfig("Blender", "", "{file}", ["blend", "fbx", "obj"]),
    ToolConfig("Unity Editor", "", "-projectPath {project} -executeMethod BoothThumbnail.ProcessSingle",
               ["unitypackage", "booth_zip", "prefab", "mat"]),
    ToolConfig("Photoshop", "", "{file}", ["psd", "tga"]),
    ToolConfig("Default Viewer", "", "{file}", ["*"]),
]

# Pre-computed mapping: filetype → list of tool names (from defaults)
DEFAULT_TOOL_MAP: dict[str, list[str]] = {
    "blend": ["Blender"],
    "fbx": ["Blender", "Unity Editor"],
    "obj": ["Blender", "Unity Editor"],
    "unitypackage": ["Unity Editor"],
    "booth_zip": ["Unity Editor"],
    "prefab": ["Unity Editor"],
    "mat": ["Unity Editor"],
    "image": ["Default Viewer"],
    "psd": ["Photoshop"],
    "tga": ["Photoshop"],
}


class ToolRegistry:
    SETTINGS_GROUP = "tools"

    def __init__(self):
        self._settings = QSettings()
        self._ensure_defaults()

    def _ensure_defaults(self):
        self._settings.beginGroup(self.SETTINGS_GROUP)
        existing = self._settings.childGroups()
        self._settings.endGroup()
        if not existing:
            for tool in DEFAULT_TOOLS:
                self.save(tool)

    def list_all(self) -> list[ToolConfig]:
        tools = []
        self._settings.beginGroup(self.SETTINGS_GROUP)
        for name in self._settings.childGroups():
            self._settings.beginGroup(name)
            tc = ToolConfig(
                name=name,
                executable=self._settings.value("executable", ""),
                args=self._settings.value("args", "{file}"),
                filetypes=self._settings.value("filetypes", []),
                enabled=self._settings.value("enabled", True, type=bool),
            )
            tools.append(tc)
            self._settings.endGroup()
        self._settings.endGroup()
        return tools

    def for_filetype(self, filetype: str) -> list[ToolConfig]:
        return [t for t in self.list_all()
                if t.enabled and ("*" in t.filetypes or filetype in t.filetypes)]

    def save(self, tool: ToolConfig):
        self._settings.beginGroup(self.SETTINGS_GROUP)
        self._settings.beginGroup(tool.name)
        self._settings.setValue("executable", tool.executable)
        self._settings.setValue("args", tool.args)
        self._settings.setValue("filetypes", tool.filetypes)
        self._settings.setValue("enabled", tool.enabled)
        self._settings.endGroup()
        self._settings.endGroup()

    def get(self, name: str) -> ToolConfig | None:
        for t in self.list_all():
            if t.name == name:
                return t
        return None
