"""Discover visualization runtimes without importing skill code or probing a GUI.

Only the VMD Studio's declared local path settings are read. Availability here
means an executable exists; dependency imports and rendering remain run evidence.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import sys


def _executable(value, *, resolve=False):
    if not isinstance(value, str) or not value:
        return None
    path = Path(value).expanduser()
    if not path.is_absolute() or not path.is_file() or not os.access(path, os.X_OK):
        return None
    # Preserve a virtual environment's lexical bin/python path.
    return str(path.resolve() if resolve else path.absolute())


def _native(value):
    if not isinstance(value, str) or not value:
        return None
    path = Path(value).expanduser()
    if path.is_dir() and path.suffix == ".app":
        for binary in sorted((path / "Contents" / "vmd").glob("vmd_MACOSX*")):
            found = _executable(str(binary), resolve=True)
            if found:
                return found
        return None
    if not path.is_absolute() and os.sep not in value:
        return _executable(shutil.which(value), resolve=True)
    return _executable(str(path), resolve=True)


def resolve_runtime(skill):
    """Return effective Python/native paths; never install or run anything."""
    skill = Path(skill).expanduser().resolve()
    settings_file = Path(os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config"))) / "vmd-figure-studio" / "runtime.json"
    settings = {}
    errors = []
    if settings_file.is_file():
        try:
            if settings_file.stat().st_size > 32768:
                raise ValueError("settings exceed the path-file size limit")
            raw = json.loads(settings_file.read_text())
            if not isinstance(raw, dict):
                raise ValueError("settings must be an object")
            # Unrelated fields are not copied into prompts, receipts or hashes.
            settings = {key: raw[key] for key in ("python_executable", "vmd_executable") if isinstance(raw.get(key), str)}
        except (OSError, ValueError) as error:
            errors.append("VMD Studio local path settings could not be read: " + type(error).__name__)

    python = None
    python_source = None
    if os.environ.get("VMD_STUDIO_PYTHON"):
        python_source = "VMD_STUDIO_PYTHON"
        python = _executable(os.environ["VMD_STUDIO_PYTHON"])
        if python is None:
            errors.append("VMD_STUDIO_PYTHON must select an existing absolute executable")
    else:
        for name in (skill / ".venv" / "bin" / "python", skill / ".venv" / "Scripts" / "python.exe"):
            python = _executable(str(name))
            if python:
                python_source = "skill-local virtual environment"
                break
        if python is None and settings.get("python_executable"):
            python_source = "VMD Studio local path settings"
            python = _executable(settings["python_executable"])
            if python is None:
                errors.append("Configured VMD Studio Python is unavailable; repair settings or set VMD_STUDIO_PYTHON")
        if python is None and not settings.get("python_executable"):
            python = _executable(sys.executable) or _executable(shutil.which("python3"))
            python_source = "coordinator Python; scientific dependencies unprobed"

    vmd = None
    vmd_source = None
    if os.environ.get("VMD_EXECUTABLE"):
        vmd_source = "VMD_EXECUTABLE"
        vmd = _native(os.environ["VMD_EXECUTABLE"])
        if vmd is None:
            errors.append("VMD_EXECUTABLE does not select an available native executable or supported app bundle")
    else:
        choices = [(shutil.which("vmd"), "PATH"), (settings.get("vmd_executable"), "VMD Studio local path settings")]
        applications = Path("/Applications")
        if applications.is_dir():
            choices.extend((str(p), "Applications app discovery") for p in sorted(applications.glob("VMD*.app")))
        for candidate, source in choices:
            vmd = _native(candidate)
            if vmd:
                vmd_source = source
                break
    return {"python_executable": python, "python_source": python_source,
            "vmd_executable": vmd, "vmd_source": vmd_source,
            "configuration_file": str(settings_file) if settings_file.is_file() else None,
            "errors": errors, "discovery_only": True}


def visualizer_readiness(skills_root):
    """Metadata-only routing context, suitable for the read-only planner."""
    skills = Path(skills_root)
    vesta = skills / "vesta-figure-maker"
    studio = skills / "vmd-figure-studio"
    runtime = resolve_runtime(studio)
    vmd_launcher = (studio / "scripts" / "vmd_studio.py").is_file()
    vmd_instructions = (studio / "SKILL.md").is_file()
    runtime_available = runtime["python_executable"] is not None
    native_available = runtime["vmd_executable"] is not None
    return {
        "vesta-figure-maker": {
            "instructions_available": (vesta / "SKILL.md").is_file(),
            "launcher_available": (vesta / "scripts" / "vesta_export.sh").is_file(),
            "native_available": sys.platform == "darwin" and Path("/Applications/VESTA.app").is_dir(),
            "platform": "packaged native adapter requires macOS and /Applications/VESTA.app",
            "discovery_only": True,
        },
        "vmd-figure-studio": {
            "instructions_available": vmd_instructions, "launcher_available": vmd_launcher,
            "runtime_available": runtime_available, "native_available": native_available,
            "portable_compile_available": vmd_instructions and vmd_launcher and runtime_available,
            "offline_viewer_available": vmd_instructions and vmd_launcher and runtime_available and (studio / "assets" / "vendor" / "3Dmol-min.js").is_file(),
            "runtime": runtime, "discovery_only": True,
        },
    }
