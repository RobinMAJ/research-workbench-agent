"""Evidence-preserving VMD scenes and independent offline browser previews.

Only the installed Skill compiles Tcl. Plans contain inline declarative scenes,
so all coordinate, topology, trajectory and scalar inputs remain visible to the
coordinator's dependency pinning. A compiled scene is never an exported figure.
"""
from __future__ import annotations

import json
import math
from pathlib import Path
import re
import struct

from .adapters import (_child, _file, _hash, _json, _keys, _process_evidence,
                       _result, _timeout, _wrapped, _write)
from .vmd_runtime import resolve_runtime


MAX_INPUTS = 128
MAX_IMAGES = 10000
MAX_ARTIFACTS = 25000
VIEWER_FORMATS = {"xyz", "pdb", "gro", "sdf", "mol2", "cif", "vasp"}
VIEWER_ASSETS = {"viewer.html", "viewer-manifest.json", "3Dmol-min.js",
                 "3Dmol-LICENSE.txt", "3Dmol-min.js.LICENSE.txt", "3Dmol-provenance.json"}


def _digest(value):
    if not isinstance(value, str) or re.fullmatch(r"[a-f0-9]{64}", value) is None:
        raise ValueError("VMD evidence contains an invalid SHA256")
    return value


def _positive_integer(value, maximum, name):
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= maximum:
        raise ValueError(name + " is outside the bounded positive integer range")
    return value


def _inputs(spec):
    if not isinstance(spec, dict):
        raise ValueError("spec must be an inline native VMD scene object, not a scene filename")
    items = spec.get("inputs")
    if not isinstance(items, list) or not 1 <= len(items) <= MAX_INPUTS:
        raise ValueError("VMD spec requires 1..128 explicit input records")
    records = []
    for item in items:
        if not isinstance(item, dict):
            raise ValueError("VMD scene input records must be objects")
        source = _file(item.get("path"), "spec.inputs[].path")
        records.append({"path": str(source), "sha256": _hash(source)})
    return records


def _unchanged(records):
    if any(not Path(row["path"]).is_file() or _hash(row["path"]) != row["sha256"] for row in records):
        raise ValueError("A VMD source input changed during execution")


def _inventory(directory):
    entries = {}
    count = 0
    for path in sorted(directory.rglob("*")):
        count += 1
        if count > MAX_ARTIFACTS:
            raise ValueError("VMD result exceeds the bounded artifact inventory")
        if path.is_symlink():
            raise ValueError("VMD result bundles cannot contain symlinks")
        if path.is_file():
            relative = str(path.relative_to(directory))
            safe = _child(directory, relative)
            entries[relative] = _hash(safe)
        elif not path.is_dir():
            raise ValueError("VMD result contains an unsupported filesystem entry")
    return entries


def _wrap(directory, status, operation):
    before = _inventory(directory)
    wrapper = _wrapped(directory, status, "vmd." + operation)
    if _json(wrapper).get("outputs_sha256") != before:
        raise ValueError("VMD result changed while its wrapper inventory was written")
    return wrapper


def _compiled(directory, sources, spec):
    path = _child(directory, "compiled-scene.json")
    data = _json(path)
    if data.get("schema") != "vmd-figure-studio-scene-v1" or data.get("status") != "compiled_unrendered":
        raise ValueError("VMD compiler did not retain an accepted portable scene")
    scene = _child(directory, "scene.vmd")
    if _hash(scene) != _digest(data.get("scene_sha256")):
        raise ValueError("Compiled VMD Tcl hash mismatch")
    entries = data.get("inputs")
    if not isinstance(entries, list) or len(entries) != len(sources):
        raise ValueError("Compiled VMD input inventory is incomplete")
    seen = set()
    for entry, source in zip(entries, sources):
        if not isinstance(entry, dict) or entry.get("original_path") != source["path"]:
            raise ValueError("Compiled VMD inputs differ from the pinned scene sources")
        relative = entry.get("bundled_path")
        if not isinstance(relative, str) or not relative.startswith("inputs/") or relative in seen:
            raise ValueError("Compiled VMD input paths are invalid or duplicated")
        seen.add(relative)
        bundled = _child(directory, relative)
        if _digest(entry.get("sha256")) != source["sha256"] or _hash(bundled) != source["sha256"]:
            raise ValueError("Bundled VMD input hash mismatch")
        if bundled.stat().st_size != entry.get("bytes"):
            raise ValueError("Bundled VMD input size mismatch")
    count = _positive_integer(data.get("expected_images"), MAX_IMAGES, "expected_images")
    animation = spec.get("animation", {})
    if not isinstance(animation, dict):
        raise ValueError("VMD animation must be an object")
    frames = animation.get("frames", [0])
    rotation = animation.get("rotation")
    if not isinstance(frames, list) or not frames:
        raise ValueError("VMD animation requires an explicit nonempty frame list")
    expected = rotation.get("count") if isinstance(rotation, dict) else len(frames)
    if count != expected:
        raise ValueError("Compiled VMD frame count differs from the inline scene")
    config = data.get("config")
    if not isinstance(config, dict) or not isinstance(config.get("output"), dict):
        raise ValueError("Compiled VMD normalized display settings are missing")
    return data


def _raster_dimensions(path):
    """Read lossless raster dimensions without adding Pillow to the coordinator."""
    with path.open("rb") as stream:
        head = stream.read(24)
        if path.suffix == ".png" and len(head) == 24 and head[:8] == b"\x89PNG\r\n\x1a\n" and head[12:16] == b"IHDR":
            return struct.unpack(">II", head[16:24])
        if path.suffix == ".tga" and len(head) >= 18 and head[2] in {2, 3, 10, 11} and head[16] in {8, 24, 32}:
            return struct.unpack("<HH", head[12:16])
        if path.suffix == ".tiff" and len(head) >= 8 and head[:2] in {b"II", b"MM"}:
            order = "<" if head[:2] == b"II" else ">"
            if struct.unpack(order + "H", head[2:4])[0] != 42:
                raise ValueError("Only classic TIFF native rasters are supported")
            stream.seek(struct.unpack(order + "I", head[4:8])[0])
            raw_count = stream.read(2)
            if len(raw_count) != 2:
                raise ValueError("Truncated native TIFF directory")
            count = struct.unpack(order + "H", raw_count)[0]
            if count > 4096:
                raise ValueError("Native TIFF directory exceeds its bound")
            dims = {}
            for _ in range(count):
                entry = stream.read(12)
                if len(entry) != 12:
                    raise ValueError("Truncated native TIFF entry")
                tag, kind, number = struct.unpack(order + "HHI", entry[:8])
                if tag in {256, 257}:
                    if number != 1 or kind not in {3, 4}:
                        raise ValueError("Invalid native TIFF dimensions")
                    dims[tag] = struct.unpack(order + ("H" if kind == 3 else "I"), entry[8:10] if kind == 3 else entry[8:12])[0]
            if set(dims) == {256, 257}:
                return dims[256], dims[257]
    raise ValueError("Native VMD image has an invalid or unsupported raster header")


def _rendered(directory, compiled):
    manifest = _child(directory, "render-manifest.json")
    data = _json(manifest)
    if data.get("schema") != "vmd-figure-studio-render-v1" or data.get("status") != "rendered_verified":
        raise ValueError("Native VMD render status is not rendered_verified")
    if data.get("scene_sha256") != compiled["scene_sha256"]:
        raise ValueError("Native render refers to another compiled VMD scene")
    processes = data.get("processes")
    if not isinstance(processes, list) or not processes or len(processes) > MAX_IMAGES + 1:
        raise ValueError("Native VMD render has no bounded subprocess evidence")
    if any(not isinstance(p, dict) or p.get("returncode") != 0 or p.get("timed_out") is not False or p.get("log_limit_exceeded") is not False for p in processes):
        raise ValueError("Native VMD subprocess evidence is not successful")
    images = data.get("images")
    if not isinstance(images, list) or len(images) != compiled["expected_images"]:
        raise ValueError("Native VMD image count differs from the compiled request")
    expected = compiled["config"]["output"]
    width = _positive_integer(expected.get("width_px"), 32768, "width_px")
    height = _positive_integer(expected.get("height_px"), 32768, "height_px")
    suffix = {"png": ".png", "tiff": ".tiff", "tga": ".tga"}.get(expected.get("format"))
    if suffix is None:
        raise ValueError("Unsupported native VMD raster format")
    for i, row in enumerate(images):
        relative = "frames/frame-" + f"{i:06d}" + suffix
        if not isinstance(row, dict) or row.get("path") != relative:
            raise ValueError("Native VMD frame path differs from its expected sequence")
        image = _child(directory, relative)
        if _hash(image) != _digest(row.get("sha256")) or image.stat().st_size != row.get("bytes"):
            raise ValueError("Native VMD figure hash or size mismatch")
        if _raster_dimensions(image) != (width, height) or (row.get("width_px"), row.get("height_px")) != (width, height):
            raise ValueError("Native VMD figure dimensions differ from the compiled scene")
        extrema = row.get("pixel_extrema")
        if not isinstance(extrema, list) or len(extrema) != 3 or any(not isinstance(pair, list) or len(pair) != 2 or any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) for v in pair) for pair in extrema) or all(a == b for a, b in extrema):
            raise ValueError("Native VMD raster has no nonuniform decoded-pixel evidence")
        raw = _child(directory, f"frames/frame-{i:06d}.tga")
        if _hash(raw) != _digest(row.get("raw_tga_sha256")):
            raise ValueError("Native VMD raw raster hash mismatch")
    for key in ("atom_metadata", "representations"):
        row = data.get(key)
        if not isinstance(row, dict) or _hash(_child(directory, row.get("path"))) != _digest(row.get("sha256")):
            raise ValueError("Native VMD metadata hash mismatch")
    return data


def _viewer_params(params):
    _keys(params, {"input", "input_format", "title", "volumes", "timeout_seconds"}, {"input"})
    source = _file(params["input"], "input")
    fmt = params.get("input_format", "xyz")
    if fmt not in VIEWER_FORMATS:
        raise ValueError("Unsupported offline viewer coordinate format")
    title = params.get("title", "Interactive structure inspection")
    if not isinstance(title, str) or not 1 <= len(title) <= 500 or any(ord(c) < 32 for c in title):
        raise ValueError("Offline viewer title must be a bounded single-line string")
    volumes = params.get("volumes", [])
    if not isinstance(volumes, list) or len(volumes) > 32:
        raise ValueError("Offline viewer accepts at most 32 explicit scalar fields")
    records = [{"path": str(source), "sha256": _hash(source)}]
    normalized = []
    for row in volumes:
        _keys(row, {"path", "format", "isovalue", "units", "signed"}, {"path", "format", "isovalue", "units", "signed"})
        field = _file(row["path"], "volumes[].path")
        value = row["isovalue"]
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
            raise ValueError("Offline viewer field isovalue must be a finite positive magnitude")
        if row["format"] not in {"cube", "dx"} or not isinstance(row["signed"], bool):
            raise ValueError("Offline viewer requires cube/dx fields and explicit boolean signed")
        units = row["units"]
        if not isinstance(units, str) or not 1 <= len(units) <= 100 or any(ord(c) < 32 for c in units):
            raise ValueError("Offline viewer fields require bounded explicit scalar units")
        normalized.append({**row, "path": str(field)})
        records.append({"path": str(field), "sha256": _hash(field)})
    return source, fmt, title, normalized, records


def _verify_viewer(directory, records, volumes):
    for relative in VIEWER_ASSETS:
        _child(directory, relative)
    data = _json(directory / "viewer-manifest.json")
    if data.get("vmd_required") is not False or data.get("native_vmd_render") is not False:
        raise ValueError("Offline viewer must explicitly identify its independent preview engine")
    expected = [{"name": Path(row["path"]).name, "sha256": row["sha256"]} for row in records]
    if data.get("source_files") != expected:
        raise ValueError("Offline viewer source hashes differ from its pinned inputs")
    expected_surfaces = [{key: row[key] for key in ("format", "isovalue", "units", "signed")} for row in volumes]
    if data.get("scientific_surfaces") != expected_surfaces:
        raise ValueError("Offline viewer field display parameters differ from the explicit request")
    provenance = _json(directory / "3Dmol-provenance.json")
    if _hash(directory / "3Dmol-min.js") != _digest(provenance.get("js_sha256")):
        raise ValueError("Offline viewer library hash differs from its pinned provenance")
    return data


def execute_vmd(operation, params, output, skills, run_process):
    """Execute one VMD operation; common dispatcher converts validation failures."""
    if operation not in {"compile", "render", "viewer"}:
        raise ValueError("Unsupported VMD operation")
    if operation == "viewer":
        source, fmt, title, volumes, sources = _viewer_params(params)
        spec = None
    else:
        _keys(params, {"spec", "timeout_seconds"}, {"spec"})
        spec = params["spec"]
        sources = _inputs(spec)
    timeout = _timeout(params)
    remaining = getattr(run_process, "remaining_seconds", None)
    if callable(remaining):
        timeout = min(float(timeout), max(0.0, float(remaining())))
    if timeout < 1:
        return _result("blocked", "No stage time remains for a bounded VMD operation",
                       details={"scientific_acceptance": "pending"})
    skill = skills / "vmd-figure-studio"
    launcher = skill / "scripts" / "vmd_studio.py"
    if not launcher.is_file():
        return _result("blocked", "VMD Figure Studio Skill is not installed", details={"scientific_acceptance": "pending"})
    runtime = resolve_runtime(skill)
    python = runtime.get("python_executable")
    if not python:
        return _result("blocked", "No compatible Python runtime is configured for VMD Figure Studio",
                       evidence={"runtime_errors": runtime.get("errors", [])}, details={"scientific_acceptance": "pending"})
    target = output / "result"
    argv = [str(python), "-B", str(launcher)]
    native_available = bool(runtime.get("vmd_executable"))
    render_now = operation == "render" and native_available and timeout >= 10
    if operation == "viewer":
        argv += ["viewer", str(source), str(target), "--input-format", fmt, "--title", title]
        if volumes:
            path = output / "volumes.json"
            # The native CLI accepts a JSON list, whereas coordinator evidence
            # helpers deliberately read objects. Keep the explicit list here.
            with path.open("x") as stream:
                stream.write(json.dumps(volumes, indent=2, allow_nan=False) + "\n")
            argv += ["--volumes-json", str(path)]
    else:
        request = output / "request.json"
        _write(request, spec)
        command = "run" if render_now else "compile"
        argv += [command, str(request), str(target)]
        if command == "run":
            # The native renderer owns its own process group. Let its deadline
            # expire first, with time for cleanup before the outer coordinator
            # deadline can terminate the CLI.
            argv += ["--vmd", str(runtime["vmd_executable"]), "--timeout", str(max(1, math.floor(timeout) - 8))]
    process = run_process(argv, cwd=skill, timeout=timeout)
    evidence = {**_process_evidence(process), "source_files": sources,
                "python_executable": str(python), "native_vmd_available": native_available}
    _unchanged(sources)
    if not target.is_dir() or target.is_symlink():
        return _result("failed", "VMD operation did not retain a result bundle", evidence=evidence,
                       details={"scientific_acceptance": "pending"})
    # Reject unsafe output entries even for failed native attempts. Preserve
    # otherwise safe partial evidence without exposing it as a figure/viewer.
    _inventory(target)
    if process.get("timed_out") or process.get("exit_code") != 0:
        wrapper = _wrap(target, "execution_failed", operation)
        return _result("failed", "VMD operation timed out or returned unsuccessful execution; partial evidence retained",
                       {"manifest": str(wrapper)}, evidence,
                       {"scientific_acceptance": "pending", "visual_review": "pending"})
    if operation == "viewer":
        report = _verify_viewer(target, sources, volumes)
        wrapper = _wrap(target, "independent_preview_created", operation)
        return _result("complete", "Independent offline 3Dmol preview bundle created; browser visual review remains pending",
                       {"manifest": str(wrapper), "native_manifest": str(target / "viewer-manifest.json"), "viewer": str(target / "viewer.html")},
                       {**evidence, "manifest_sha256": _hash(wrapper)},
                       {"native_status": "independent_preview_created", "engine": report.get("engine"),
                        "native_vmd_render": False, "browser_visual_review": "pending", "visual_review": "pending", "scientific_acceptance": "pending"})
    compiled = _compiled(target, sources, spec)
    artifacts = {"scene": str(target / "scene.vmd"), "compiled_manifest": str(target / "compiled-scene.json")}
    details = {"native_status": "compiled_unrendered", "rendering": "pending", "visual_review": "pending", "scientific_acceptance": "pending"}
    if render_now:
        report = _rendered(target, compiled)
        details.update(native_status="rendered_verified", rendering="complete", image_count=len(report["images"]))
        artifacts.update(native_manifest=str(target / "render-manifest.json"),
                         figure=str(target / report["images"][0]["path"]), preview=str(target / report["images"][0]["path"]))
    wrapper = _wrap(target, details["native_status"], operation)
    artifacts["manifest"] = str(wrapper)
    evidence.update(manifest_sha256=_hash(wrapper), scene_sha256=compiled["scene_sha256"], expected_images=compiled["expected_images"])
    missing = operation == "render" and not native_available
    deferred = operation == "render" and not render_now
    return _result("blocked" if deferred else "complete",
                   "Native VMD unavailable; verified portable scene retained for an authorized VMD machine" if missing else
                   "Insufficient remaining stage time for native rendering and cleanup; verified portable scene retained" if deferred else
                   "VMD produced verified native rasters; visual review remains pending" if operation == "render" else
                   "Portable VMD scene compiled; native rendering remains pending",
                   artifacts, evidence, details)
