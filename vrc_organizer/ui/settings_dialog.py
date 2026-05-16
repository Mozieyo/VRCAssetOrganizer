from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QTabWidget, QWidget, QFormLayout,
    QLineEdit, QPushButton, QHBoxLayout, QSlider, QLabel,
    QCheckBox, QFileDialog, QDialogButtonBox, QGroupBox,
    QListWidget, QSpinBox,
)

from vrc_organizer.tools.registry import ToolRegistry


class SettingsDialog(QDialog):
    def __init__(self, tool_registry: ToolRegistry, parent=None):
        super().__init__(parent)
        # See note in TagDialog: without WA_DeleteOnClose, Qt parent-child
        # ownership keeps every opened dialog alive for the lifetime of the
        # main window.
        self.setAttribute(Qt.WA_DeleteOnClose)
        self._registry = tool_registry
        self.setWindowTitle("Settings")
        self.setMinimumSize(500, 400)
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)

        tabs = QTabWidget()

        # General tab
        general = QWidget()
        gen_form = QFormLayout(general)

        self._grid_size = QSpinBox()
        self._grid_size.setRange(64, 512)
        self._grid_size.setSingleStep(32)
        self._grid_size.setValue(192)
        gen_form.addRow("Default thumbnail size:", self._grid_size)

        tabs.addTab(general, "General")

        # Tools tab
        tools_tab = QTabWidget()
        for tool in self._registry.list_all():
            tool_widget = QWidget()
            tool_form = QFormLayout(tool_widget)

            exe_input = QLineEdit(tool.executable)
            exe_input.setPlaceholderText(f"Path to {tool.name}...")

            browse_btn = QPushButton("Browse...")
            browse_row = QWidget()
            browse_layout = QHBoxLayout(browse_row)
            browse_layout.setContentsMargins(0, 0, 0, 0)
            browse_layout.addWidget(exe_input)
            browse_layout.addWidget(browse_btn)

            browse_btn.clicked.connect(
                lambda checked, inp=exe_input: self._browse_exe(inp)
            )
            tool_form.addRow("Executable:", browse_row)

            args_input = QLineEdit(tool.args)
            tool_form.addRow("Arguments:", args_input)

            enable_cb = QCheckBox("Show in context menu")
            enable_cb.setChecked(tool.enabled)
            tool_form.addRow(enable_cb)

            # Store references
            setattr(self, f"_exe_{tool.name}", exe_input)
            setattr(self, f"_args_{tool.name}", args_input)
            setattr(self, f"_enable_{tool.name}", enable_cb)

            tools_tab.addTab(tool_widget, tool.name)

        tabs.addTab(tools_tab, "Tools")

        layout.addWidget(tabs)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._save_and_accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _browse_exe(self, input_widget: QLineEdit):
        path, _ = QFileDialog.getOpenFileName(
            self, "Select Executable", "",
            "Executables (*.exe);;All Files (*)"
        )
        if path:
            input_widget.setText(path)

    def _save_and_accept(self):
        for tool in self._registry.list_all():
            exe = getattr(self, f"_exe_{tool.name}").text()
            args = getattr(self, f"_args_{tool.name}").text()
            enabled = getattr(self, f"_enable_{tool.name}").isChecked()
            tool.executable = exe
            tool.args = args
            tool.enabled = enabled
            self._registry.save(tool)
        self.accept()

    def grid_size(self) -> int:
        return self._grid_size.value()
