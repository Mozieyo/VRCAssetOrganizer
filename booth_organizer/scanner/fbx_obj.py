from __future__ import annotations

from pathlib import Path

from booth_organizer.scanner.orchestrator import ScanReport


def scan_fbx_obj(filepath: Path) -> ScanReport:
    suffix = filepath.suffix.lower()
    ft = "fbx" if suffix == ".fbx" else "obj"

    contents: list[tuple[str, str, int]] = []
    metadata: dict[str, str] = {}

    try:
        with open(filepath, "r", encoding="utf-8", errors="replace") as f:
            head = f.read(8192)
    except (OSError, UnicodeDecodeError):
        head = ""

    if ft == "obj":
        for line in head.splitlines():
            line = line.strip()
            if line.startswith("o ") or line.startswith("g "):
                name = line[2:].strip()
                if name:
                    contents.append((name, "object", 0))
            elif line.startswith("mtllib "):
                metadata["mtllib"] = line[7:].strip()
            elif line.startswith("usemtl "):
                mat = line[7:].strip()
                if mat:
                    contents.append((mat, "material", 0))

    elif ft == "fbx":
        # Try to detect binary vs ASCII FBX
        if head.startswith("Kaydara FBX Binary"):
            metadata["format"] = "binary"
        else:
            metadata["format"] = "ascii"
            for line in head.splitlines():
                line = line.strip()
                if 'Model:' in line:
                    quote1 = line.find('"')
                    if quote1 >= 0:
                        quote2 = line.find('"', quote1 + 1)
                        if quote2 >= 0:
                            name = line[quote1 + 1:quote2]
                            contents.append((name, "mesh", 0))

    return ScanReport(filetype=ft, thumbnail_source=None, contents=contents, metadata=metadata)
