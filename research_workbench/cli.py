"""Local CLI for discovery, planning, execution and extension registration."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import sys

from .core import WorkflowError, execute_plan, write_json
from .registry import Registry, default_skills_root, sha256
from .planner import codex_binary, plan_request


def exit_status(manifest):
    return {"complete": 0, "blocked": 3, "needs_input": 3, "failed": 2}[manifest["status"]]


def doctor(registry, skills_root):
    from .vmd_runtime import visualizer_readiness
    visualizers = visualizer_readiness(skills_root)
    entries = []
    for entry in registry.catalog():
        skill = skills_root / entry["skill"] if entry["skill"] else None
        item = {"id": entry["id"], "adapter": entry["adapter"], "skill_path": str(skill) if skill else None, "instructions_available": (skill / "SKILL.md").is_file() if skill else None, "operations": entry["operations"]}
        if entry["id"] in {"atomistic-structure-builder", "forcefield-generator"}:
            key = "ATOMISTIC_BUILDER_PYTHON" if entry["id"] == "atomistic-structure-builder" else "FF_GENERATOR_PYTHON"
            runtime = Path(os.environ[key]).expanduser() if os.environ.get(key) else skill / ".venv" / "bin" / "python"
            item["runtime_available"] = runtime.is_file() and os.access(runtime, os.X_OK)
            item["runtime"] = str(runtime)
        if entry["id"] == "vesta-figure-maker":
            item["native_app_available"] = Path("/Applications/VESTA.app").is_dir()
        if entry["id"] == "vmd-figure-studio":
            readiness = visualizers[entry["id"]]
            item.update({key: readiness[key] for key in ("launcher_available", "runtime_available", "native_available", "portable_compile_available", "offline_viewer_available")})
            item["runtime"] = readiness["runtime"]
        entries.append(item)
    try:
        codex = codex_binary()
    except WorkflowError:
        codex = None
    return {"schema_version": 1, "python": sys.version.split()[0], "codex_cli": codex, "mp_api_key_configured": bool(os.environ.get("MP_API_KEY")), "mp_api_key_link": "https://next-gen.materialsproject.org/api", "capabilities": entries, "note": "Discovery checks file availability, not scientific validation or a runtime benchmark."}


def register(descriptor_path, directory, adapter_file=None, trust_code=False):
    value = json.loads(descriptor_path.read_text())
    Registry.validate_descriptor(value)
    existing = Registry(directory)
    if value["id"] in existing.entries:
        raise WorkflowError(f"Capability already registered: {value['id']}")
    if value["adapter"].startswith("python:"):
        if not trust_code or adapter_file is None:
            raise WorkflowError("Executable extensions need --adapter-file and --trust-code after local code review")
        if not adapter_file.is_file() or adapter_file.suffix != ".py":
            raise WorkflowError("Extension adapter must be a local Python source file")
        module = value["adapter"].split(":")[1]
        code_target = directory / "extensions" / f"{module}.py"
        if code_target.exists():
            raise WorkflowError(f"Extension module already exists: {code_target}")
    elif adapter_file is not None or trust_code:
        raise WorkflowError("Code flags apply only to Python extensions")
    directory.mkdir(parents=True, exist_ok=True)
    destination = directory / (value["id"] + ".json")
    if destination.exists():
        raise WorkflowError(f"Descriptor already exists: {destination}")
    if value["adapter"].startswith("python:"):
        code_target.parent.mkdir(exist_ok=True)
        shutil.copyfile(adapter_file, code_target)
    write_json(destination, value)
    return {"id": value["id"], "descriptor": str(destination), "sha256": sha256(destination), "adapter": value["adapter"], "status": "registered"}


def main(argv=None):
    parser = argparse.ArgumentParser(prog="research-workbench", description="Research Workbench Agent: plan and execute extensible, audited skill workflows.")
    parser.add_argument("--registry", type=Path, help="Additional local capability descriptor directory")
    parser.add_argument("--skills-root", type=Path, default=default_skills_root())
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("doctor")
    commands.add_parser("capabilities")
    run = commands.add_parser("run")
    run.add_argument("--plan", type=Path, required=True)
    run.add_argument("--output", type=Path, required=True)
    resume = commands.add_parser("resume")
    resume.add_argument("--output", type=Path, required=True)
    ask = commands.add_parser("ask")
    ask.add_argument("--request", required=True)
    ask.add_argument("--workspace", type=Path, default=Path.cwd())
    ask.add_argument("--output", type=Path, required=True)
    ask.add_argument("--plan-only", action="store_true")
    ask.add_argument("--timeout", type=int, default=300)
    extension = commands.add_parser("register")
    extension.add_argument("--descriptor", type=Path, required=True)
    extension.add_argument("--registry", type=Path, required=True, dest="extension_registry")
    extension.add_argument("--adapter-file", type=Path)
    extension.add_argument("--trust-code", action="store_true")
    args = parser.parse_args(argv)
    args.skills_root = args.skills_root.expanduser().resolve()
    if args.registry:
        args.registry = args.registry.expanduser().resolve()
    try:
        registry = Registry(args.registry)
        if args.command == "doctor":
            result = doctor(registry, args.skills_root)
        elif args.command == "capabilities":
            result = registry.catalog()
        elif args.command == "register":
            result = register(args.descriptor.resolve(), args.extension_registry.expanduser().resolve(), args.adapter_file.resolve() if args.adapter_file else None, args.trust_code)
        elif args.command == "run":
            plan = json.loads(args.plan.read_text())
            plan["_base_dir"] = str(args.plan.resolve().parent)
            result = execute_plan(plan, args.output, args.skills_root, registry)
        elif args.command == "resume":
            plan = json.loads((args.output / "plan.json").read_text())
            result = execute_plan(plan, args.output, args.skills_root, registry, resume=True)
        else:
            if not 1 <= args.timeout <= 3600:
                raise WorkflowError("Planner timeout must be from 1 to 3600 seconds")
            output = args.output.expanduser().resolve()
            planning = output.with_name(output.name + "-planning")
            if output.exists():
                raise WorkflowError(f"Output directory already exists: {output}")
            decision = plan_request(args.request, args.workspace, planning, registry, args.skills_root, args.timeout)
            if decision["status"] == "needs_input" or args.plan_only:
                result = {"status": decision["status"], "planning_directory": str(planning), "questions": decision["questions"], "assumptions": decision["assumptions"], "plan": str(planning / "plan.json") if decision["status"] == "ready" else None}
            else:
                plan = json.loads((planning / "plan.json").read_text())
                result = execute_plan(plan, output, args.skills_root, registry)
                result["planning_directory"] = str(planning)
                result["planning_assumptions"] = decision["assumptions"]
        # Full evidence is saved on disk; terminal output stays compact.
        if isinstance(result, dict) and "stages" in result:
            print(json.dumps({"status": result["status"], "stages": {k: {"status": v["status"], "reason": v.get("reason", "")} for k, v in result["stages"].items()}, "scientific_acceptance": result["scientific_acceptance"]}, indent=2))
            return exit_status(result)
        print(json.dumps(result, indent=2))
        return exit_status(result) if isinstance(result, dict) and result.get("status") in {"ready", "needs_input", "failed", "blocked", "complete"} and result["status"] != "ready" else 0
    except (WorkflowError, ValueError, OSError, KeyError, TypeError) as error:
        print(f"research-workbench: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
