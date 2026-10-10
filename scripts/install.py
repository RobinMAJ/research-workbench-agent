#!/usr/bin/env python3
"""Install a personal Codex agent and its self-contained launcher without changing config."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile

if sys.version_info < (3, 11):
    raise SystemExit("Python >=3.11 is required. Use scripts/bootstrap.sh install to locate an existing runtime.")
import tomllib

ROOT = Path(__file__).resolve().parent.parent


def checksum(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def install(codex_home):
    if sys.version_info < (3, 11):
        raise ValueError("Python 3.11 or newer is required")
    codex_home = codex_home.expanduser().resolve()
    skill_target = codex_home / "skills" / "research-workbench"
    agent_target = codex_home / "agents" / "research_workbench.toml"
    agent_source = ROOT / "agents" / "research_workbench.toml"
    agent = tomllib.loads(agent_source.read_text())
    if not {"name", "description", "developer_instructions"} <= agent.keys() or agent["name"] != "research_workbench":
        raise ValueError("Invalid custom agent definition")
    files = {}
    for folder in ("research_workbench", "capabilities", "docs", "agents", "examples"):
        for path in (ROOT / folder).rglob("*"):
            if path.is_file() and "__pycache__" not in path.parts and path.suffix != ".pyc":
                files[str(path.relative_to(ROOT))] = path
    # Launcher is flattened; the custom agent file is also installed separately.
    for path in (ROOT / "launcher").rglob("*"):
        if path.is_file():
            relative = str(path.relative_to(ROOT / "launcher"))
            if relative in files:
                raise ValueError(f"Installation file collision: {relative}")
            files[relative] = path
    for name in ("README.md", "NOTICE.md", "LICENSE"):
        files[name] = ROOT / name
    desired = {name: checksum(path) for name, path in files.items()}
    if skill_target.exists():
        actual = {str(p.relative_to(skill_target)): checksum(p) for p in skill_target.rglob("*") if p.is_file() and "__pycache__" not in p.parts and p.suffix != ".pyc" and p.name != "installation.json"}
        if actual != desired:
            raise ValueError(f"Existing installation differs: {skill_target}. Preserve it and choose a separate Codex home for this version")
    if agent_target.exists() and checksum(agent_target) != checksum(agent_source):
        raise ValueError(f"Existing agent definition differs: {agent_target}")
    skill_target.parent.mkdir(parents=True, exist_ok=True)
    agent_target.parent.mkdir(parents=True, exist_ok=True)
    if not skill_target.exists():
        temporary = Path(tempfile.mkdtemp(prefix=".research-workbench-", dir=skill_target.parent))
        try:
            for name, source in files.items():
                target = temporary / name
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, target)
            os.chmod(temporary / "scripts" / "run.sh", 0o755)
            (temporary / "installation.json").write_text(json.dumps({"schema_version": 1, "source": str(ROOT), "payload_sha256": desired}, indent=2) + "\n")
            os.rename(temporary, skill_target)
        finally:
            if temporary.exists():
                shutil.rmtree(temporary)
    if not agent_target.exists():
        with agent_target.open("xb") as handle:
            handle.write(agent_source.read_bytes())
    return {"status": "installed", "skill": str(skill_target), "agent": str(agent_target), "payload_files": len(desired), "config_modified": False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--codex-home", type=Path, default=Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex"))))
    args = parser.parse_args()
    try:
        print(json.dumps(install(args.codex_home), indent=2))
    except (OSError, ValueError) as error:
        parser.exit(2, f"Install failed: {error}\n")


if __name__ == "__main__":
    main()
