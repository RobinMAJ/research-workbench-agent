"""Bounded adapters for existing local scientific skills; no shell interpolation."""
from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import struct


MAX_JSON_BYTES = 16 * 1024 * 1024
OPERATIONS = {
    "atomistic-structure-builder": {"build", "audit", "mp-fetch", "mp-select"},
    "forcefield-generator": {"prepare", "generate", "audit", "audit-mixture", "handoff"},
    "vesta-figure-maker": {"render"},
    "vmd-figure-studio": {"compile", "render", "viewer"},
    "visualization": {"collect"},
}


def _hash(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _json(path):
    path = Path(path)
    if path.stat().st_size > MAX_JSON_BYTES:
        raise ValueError("JSON evidence exceeds the bounded file-size limit")
    value = json.loads(path.read_text())
    if not isinstance(value, dict):
        raise ValueError("JSON evidence must be an object")
    return value


def _write(path, value):
    with Path(path).open("x") as stream:
        stream.write(json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n")


def _keys(params, allowed, required=()):
    if not isinstance(params, dict) or set(params) - set(allowed):
        raise ValueError("Unsupported adapter parameter fields")
    if set(required) - set(params):
        raise ValueError("Missing required adapter parameter fields: " + ", ".join(sorted(set(required) - set(params))))
    _no_credentials(params)


def _no_credentials(value):
    if isinstance(value, dict):
        for key, child in value.items():
            if re.fullmatch(r"(?:api[_-]?key|token|password|secret|credentials?|authorization)", str(key), re.I):
                raise ValueError("Credentials must stay outside plan/spec files; use the skill's credential environment")
            _no_credentials(child)
    elif isinstance(value, list):
        for child in value:
            _no_credentials(child)


def _file(value, name):
    if not isinstance(value, str) or not value or not Path(value).is_absolute():
        raise ValueError(name + " must be an absolute file path resolved by the coordinator")
    path = Path(value).resolve()
    if not path.is_file():
        raise ValueError(name + " file is missing")
    return path


def _timeout(params):
    value = params.get("timeout_seconds", 600)
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 3600:
        raise ValueError("timeout_seconds must be an integer in 1..3600")
    return value


def _runtime(params, skill, variable):
    chosen = os.environ.get(variable)
    if chosen:
        path = Path(chosen).expanduser()
        if not path.is_absolute() or not path.is_file() or not os.access(path, os.X_OK):
            raise ValueError("A runtime override must be an absolute executable path")
        # Resolving bin/python symlinks can select the global interpreter and
        # discard a venv's site-packages. Preserve the lexical executable path.
        return str(path.absolute())
    local = skill / ".venv" / "bin" / "python"
    if local.is_file() and os.access(local, os.X_OK):
        return str(local.absolute())
    found = shutil.which("python3")
    if not found:
        raise ValueError("No compatible Python runtime discovered; set the skill runtime override")
    return str(Path(found).absolute())


def _child(root, relative):
    if not isinstance(relative, str) or not relative or "\\" in relative or any(ord(c) < 32 for c in relative):
        raise ValueError("Unsafe native artifact name")
    raw = Path(relative)
    if raw.is_absolute() or ".." in raw.parts:
        raise ValueError("Native artifact escapes its result directory")
    root = Path(root).resolve()
    path = root / raw
    cursor = path
    while cursor != root:
        if cursor.is_symlink():
            raise ValueError("Native evidence symlinks are unsupported")
        cursor = cursor.parent
    path = path.resolve()
    if not path.is_relative_to(root) or not path.is_file():
        raise ValueError("Native artifact is missing or outside its result directory")
    return path


def _verify_hashes(manifest_path, field):
    data = _json(manifest_path)
    entries = data.get(field)
    if not isinstance(entries, dict) or not entries or len(entries) > 20000:
        raise ValueError("Native manifest has no bounded artifact hash inventory")
    verified = {}
    for relative, expected in entries.items():
        if not isinstance(expected, str) or not re.fullmatch(r"[a-f0-9]{64}", expected):
            raise ValueError("Native artifact SHA256 is invalid")
        path = _child(Path(manifest_path).parent, relative)
        if _hash(path) != expected:
            raise ValueError("Native artifact hash mismatch: " + relative)
        verified[relative] = str(path)
    return data, verified


def _artifact(verified, relative):
    if relative not in verified:
        raise ValueError("Exposed artifact is absent from the verified hash inventory: " + relative)
    return verified[relative]


def _result(status, reason, artifacts=None, evidence=None, details=None):
    return {"status": status, "reason": reason, "artifacts": artifacts or {},
            "evidence": evidence or {}, "details": details or {}}


def _process_evidence(process):
    return {key: process.get(key) for key in ("exit_code", "stdout_file", "stderr_file", "timed_out")}


def _wrapped(directory, native_status, operation):
    """Wrap skills that deliberately produce checks without a native manifest."""
    path = directory / "adapter-manifest.json"
    hashes = {str(p.relative_to(directory)): _hash(p) for p in sorted(directory.rglob("*")) if p.is_file()}
    _write(path, {"schema_version": 1, "kind": "adapter_wrapper", "native_operation": operation,
                  "status": native_status, "scientific_acceptance": "pending", "outputs_sha256": hashes})
    return path


def _selected_structure(manifest_path, candidate):
    data, verified = _verify_hashes(manifest_path, "output_sha256")
    if data.get("schema_version") != 1 or data.get("status") != "complete":
        return _result("blocked", "Native structure run is not complete", {"manifest": str(manifest_path)},
                       {"manifest_sha256": _hash(manifest_path)}, {"native_status": data.get("status")})
    if not isinstance(candidate, str) or not candidate:
        raise ValueError("Select an explicit candidate label; the adapter never chooses the first candidate")
    matches = [row for row in data.get("candidates", []) if row.get("label") == candidate]
    if len(matches) != 1:
        raise ValueError("Selected candidate is missing or ambiguous in the native manifest")
    row = matches[0]
    if row.get("status") != "geometry_screen_pass":
        return _result("blocked", "Selected structure failed its geometry screen", {"manifest": str(manifest_path)},
                       {"manifest_sha256": _hash(manifest_path)}, {"native_status": row.get("status")})
    directory = row.get("directory")
    if not isinstance(directory, str) or directory != candidate or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,120}", directory):
        raise ValueError("Unsafe candidate directory")
    artifacts = {"manifest": str(manifest_path), "structure": _artifact(verified, directory + "/structure.json"),
                 "geometry": _artifact(verified, directory + "/structure.extxyz"),
                 "structure_cif": _artifact(verified, directory + "/structure.cif"),
                 "poscar": _artifact(verified, directory + "/POSCAR"),
                 "atom_map": _artifact(verified, directory + "/atom-map.json"),
                 "build_metadata": _artifact(verified, directory + "/build-metadata.json"),
                 "checks": _artifact(verified, directory + "/checks.json"),
                 "export_checks": _artifact(verified, directory + "/export-checks.json")}
    for key, name in (("viewer", "viewer.html"), ("preview", "preview.png")):
        relative = directory + "/" + name
        if relative in verified:
            artifacts[key] = verified[relative]
    if _json(artifacts["checks"]).get("status") != "geometry_screen_pass" or _json(artifacts["export_checks"]).get("status") != "geometry_screen_pass":
        raise ValueError("Candidate check statuses disagree with the native manifest")
    return _result("complete", "Explicit candidate passed its configured geometry and export screens", artifacts,
                   {"manifest_sha256": _hash(manifest_path), "verified_artifact_count": len(verified)},
                   {"candidate": candidate, "scientific_state": data.get("scientific_state"),
                    "visual_review": "pending", "energetic_stability": "not_evaluated"})


def _collect(params):
    _keys(params, {"source_manifest", "candidate"}, {"source_manifest"})
    manifest = _file(params["source_manifest"], "source_manifest")
    data = _json(manifest)
    if "output_sha256" in data:
        result = _selected_structure(manifest, params.get("candidate"))
    elif "outputs_sha256" in data:
        if params.get("candidate") is not None:
            raise ValueError("Molecular manifests have no selectable structure candidates")
        data, verified = _verify_hashes(manifest, "outputs_sha256")
        if data.get("status") not in {"parameter_contract_pass", "molecular_preflight_pass", "native_topology_readable"}:
            return _result("blocked", "Source manifest does not declare a technically accepted molecular result",
                           {"manifest": str(manifest)}, {"manifest_sha256": _hash(manifest)})
        artifacts = {"manifest": str(manifest)}
        for key, relative in (("viewer", "viewer.html"), ("molecular_graph", "input.sdf"), ("parameters", "parameters.json")):
            if relative in verified:
                artifacts[key] = verified[relative]
        result = _result("complete", "Verified visualization references retained with their original evidence bundle", artifacts,
                         {"manifest_sha256": _hash(manifest), "verified_artifact_count": len(verified)},
                         {"scientific_acceptance": "pending", "visual_review": "pending"})
    else:
        raise ValueError("Visualization collection requires a supported native hash manifest")
    if result["status"] == "complete" and "viewer" not in result["artifacts"]:
        return _result("blocked", "Verified result contains no viewer artifact", result["artifacts"], result["evidence"], result["details"])
    result["details"]["collection_policy"] = "reference original verified bundle; HTML is never copied in isolation"
    return result


def _skill_request(capability, operation, params, output, skills, run_process):
    basic = {"timeout_seconds"}
    if operation == "build":
        _keys(params, basic | {"spec", "candidate"}, {"spec", "candidate"})
    elif operation == "audit" and capability == "atomistic-structure-builder":
        _keys(params, basic | {"input", "slab", "max_atoms", "min_distance", "radius_scale", "min_vacuum"}, {"input"})
    elif operation == "mp-fetch":
        _keys(params, basic | {"query"}, {"query"})
    elif operation == "mp-select":
        _keys(params, basic | {"source_manifest", "material_id", "reason"}, {"source_manifest", "material_id", "reason"})
    else:
        _keys(params, basic | {"spec"}, {"spec"})
    skill = skills / capability
    launcher = skill / "scripts" / "run.sh"
    if not launcher.is_file():
        return _result("blocked", "Required local skill launcher is unavailable")
    variable = "ATOMISTIC_BUILDER_PYTHON" if capability == "atomistic-structure-builder" else "FF_GENERATOR_PYTHON"
    env = {variable: _runtime(params, skill, variable)}
    if operation == "mp-fetch" and not os.environ.get("MP_API_KEY"):
        return _result("blocked", "Materials Project acquisition needs a locally configured MP_API_KEY; interactive credential prompts are disabled in the workbench",
                       evidence={"credential_available": False, "api_key_help_url": "https://next-gen.materialsproject.org/api"})
    target = output / "result"
    argv = ["/bin/sh", str(launcher), operation]
    if operation == "audit" and capability == "atomistic-structure-builder":
        argv.append(str(_file(params["input"], "input")))
        for key in ("max_atoms", "min_distance", "radius_scale", "min_vacuum"):
            if key in params:
                value = params[key]
                if isinstance(value, bool) or not isinstance(value, (float, int)) or not math.isfinite(value):
                    raise ValueError("Geometry audit thresholds must be finite numeric values")
                if key == "max_atoms" and (not isinstance(value, int) or not 1 <= value <= 2000):
                    raise ValueError("Geometry audit max_atoms must be an integer in 1..2000")
                if key != "max_atoms" and (value < 0 or (key == "min_distance" and value == 0)):
                    raise ValueError("Geometry audit distance thresholds are invalid")
                argv.extend(["--" + key.replace("_", "-"), str(value)])
        if params.get("slab") is True:
            argv.append("--slab")
        elif params.get("slab", False) is not False:
            raise ValueError("slab must be boolean")
    elif operation == "mp-select":
        argv.extend(["--manifest", str(_file(params["source_manifest"], "source_manifest")),
                     "--material-id", str(params["material_id"]), "--reason", str(params["reason"])])
    else:
        field = "query" if operation == "mp-fetch" else "spec"
        if not isinstance(params[field], dict):
            raise ValueError(field + " must be a structured JSON object")
        request = output / ("query.json" if field == "query" else "request.json")
        _write(request, params[field])
        argv.extend(["--" + field, str(request)])
    argv.extend(["--output", str(target)])
    process = run_process(argv, cwd=skill, timeout=_timeout(params), env=env)
    evidence = _process_evidence(process)
    if process.get("timed_out"):
        return _result("failed", "Skill subprocess exceeded its declared time budget", evidence=evidence)
    code = process.get("exit_code")
    if operation == "audit-mixture":
        checks_path = target / "checks.json"
        if not checks_path.is_file():
            return _result("failed", "Mixture auditor produced no checks", evidence=evidence)
        report = _json(checks_path)
        accepted = report.get("status") == "compatibility_metadata_pass" and code == 0
        blocked = report.get("status") == "compatibility_blocked" and code == 3
        wrapper = _wrapped(target, report.get("status"), "forcefield.audit-mixture")
        return _result("complete" if accepted else "blocked" if blocked else "failed",
                       "Compatibility metadata passed; assembled physics remains unevaluated" if accepted else "Mixture remains incomplete or incompatible",
                       {"manifest": str(wrapper), "checks": str(checks_path)}, evidence,
                       {"native_status": report.get("status"), "problems": report.get("problems", []), "scientific_acceptance": "pending"})
    if operation == "audit" and capability == "atomistic-structure-builder":
        checks_path = target / "checks.json"
        if not checks_path.is_file():
            return _result("failed", "Geometry auditor produced no checks", evidence=evidence)
        report = _json(checks_path)
        accepted = code == 0 and report.get("status") == "geometry_screen_pass"
        wrapper = _wrapped(target, report.get("status"), "structure.audit")
        artifacts = {"manifest": str(wrapper), "checks": str(checks_path)}
        for key, name in (("viewer", "viewer.html"), ("preview", "preview.png")):
            if (target / name).is_file():
                artifacts[key] = str(target / name)
        return _result("complete" if accepted else "blocked" if code == 1 and report.get("status") == "rejected" else "failed",
                       "Configured geometry screen completed" if accepted else "Geometry screen did not pass", artifacts, evidence,
                       {"scientific_state": report.get("scientific_state"), "visual_review": "pending"})
    if code != 0:
        artifacts = {"manifest": str(target / "manifest.json")} if (target / "manifest.json").is_file() else {}
        if operation == "build" and artifacts and _json(artifacts["manifest"]).get("status") == "all_candidates_rejected":
            return _result("blocked", "Every native structure candidate failed the configured geometry screen", artifacts, evidence)
        return _result("failed", "Native skill returned a failed execution or rejected all candidates", artifacts, evidence,
                       {"native_acceptance": "not_promoted"})
    if operation == "build":
        result = _selected_structure(target / "manifest.json", params["candidate"])
        result["evidence"].update(evidence)
        return result
    if operation == "mp-fetch":
        manifest = target / "retrieval.json"
        report = _json(manifest)
        for candidate in report.get("candidates", []):
            directory = candidate.get("candidate_directory")
            if not isinstance(directory, str) or not re.fullmatch(r"mp-(?:[0-9]+|[a-z]+)", directory) or len(directory) > 64:
                raise ValueError("Unsafe MP candidate directory")
            provenance = _child(target, directory + "/mp-provenance.json")
            data, _ = _verify_hashes(provenance, "files_sha256")
            if data.get("material_id") != candidate.get("material_id") or data.get("selection_status") != "provisional" or data.get("export_complete") is not True:
                raise ValueError("Retrieved candidate provenance is incomplete")
            if candidate.get("structure_sha256") != data.get("files_sha256", {}).get("structure.json"):
                raise ValueError("Retrieval manifest disagrees with candidate structure hash")
        status = "complete" if report.get("status") == "requires_explicit_material_id_selection" else "blocked"
        return _result(status, "Crystal acquisition retained provisional choices; explicit ID selection is still required",
                       {"manifest": str(manifest)}, {**evidence, "manifest_sha256": _hash(manifest)},
                       {"native_status": report.get("status"), "candidate_count": len(report.get("candidates", [])),
                        "ambiguity": report.get("ambiguity"), "selection_status": "provisional"})
    if operation == "mp-select":
        provenance = target / "mp-provenance.json"
        report, verified = _verify_hashes(provenance, "files_sha256")
        selection = target / "source-selection.json"
        if report.get("material_id") != params["material_id"] or report.get("selection_status") != "selected" or report.get("export_complete") is not True or _hash(selection) != report.get("selection_record_sha256"):
            raise ValueError("MP selection is incomplete or its source-selection evidence changed")
        return _result("complete", "Explicitly selected database starting geometry retained with provenance",
                       {"manifest": str(provenance), "selection_record": str(selection), "structure": _artifact(verified, "structure.json"),
                        "structure_cif": _artifact(verified, "structure.cif"), "viewer": _artifact(verified, "viewer.html")},
                       {**evidence, "manifest_sha256": _hash(provenance)}, {"material_id": report.get("material_id"),
                        "selection_status": "selected", "scientific_acceptance": "pending"})
    manifest = target / "manifest.json"
    data, verified = _verify_hashes(manifest, "outputs_sha256")
    expected = {"prepare": "molecular_preflight_pass", "generate": "parameter_contract_pass", "audit": "native_topology_readable"}[operation]
    if data.get("status") != expected:
        return _result("failed", "Molecular manifest does not match the requested accepted operation", {"manifest": str(manifest)}, evidence)
    artifacts = {"manifest": str(manifest)}
    required = {"prepare": ["input.sdf", "atom-map.json"],
                "generate": ["input.sdf", "atom-map.json", "parameters.json", "checks.json", "export-checks.json", "reference-system.xml"],
                "audit": ["checks.json"]}[operation]
    for relative in required:
        _artifact(verified, relative)
    if operation == "generate":
        if _json(verified["checks.json"]).get("status") != "parameter_contract_pass":
            raise ValueError("Parameter checks disagree with the accepted molecular manifest")
        expected_exports = params["spec"].get("exports", ["openmm", "amber", "gromacs"])
        report = _json(verified["export-checks.json"])
        if set(data.get("exports", [])) != set(expected_exports) or set(report) != set(expected_exports):
            raise ValueError("Requested molecular exports are incomplete")
        for name, files in {"openmm": ["system.xml"], "amber": ["molecule.prmtop", "molecule.inpcrd"],
                            "gromacs": ["molecule.top", "molecule.gro"]}.items():
            if name in expected_exports:
                if report[name].get("status") != "openmm_reader_roundtrip_pass":
                    raise ValueError("A requested molecular export failed its roundtrip gate")
                for relative in files:
                    _artifact(verified, relative)
    for key, relative in (("molecular_graph", "input.sdf"), ("atom_map", "atom-map.json"), ("parameters", "parameters.json"),
                          ("checks", "checks.json"), ("export_checks", "export-checks.json"), ("viewer", "viewer.html"),
                          ("openmm", "system.xml"), ("amber_topology", "molecule.prmtop"), ("amber_coordinates", "molecule.inpcrd"),
                          ("gromacs_topology", "molecule.top"), ("gromacs_coordinates", "molecule.gro")):
        if relative in verified:
            artifacts[key] = verified[relative]
    return _result("complete", "Native molecular technical checks passed", artifacts,
                   {**evidence, "manifest_sha256": _hash(manifest), "verified_artifact_count": len(verified)},
                   {"native_status": data.get("status"), "scientific_acceptance": "pending", "visual_review": "pending"})


def _handoff(params, output, skills, run_process):
    _keys(params, {"graph_sdf", "geometry", "atom_map", "timeout_seconds"}, {"graph_sdf", "geometry"})
    graph = _file(params["graph_sdf"], "graph_sdf")
    geometry = _file(params["geometry"], "geometry")
    argv = [_runtime(params, skills / "forcefield-generator", "FF_GENERATOR_PYTHON"), "-B",
            str(Path(__file__).with_name("mapping_worker.py")), "--graph-sdf", str(graph), "--geometry", str(geometry),
            "--output", str(output / "result")]
    if "atom_map" in params:
        argv.extend(["--atom-map", str(_file(params["atom_map"], "atom_map"))])
    process = run_process(argv, cwd=output, timeout=_timeout(params))
    evidence = _process_evidence(process)
    manifest = output / "result" / "manifest.json"
    if process.get("timed_out") or process.get("exit_code") != 0:
        return _result("failed", "Molecular graph/coordinate handoff failed; no topology was inferred",
                       {"manifest": str(manifest)} if manifest.is_file() else {}, evidence)
    data, verified = _verify_hashes(manifest, "outputs_sha256")
    if data.get("status") != "molecular_handoff_pass":
        raise ValueError("Handoff worker did not verify molecular identity")
    return _result("complete", "Authoritative SDF graph retained with verified mapped coordinates",
                   {"manifest": str(manifest), "molecular_graph": _artifact(verified, "positioned.sdf"),
                    "mapping_evidence": _artifact(verified, "mapping-evidence.json")},
                   {**evidence, "manifest_sha256": _hash(manifest)},
                   {"scientific_acceptance": "pending", "bond_inference": "none", "atom_count": data.get("atom_count")})


def _vesta(params, output, skills, run_process):
    _keys(params, {"scene", "scale", "timeout_seconds"}, {"scene"})
    scene = _file(params["scene"], "scene")
    if scene.suffix.lower() not in {".vesta", ".cif", ".mcif", ".vasp"} and scene.name not in {"POSCAR", "CONTCAR"}:
        raise ValueError("VESTA render expects an embedded scene or explicit periodic structure file")
    if scene.suffix.lower() == ".vesta" and re.search(r"(?m)^\s*IMPORT_(?:STRUCTURE|DENSITY|TEXTURE|ORFFE)\b", scene.read_text()):
        return _result("blocked", "Linked VESTA scenes require an explicit dependency-bundle adapter; use an embedded scene")
    scale = params.get("scale", 4)
    if isinstance(scale, bool) or not isinstance(scale, int) or not 1 <= scale <= 8:
        raise ValueError("VESTA scale must be an integer in 1..8")
    launcher = skills / "vesta-figure-maker" / "scripts" / "vesta_export.sh"
    if not launcher.is_file():
        return _result("blocked", "VESTA export skill is unavailable")
    target = output / "result"
    target.mkdir()
    before = _hash(scene)
    image = target / "figure.png"
    timeout = _timeout(params)
    process = run_process(["/bin/zsh", str(launcher), str(scene), str(image), str(scale)], cwd=scene.parent,
                          timeout=timeout, env={"VESTA_EXPORT_TIMEOUT_SECONDS": str(max(1, min(90, timeout - 5)))})
    if process.get("timed_out") or process.get("exit_code") != 0:
        return _result("blocked" if process.get("exit_code") == 4 else "failed", "VESTA image export did not complete", evidence=_process_evidence(process))
    if _hash(scene) != before:
        raise ValueError("VESTA input changed during rendering")
    with image.open("rb") as stream:
        header = stream.read(24)
    if len(header) != 24 or header[:8] != b"\x89PNG\r\n\x1a\n" or header[12:16] != b"IHDR":
        raise ValueError("VESTA export is not a valid PNG header")
    width, height = struct.unpack(">II", header[16:24])
    if not width or not height:
        raise ValueError("VESTA image dimensions are empty")
    manifest = _wrapped(target, "image_export_pass", "vesta.render")
    return _result("complete", "VESTA exported a nonempty raster; visual review remains pending",
                   {"manifest": str(manifest), "figure": str(image), "preview": str(image)},
                   {**_process_evidence(process), "source_sha256": before, "figure_sha256": _hash(image)},
                   {"width_pixels": width, "height_pixels": height, "scale": scale,
                    "scene_format": scene.suffix.lower(), "visual_review": "pending", "scientific_acceptance": "pending"})


def execute(capability: str, operation: str, params: dict, output_dir: Path, skills_root: Path, run_process) -> dict:
    """Execute one approved capability operation and retain bounded native evidence."""
    try:
        if operation not in OPERATIONS.get(capability, set()):
            raise ValueError("Unsupported capability operation")
        output = Path(output_dir).resolve()
        if not output.is_dir() or any(output.iterdir()):
            raise ValueError("Adapter output must be an existing empty attempt directory")
        skills = Path(skills_root).expanduser().resolve()
        if capability == "visualization":
            return _collect(params)
        if capability == "vesta-figure-maker":
            return _vesta(params, output, skills, run_process)
        if capability == "vmd-figure-studio":
            from .vmd_adapter import execute_vmd
            return execute_vmd(operation, params, output, skills, run_process)
        if operation == "handoff":
            return _handoff(params, output, skills, run_process)
        return _skill_request(capability, operation, params, output, skills, run_process)
    except (ValueError, OSError, KeyError, TypeError) as error:
        return _result("failed", str(error), details={"error_type": type(error).__name__, "scientific_acceptance": "pending"})
