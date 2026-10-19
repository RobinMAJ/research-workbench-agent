"""Deterministic execution, immutable handoffs and conservative resume semantics."""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import html
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import time

from .registry import Registry, MODULE_ROOT, default_skills_root, sha256


class WorkflowError(ValueError):
    pass


PATH_KEYS = {"file", "path", "input", "bulk", "film", "substrate", "slab", "adsorbate", "molecule", "sdf", "geometry", "graph_sdf", "atom_map", "scene", "source_manifest", "manifest", "charges_file", "scan_csv", "param_file", "topology", "coordinates", "charge_file", "reference_file"}
SECRET_KEYS = {"api_key", "mp_api_key", "openai_api_key", "password", "access_token", "private_key"}


def now():
    return datetime.now(timezone.utc).isoformat()


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def write_json(path: Path, value):
    path = Path(path)
    temporary = path.with_name(path.name + ".new")
    with temporary.open("w") as handle:
        json.dump(value, handle, indent=2, ensure_ascii=False)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def normalize_params(value, base: Path, key=""):
    if key.lower() in SECRET_KEYS or key.lower().endswith("_api_key"):
        raise WorkflowError("Credentials must remain in the local environment, never in a workflow plan")
    if isinstance(value, dict):
        if "$ref" in value:
            if set(value) != {"$ref"} or not isinstance(value["$ref"], str):
                raise WorkflowError("Artifact references must contain only a string $ref")
            return value
        return {k: normalize_params(v, base, k) for k, v in value.items()}
    if isinstance(value, list):
        return [normalize_params(v, base, key) for v in value]
    if isinstance(value, str) and key in PATH_KEYS:
        candidate = Path(value).expanduser()
        if not candidate.is_absolute():
            candidate = base / candidate
        if not candidate.is_file():
            raise WorkflowError(f"Missing input file for {key}: {candidate}")
        return str(candidate.resolve())
    return value


def references(value):
    if isinstance(value, dict):
        if "$ref" in value:
            yield value["$ref"]
        else:
            for v in value.values():
                yield from references(v)
    elif isinstance(value, list):
        for v in value:
            yield from references(v)


def source_files(value):
    if isinstance(value, dict):
        if "$ref" not in value:
            for v in value.values():
                yield from source_files(v)
    elif isinstance(value, list):
        for v in value:
            yield from source_files(v)
    elif isinstance(value, str):
        path = Path(value)
        if path.is_absolute() and path.is_file():
            yield path


def validate_plan(plan: dict, registry: Registry) -> dict:
    if not isinstance(plan, dict) or plan.get("schema_version") != 1:
        raise WorkflowError("Workflow requires schema_version 1")
    if set(plan) - {"schema_version", "title", "steps", "_base_dir"}:
        raise WorkflowError("Unknown workflow fields")
    if not isinstance(plan.get("title"), str) or not plan["title"].strip():
        raise WorkflowError("Workflow requires a title")
    steps = plan.get("steps")
    if not isinstance(steps, list) or not 1 <= len(steps) <= 32:
        raise WorkflowError("Workflow needs 1 to 32 steps")
    result = {"schema_version": 1, "title": plan["title"], "steps": []}
    base = Path(plan.get("_base_dir", Path.cwd())).resolve()
    ids = set()
    for original in steps:
        required = {"id", "capability", "operation", "depends_on", "params"}
        if not isinstance(original, dict) or not required <= original.keys() or set(original) - required - {"timeout_seconds"}:
            raise WorkflowError("Invalid stage fields")
        stage = dict(original)
        if not isinstance(stage["id"], str) or not re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", stage["id"]) or stage["id"] in ids:
            raise WorkflowError("Duplicate or invalid stage id")
        ids.add(stage["id"])
        try:
            registry.get(stage["capability"], stage["operation"])
        except ValueError as error:
            raise WorkflowError(str(error)) from error
        if not isinstance(stage["depends_on"], list) or not all(isinstance(x, str) for x in stage["depends_on"]) or len(set(stage["depends_on"])) != len(stage["depends_on"]):
            raise WorkflowError("Dependencies must be unique stage ids")
        if not isinstance(stage["params"], dict):
            raise WorkflowError("Stage params must be an object")
        timeout = stage.get("timeout_seconds", 600)
        if type(timeout) is not int or not 1 <= timeout <= 3600:
            raise WorkflowError("Stage timeout must be an integer from 1 to 3600 seconds")
        stage["timeout_seconds"] = timeout
        stage["params"] = normalize_params(stage["params"], base)
        result["steps"].append(stage)
    by_id = {s["id"]: s for s in result["steps"]}
    ordered, visiting, visited = [], set(), set()

    def visit(stage_id):
        if stage_id in visiting:
            raise WorkflowError("Workflow contains a dependency cycle")
        if stage_id in visited:
            return
        if stage_id not in by_id:
            raise WorkflowError(f"Unknown dependency: {stage_id}")
        visiting.add(stage_id)
        for dep in by_id[stage_id]["depends_on"]:
            visit(dep)
        visiting.remove(stage_id)
        visited.add(stage_id)
        ordered.append(by_id[stage_id])

    for stage_id in by_id:
        visit(stage_id)

    def ancestors(stage_id):
        found = set()
        for dep in by_id[stage_id]["depends_on"]:
            found.add(dep)
            found.update(ancestors(dep))
        return found

    for stage in ordered:
        for ref in references(stage["params"]):
            parts = ref.split(":")
            if len(parts) != 2 or not all(parts) or parts[0] not in ancestors(stage["id"]):
                raise WorkflowError(f"Reference {ref} must name a dependency and artifact key")
    result["steps"] = ordered
    return result


def resolve_refs(value, stages):
    if isinstance(value, dict):
        if "$ref" in value:
            stage_id, artifact = value["$ref"].split(":")
            entry = stages[stage_id].get("artifacts", {}).get(artifact)
            if not entry:
                raise WorkflowError(f"Missing dependency artifact: {value['$ref']}")
            path = Path(entry["path"])
            if not path.is_file() or sha256(path) != entry["sha256"]:
                raise WorkflowError(f"Dependency artifact changed: {value['$ref']}")
            return str(path)
        return {k: resolve_refs(v, stages) for k, v in value.items()}
    if isinstance(value, list):
        return [resolve_refs(v, stages) for v in value]
    return value


def process_runner(directory: Path, stage_timeout: int, journal=None):
    counter = [0]
    deadline = time.monotonic() + stage_timeout

    def run(argv, cwd=None, timeout=600, env=None):
        if not isinstance(argv, list) or not argv or not all(isinstance(x, str) for x in argv):
            raise WorkflowError("Adapter commands must be a nonempty argv list")
        counter[0] += 1
        prefix = directory / f"process-{counter[0]:02d}"
        stdout, stderr = prefix.with_suffix(".stdout.log"), prefix.with_suffix(".stderr.log")
        remaining = max(0.001, min(float(timeout), deadline - time.monotonic()))
        process_env = os.environ.copy()
        process_env.update(env or {})
        started, timed_out = now(), False
        with stdout.open("wb") as out, stderr.open("wb") as err:
            process = subprocess.Popen(argv, cwd=cwd, env=process_env, stdout=out, stderr=err, start_new_session=True)
            if journal:
                journal({"pid": process.pid, "started_at": started, "argv": argv, "stdout_file": str(stdout), "stderr_file": str(stderr)})
            try:
                exit_code = process.wait(timeout=remaining)
            except subprocess.TimeoutExpired:
                timed_out = True
                os.killpg(process.pid, signal.SIGTERM)
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait()
                exit_code = process.returncode
        record = {"exit_code": exit_code, "stdout_file": str(stdout), "stderr_file": str(stderr), "timed_out": timed_out, "started_at": started, "ended_at": now(), "argv": argv}
        write_json(prefix.with_suffix(".json"), record)
        return record

    # Native renderers may own separate process groups. Adapters can reserve
    # their native timeout/cleanup inside this outer stage deadline.
    run.remaining_seconds = lambda: max(0.0, deadline - time.monotonic())
    return run


def output_inventory(output):
    return {str(p.relative_to(output)): sha256(p) for p in sorted(output.rglob("*")) if p.is_file() and p != output / "manifest.json" and p.name != ".run.lock" and not p.name.endswith(".new")}


def verify_resume(manifest, plan, sources, tools, output):
    if manifest.get("status") not in {"complete", "blocked", "failed"}:
        raise WorkflowError("Incomplete run requires process/output audit before retry")
    if set(manifest.get("stages", {})) != {stage["id"] for stage in plan["steps"]}:
        raise WorkflowError("Saved run does not contain every planned stage")
    if any(stage.get("status") not in {"complete", "blocked", "failed", "dependency_blocked"} for stage in manifest["stages"].values()):
        raise WorkflowError("Invalid or interrupted saved stage status")
    states = {stage["status"] for stage in manifest["stages"].values()}
    expected = "failed" if "failed" in states else "blocked" if states & {"blocked", "dependency_blocked"} else "complete"
    if manifest["status"] != expected:
        raise WorkflowError("Saved execution status contradicts its stage statuses")
    if manifest.get("plan_sha256") != digest(plan):
        raise WorkflowError("Cannot resume a changed plan; use a new output directory")
    if manifest.get("source_sha256") != sources:
        raise WorkflowError("Input source changed since this run")
    if manifest.get("tool_fingerprints") != tools:
        raise WorkflowError("Adapter, skill or runtime changed since this run")
    for name, checksum in manifest.get("output_sha256", {}).items():
        path = output / name
        if not path.is_file() or sha256(path) != checksum:
            raise WorkflowError(f"Run artifact changed: {name}")
    for stage in manifest.get("stages", {}).values():
        if stage.get("status") == "running":
            raise WorkflowError("Interrupted stage requires process/output audit before retry; use a new run after resolving it")
        for artifact in stage.get("artifacts", {}).values():
            path = Path(artifact["path"])
            if not path.is_file() or sha256(path) != artifact["sha256"]:
                raise WorkflowError(f"Artifact changed: {path}")


def write_report(output, manifest):
    rows = []
    lines = [f"# {manifest['title']}", "", f"Execution: **{manifest['status']}**. Scientific acceptance: **pending**.", "", "These are preparation outputs. Construction, parameter assignment and image export do not establish stability, adsorption preference or predictive accuracy.", ""]
    for stage_id, stage in manifest["stages"].items():
        rows.append(f"<tr><td>{html.escape(stage_id)}</td><td>{html.escape(stage['status'])}</td><td>{html.escape(stage.get('reason', ''))}</td></tr>")
        lines += [f"## {stage_id}", "", f"Status: {stage['status']}", "", stage.get("reason", ""), ""]
        for key, item in stage.get("artifacts", {}).items():
            path = Path(item["path"])
            target = os.path.relpath(path, output)
            lines.append(f"- [{key}]({target}) — SHA-256 `{item['sha256']}`")
        lines += ["", "Evidence:", "", "```json", json.dumps(stage.get("evidence", {}), indent=2), "```", ""]
    (output / "report.md").write_text("\n".join(lines))
    links = []
    for stage_id, stage in manifest["stages"].items():
        for key, item in stage.get("artifacts", {}).items():
            if key in {"viewer", "preview", "figure", "manifest", "parameters", "structure", "molecular_graph"}:
                target = html.escape(os.path.relpath(item["path"], output), quote=True)
                links.append(f'<li><a href="{target}">{html.escape(stage_id)} / {html.escape(key)}</a></li>')
    (output / "index.html").write_text("<!doctype html><meta charset='utf-8'><meta name='viewport' content='width=device-width'><title>Research Workbench</title><style>body{max-width:1000px;margin:40px auto;padding:0 24px;font:16px system-ui;color:#173032;background:#fafaf6}h1{font-size:36px}table{width:100%;border-collapse:collapse}td,th{padding:12px;text-align:left;border-bottom:1px solid #d4dfda}a{color:#006c68}li{margin:10px 0}.note{padding:20px;background:#e9f2ef}</style><h1>" + html.escape(manifest["title"]) + "</h1><p class='note'>Execution: <b>" + html.escape(manifest["status"]) + "</b>. Scientific acceptance and human visual approval remain pending unless separately documented. Interactive viewers can be rotated and zoomed.</p><table><tr><th>Stage</th><th>Status</th><th>Finding</th></tr>" + "".join(rows) + "</table><h2>Artifacts</h2><ul>" + "".join(links) + "</ul><p><a href='report.md'>Evidence report</a> · <a href='manifest.json'>Run manifest</a></p>")


def execute_plan(plan: dict, output: Path, skills_root: Path | None = None, registry: Registry | None = None, resume: bool = False, executor=None) -> dict:
    registry = registry or Registry()
    plan = validate_plan(plan, registry)
    skills_root = Path(skills_root or default_skills_root()).resolve()
    output = Path(output).expanduser().resolve()
    sources = {str(path): sha256(path) for stage in plan["steps"] for path in source_files(stage["params"])}
    tools = {stage["capability"]: registry.fingerprint(stage["capability"], skills_root) for stage in plan["steps"]}
    tools["coordinator"] = {p.name: sha256(p) for p in sorted(MODULE_ROOT.glob("*.py"))}
    if resume:
        if not (output / "manifest.json").is_file():
            raise WorkflowError("Missing run manifest")
    else:
        if output.exists():
            raise WorkflowError(f"Output directory already exists: {output}")
        output.mkdir(parents=True)
    lock = output / ".run.lock"
    try:
        handle = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError as error:
        raise WorkflowError("Run is locked. Check the recorded process before removing an abandoned lock") from error
    with os.fdopen(handle, "w") as stream:
        json.dump({"pid": os.getpid(), "started_at": now()}, stream)
    try:
        if resume:
            manifest = json.loads((output / "manifest.json").read_text())
            verify_resume(manifest, plan, sources, tools, output)
        else:
            manifest = {"schema_version": 1, "title": plan["title"], "status": "running", "created_at": now(), "plan_sha256": digest(plan), "source_sha256": sources, "tool_fingerprints": tools, "scientific_acceptance": "pending", "stages": {}}
            write_json(output / "plan.json", plan)
            write_json(output / "manifest.json", manifest)
        dispatch = executor or registry.execute
        for stage in plan["steps"]:
            stage_id = stage["id"]
            if stage_id in manifest["stages"]:
                continue
            blockers = [dep for dep in stage["depends_on"] if manifest["stages"][dep]["status"] != "complete"]
            if blockers:
                manifest["stages"][stage_id] = {"status": "dependency_blocked", "reason": "Required stages did not complete: " + ", ".join(blockers), "artifacts": {}, "evidence": {"scientific_acceptance": "pending"}}
                write_json(output / "manifest.json", manifest)
                continue
            attempt = output / "stages" / stage_id / "attempt-001"
            attempt.mkdir(parents=True)
            entry = {"status": "running", "started_at": now(), "capability": stage["capability"], "operation": stage["operation"], "artifacts": {}}
            manifest["stages"][stage_id] = entry
            write_json(output / "manifest.json", manifest)

            def journal(process):
                entry["process"] = process
                write_json(output / "manifest.json", manifest)

            try:
                params = resolve_refs(stage["params"], manifest["stages"])
                write_json(attempt.parent / "inputs.json", params)
                result = dispatch(stage["capability"], stage["operation"], params, attempt, skills_root, process_runner(attempt, stage["timeout_seconds"], journal))
                if not isinstance(result, dict) or result.get("status") not in {"complete", "blocked", "failed"}:
                    raise WorkflowError("Adapter returned invalid execution status")
                artifacts = {}
                for key, value in result.get("artifacts", {}).items():
                    if not isinstance(key, str) or not isinstance(value, (str, Path)):
                        raise WorkflowError("Adapter artifacts must map names to local file paths")
                    path = Path(value).resolve()
                    if not path.is_file():
                        raise WorkflowError(f"Adapter artifact is missing: {path}")
                    artifacts[key] = {"path": str(path), "sha256": sha256(path)}
                entry.update(status=result["status"], reason=str(result.get("reason", "")), artifacts=artifacts, evidence=result.get("evidence", {}), details=result.get("details", {}), ended_at=now())
                # Execution success never promotes scientific or human acceptance.
                entry["evidence"].setdefault("scientific_acceptance", "pending")
                write_json(attempt / "handoff.json", {"schema_version": 1, "stage": stage_id, "capability": stage["capability"], "operation": stage["operation"], "inputs_sha256": {str(p): sha256(p) for p in source_files(params)}, "artifacts": artifacts, "evidence": entry["evidence"], "details": entry["details"]})
            except Exception as error:
                entry.update(status="failed", reason=f"{type(error).__name__}: {error}", evidence={"scientific_acceptance": "pending"}, ended_at=now())
            write_json(output / "manifest.json", manifest)
        states = {stage["status"] for stage in manifest["stages"].values()}
        manifest["status"] = "failed" if "failed" in states else "blocked" if states & {"blocked", "dependency_blocked"} else "complete"
        changed_sources = [path for path, checksum in sources.items() if not Path(path).is_file() or sha256(Path(path)) != checksum]
        final_tools = {stage["capability"]: registry.fingerprint(stage["capability"], skills_root) for stage in plan["steps"]}
        final_tools["coordinator"] = {p.name: sha256(p) for p in sorted(MODULE_ROOT.glob("*.py"))}
        if changed_sources or final_tools != tools:
            manifest["status"] = "failed"
            manifest["integrity_failure"] = {"changed_sources": changed_sources, "tool_changed": final_tools != tools}
        manifest["updated_at"] = now()
        write_report(output, manifest)
        manifest["output_sha256"] = output_inventory(output)
        write_json(output / "manifest.json", manifest)
        return manifest
    finally:
        lock.unlink(missing_ok=True)
