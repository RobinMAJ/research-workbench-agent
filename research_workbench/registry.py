"""Declarative capability discovery. Loading metadata never imports extension code."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import sys

MODULE_ROOT = Path(__file__).resolve().parent
ROOT = MODULE_ROOT.parent if (MODULE_ROOT.parent / "capabilities").is_dir() else MODULE_ROOT / "resources"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def default_skills_root() -> Path:
    return Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex"))) / "skills"


class Registry:
    def __init__(self, directory: Path | None = None):
        self.directory = Path(directory).resolve() if directory else None
        self.entries: dict[str, dict] = {}
        directories = [ROOT / "capabilities"]
        if self.directory:
            directories.append(self.directory)
        for folder in directories:
            if not folder.exists():
                continue
            for path in sorted(folder.glob("*.json")):
                value = json.loads(path.read_text())
                self.validate_descriptor(value)
                if value["id"] in self.entries:
                    raise ValueError(f"Duplicate capability: {value['id']}")
                value["_descriptor"] = str(path)
                if value["adapter"].startswith("python:"):
                    module = value["adapter"].split(":")[1]
                    code = folder / "extensions" / f"{module}.py"
                    if not code.is_file():
                        raise ValueError(f"Missing registered extension module: {code}")
                    value["_code"] = str(code)
                self.entries[value["id"]] = value

    @staticmethod
    def validate_descriptor(value: dict):
        required = {"schema_version", "id", "version", "skill", "description", "operations", "adapter", "limits", "side_effects"}
        if not isinstance(value, dict) or not required <= value.keys():
            raise ValueError("Capability descriptor is missing required fields")
        if value["schema_version"] != 1 or not re.fullmatch(r"[a-z][a-z0-9-]{0,63}", value["id"]):
            raise ValueError("Invalid capability version or id")
        if not all(isinstance(value[key], str) and value[key] for key in ("version", "description", "adapter")):
            raise ValueError("Capability text fields must be nonempty strings")
        if value["skill"] is not None and (not isinstance(value["skill"], str) or not re.fullmatch(r"[a-z][a-z0-9-]{0,63}", value["skill"])):
            raise ValueError("Capability skill must be a valid installed skill id or null for coordinator utilities")
        if value["adapter"] == "instruction-only" and value["skill"] is None:
            raise ValueError("Instruction-only route requires an installed skill id")
        operations = value["operations"]
        if not isinstance(operations, list) or not operations or len(set(operations)) != len(operations) or not all(isinstance(x, str) and re.fullmatch(r"[a-z][a-z0-9-]{0,63}", x) for x in operations):
            raise ValueError("Invalid capability operations")
        if not isinstance(value["limits"], dict) or not isinstance(value["side_effects"], list):
            raise ValueError("Invalid capability limits or side effects")
        adapter = value["adapter"]
        if adapter not in {"builtin", "instruction-only"} and not re.fullmatch(r"python:[A-Za-z_][A-Za-z0-9_]*:[A-Za-z_][A-Za-z0-9_]*", adapter):
            raise ValueError("Invalid adapter; use builtin, instruction-only or python:module:function")
        if adapter == "builtin" and value["id"] not in {"atomistic-structure-builder", "forcefield-generator", "vesta-figure-maker", "vmd-figure-studio", "visualization"}:
            raise ValueError("New capabilities need a declared adapter or instruction-only route")

    def get(self, capability: str, operation: str | None = None) -> dict:
        if capability not in self.entries:
            raise ValueError(f"Unknown capability: {capability}")
        entry = self.entries[capability]
        if operation and operation not in entry["operations"]:
            raise ValueError(f"Unsupported operation: {capability}.{operation}")
        return entry

    def catalog(self) -> list[dict]:
        return [{k: v for k, v in entry.items() if not k.startswith("_")} for entry in self.entries.values()]

    def fingerprint(self, capability: str, skills_root: Path) -> dict:
        entry = self.get(capability)
        result = {"descriptor": sha256(Path(entry["_descriptor"])), "adapter_version": entry["version"]}
        if "_code" in entry:
            result["extension"] = sha256(Path(entry["_code"]))
        if entry["skill"] is None:
            return result
        # Hash only the relevant skill entry point, scripts and dependency declarations.
        skill = skills_root / entry["skill"]
        paths = [skill / "SKILL.md", skill / "requirements.txt", skill / "environment.yml"]
        if (skill / "scripts").is_dir():
            paths += [p for p in sorted((skill / "scripts").rglob("*")) if p.is_file() and p.suffix in {".py", ".sh", ".json"}]
        if entry["skill"] == "vmd-figure-studio":
            paths += [p for p in sorted((skill / "assets" / "vendor").rglob("*")) if p.is_file()]
        result["skill_files"] = {str(p.relative_to(skill)): sha256(p) for p in paths if p.is_file()}
        override = {"atomistic-structure-builder": "ATOMISTIC_BUILDER_PYTHON", "forcefield-generator": "FF_GENERATOR_PYTHON"}.get(entry["skill"])
        runtime = Path(os.environ[override]).expanduser().absolute() if override and os.environ.get(override) else skill / ".venv" / "bin" / "python"
        if entry["skill"] == "vmd-figure-studio":
            from .vmd_runtime import resolve_runtime
            settings = resolve_runtime(skill)
            result["visualization_runtime"] = {key: settings[key] for key in ("python_executable", "python_source", "vmd_executable", "vmd_source")}
            if settings["vmd_executable"]:
                result["vmd_executable_sha256"] = sha256(Path(settings["vmd_executable"]))
            if not settings["python_executable"]:
                result["runtime"] = "unavailable"
                return result
            runtime = Path(settings["python_executable"])
        result["runtime"] = str(runtime) if runtime.exists() else "python3"
        if runtime.is_file():
            result["runtime_sha256"] = sha256(runtime.resolve())
        # Pin dependency metadata without reading auth, caches or private user settings.
        env_root = runtime.parent.parent.resolve()
        if (env_root / "conda-meta").is_dir():
            result["dependencies"] = {p.name: sha256(p) for p in sorted((env_root / "conda-meta").glob("*.json"))}
        elif (env_root / "lib").is_dir():
            result["dependencies"] = {str(p.relative_to(env_root)): sha256(p) for p in sorted((env_root / "lib").glob("python*/site-packages/*.dist-info/METADATA"))}
        return result

    def execute(self, capability, operation, params, output_dir, skills_root, run_process):
        entry = self.get(capability, operation)
        if entry["adapter"] == "instruction-only":
            return {"status": "blocked", "reason": "This skill is registered for conversational use; no automatic execution adapter is installed.", "artifacts": {}, "evidence": {"scientific_acceptance": "pending"}, "details": {"skill": entry["skill"]}}
        if entry["adapter"] == "builtin":
            from .adapters import execute
            return execute(capability, operation, params, output_dir, skills_root, run_process)
        _, _, function = entry["adapter"].split(":")
        code = Path(entry["_code"])
        module_spec = importlib.util.spec_from_file_location("rwb_extension_" + sha256(code)[:16], code)
        module = importlib.util.module_from_spec(module_spec)
        sys.modules[module_spec.name] = module
        module_spec.loader.exec_module(module)
        return getattr(module, function)(capability, operation, params, output_dir, skills_root, run_process)
