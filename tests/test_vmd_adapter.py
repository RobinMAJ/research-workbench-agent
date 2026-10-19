"""VMD adapter evidence contracts, including the no-local-VMD paths."""
from pathlib import Path
import hashlib
import json
import struct
import subprocess
import sys
import zlib

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from research_workbench.adapters import execute
from research_workbench import vmd_adapter


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value))


def png():
    def chunk(name, data):
        return struct.pack(">I", len(data)) + name + data + struct.pack(">I", zlib.crc32(name + data))
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 2, 1, 8, 2, 0, 0, 0)) + chunk(b"IDAT", zlib.compress(b"\x00\xff\x00\x00\x00\x00\xff")) + chunk(b"IEND", b"")


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    source = tmp_path / "water.xyz"
    source.write_text("3\nOriginal test geometry, not simulated data\nO 0 0 0\nH .8 0 0\nH 0 .8 0\n")
    output, skills = tmp_path / "attempt", tmp_path / "skills"
    output.mkdir()
    launcher = skills / "vmd-figure-studio/scripts/vmd_studio.py"
    launcher.parent.mkdir(parents=True)
    launcher.write_text("# Mocked CLI; never executed\n")
    runtime = {"python_executable": sys.executable, "vmd_executable": "/explicit/mock/vmd"}
    monkeypatch.setattr(vmd_adapter, "resolve_runtime", lambda skill: dict(runtime))
    spec = {"schema": "vmd-figure-studio-scene-v1", "inputs": [
        {"path": str(source), "type": "xyz", "role": "structure", "expected_atoms": 3}],
        "representations": [{"style": "CPK", "selection": "all", "color": "Element"}],
        "output": {"width_px": 2, "height_px": 1, "dpi": 300, "format": "png"}}
    return output, skills, source, spec, runtime


def native_bundle(argv, edit=None):
    command, request, target = argv[3], Path(argv[4]), Path(argv[5])
    spec = json.loads(request.read_text())
    target.mkdir()
    (target / "inputs").mkdir()
    (target / "scene.vmd").write_text("# Generated declarative test scene\nquit\n")
    inputs = []
    for i, item in enumerate(spec["inputs"]):
        source = Path(item["path"])
        bundled = target / f"inputs/input-{i:03d}.xyz"
        bundled.write_bytes(source.read_bytes())
        inputs.append({"original_path": str(source), "bundled_path": str(bundled.relative_to(target)),
                       "sha256": digest(source), "bytes": source.stat().st_size})
    compiled = {"schema": "vmd-figure-studio-scene-v1", "status": "compiled_unrendered",
                "scene_sha256": digest(target / "scene.vmd"), "inputs": inputs,
                "expected_images": 1, "config": {"output": spec["output"]}}
    write_json(target / "compiled-scene.json", compiled)
    if command == "run":
        (target / "frames").mkdir()
        image = target / "frames/frame-000000.png"
        image.write_bytes(png())
        raw = target / "frames/frame-000000.tga"
        raw.write_bytes(bytes(12) + struct.pack("<HH", 2, 1) + bytes([24, 0]) + b"\x00\x00\xff\xff\x00\x00")
        metadata = {}
        for key, name in (("atom_metadata", "atom-metadata.tsv"), ("representations", "representation-metadata.tsv")):
            (target / name).write_text("Original native metadata fixture\n")
            metadata[key] = {"path": name, "sha256": digest(target / name), "count": 3}
        report = {"schema": "vmd-figure-studio-render-v1", "status": "rendered_verified",
                  "scene_sha256": compiled["scene_sha256"],
                  "processes": [{"returncode": 0, "timed_out": False, "log_limit_exceeded": False}],
                  "images": [{"path": "frames/frame-000000.png", "sha256": digest(image), "bytes": image.stat().st_size,
                              "raw_tga_sha256": digest(raw), "width_px": 2, "height_px": 1,
                              "pixel_extrema": [[0, 255], [0, 0], [0, 255]]}], **metadata}
        write_json(target / "render-manifest.json", report)
    if edit:
        edit(target)
    return target


def native_viewer(argv, edit=None):
    target = Path(argv[5]); target.mkdir()
    records = [{"name": Path(argv[4]).name, "sha256": digest(Path(argv[4]))}]
    fields = json.loads(Path(argv[argv.index("--volumes-json") + 1]).read_text()) if "--volumes-json" in argv else []
    for field in fields:
        records.append({"name": Path(field["path"]).name, "sha256": digest(Path(field["path"]))})
    for name in vmd_adapter.VIEWER_ASSETS - {"viewer-manifest.json", "3Dmol-provenance.json"}:
        (target / name).write_text("Independent local preview fixture\n")
    write_json(target / "3Dmol-provenance.json", {"js_sha256": digest(target / "3Dmol-min.js")})
    write_json(target / "viewer-manifest.json", {"engine": "3Dmol.js test fixture", "vmd_required": False,
               "native_vmd_render": False, "source_files": records,
               "scientific_surfaces": [{key: row[key] for key in ("format", "isovalue", "units", "signed")} for row in fields]})
    if edit:
        edit(target)


def runner(callback, code=0, timed_out=False):
    def run(argv, cwd=None, timeout=600, env=None):
        assert isinstance(argv, list) and all(isinstance(x, str) for x in argv)
        assert argv[1:3] == ["-B", str(Path(cwd) / "scripts/vmd_studio.py")]
        assert 1 <= timeout <= 3600
        callback(argv)
        return {"exit_code": code, "timed_out": timed_out, "stdout_file": None, "stderr_file": None}
    return run


def perform(workspace, operation, params=None, callback=native_bundle, **process):
    output, skills, source, spec, runtime = workspace
    return execute("vmd-figure-studio", operation, params if params is not None else {"spec": spec}, output, skills, runner(callback, **process))


def test_compile_retains_hash_verified_bundle_without_claiming_native_figure(workspace):
    result = perform(workspace, "compile")
    assert result["status"] == "complete"
    assert result["details"]["native_status"] == "compiled_unrendered"
    assert result["details"]["rendering"] == "pending"
    assert set(result["artifacts"]) == {"manifest", "scene", "compiled_manifest"}
    wrapper = json.loads(Path(result["artifacts"]["manifest"]).read_text())
    assert set(wrapper["outputs_sha256"]) == {"scene.vmd", "compiled-scene.json", "inputs/input-000.xyz"}
    assert wrapper["scientific_acceptance"] == "pending"


def test_render_without_native_vmd_blocks_but_retains_portable_scene(workspace):
    workspace[-1]["vmd_executable"] = None
    def compile_only(argv):
        assert argv[3] == "compile" and "--vmd" not in argv
        native_bundle(argv)
    result = perform(workspace, "render", callback=compile_only)
    assert result["status"] == "blocked"
    assert "scene" in result["artifacts"]
    assert "figure" not in result["artifacts"] and "preview" not in result["artifacts"]


def test_native_render_requires_native_receipt_and_image_hashes(workspace):
    def native(argv):
        assert argv[3] == "run" and argv[argv.index("--vmd") + 1] == "/explicit/mock/vmd"
        native_bundle(argv)
    result = perform(workspace, "render", callback=native)
    assert result["status"] == "complete", result
    assert result["details"]["native_status"] == "rendered_verified"
    assert result["details"]["image_count"] == 1
    assert result["details"]["scientific_acceptance"] == "pending"
    assert result["details"]["visual_review"] == "pending"
    assert result["artifacts"]["figure"] == result["artifacts"]["preview"]


@pytest.mark.parametrize("damage", ["tcl", "input", "source", "image", "raw", "metadata", "count", "traversal", "uniform", "unsuccessful", "symlink", "dimensions"])
def test_changed_missing_unsafe_or_incomplete_evidence_cannot_be_promoted(workspace, damage):
    def edit(target):
        if damage in {"tcl", "input", "image", "raw", "metadata"}:
            name = {"tcl": "scene.vmd", "input": "inputs/input-000.xyz", "image": "frames/frame-000000.png",
                    "raw": "frames/frame-000000.tga", "metadata": "atom-metadata.tsv"}[damage]
            with (target / name).open("ab") as stream:
                stream.write(b"changed")
        elif damage == "source":
            workspace[2].write_text("changed source")
        elif damage == "symlink":
            (target / "linked-source.xyz").symlink_to(workspace[2])
        else:
            path = target / "render-manifest.json"; data = json.loads(path.read_text())
            if damage == "count": data["images"] = []
            if damage == "traversal": data["images"][0]["path"] = "../outside.png"
            if damage == "uniform": data["images"][0]["pixel_extrema"] = [[255, 255]] * 3
            if damage == "unsuccessful": data["processes"][0]["timed_out"] = True
            if damage == "dimensions": data["images"][0]["width_px"] = 3
            write_json(path, data)
    result = perform(workspace, "render", callback=lambda argv: native_bundle(argv, edit))
    assert result["status"] == "failed", result
    assert "figure" not in result["artifacts"]


@pytest.mark.parametrize("timed_out,code", [(True, -15), (False, 1)])
def test_failed_native_execution_keeps_partial_bundle_without_promoting_images(workspace, timed_out, code):
    result = perform(workspace, "render", timed_out=timed_out, code=code)
    assert result["status"] == "failed"
    assert set(result["artifacts"]) == {"manifest"}
    assert json.loads(Path(result["artifacts"]["manifest"]).read_text())["status"] == "execution_failed"


def test_viewer_needs_no_vmd_and_retains_complete_asset_bundle(workspace):
    workspace[-1]["vmd_executable"] = None
    result = perform(workspace, "viewer", {"input": str(workspace[2]), "input_format": "xyz"}, callback=native_viewer)
    assert result["status"] == "complete", result
    assert result["details"]["native_vmd_render"] is False
    assert result["details"]["browser_visual_review"] == "pending"
    assert "viewer" in result["artifacts"] and "figure" not in result["artifacts"]
    inventory = json.loads(Path(result["artifacts"]["manifest"]).read_text())["outputs_sha256"]
    assert set(inventory) == vmd_adapter.VIEWER_ASSETS


def test_viewer_signed_field_parameters_and_hashes_are_verified(workspace, tmp_path):
    field = tmp_path / "density.dx"; field.write_text("Original illustrative scalar fixture")
    params = {"input": str(workspace[2]), "volumes": [{"path": str(field), "format": "dx", "isovalue": .05,
               "units": "e/angstrom^3", "signed": True}]}
    result = perform(workspace, "viewer", params, callback=native_viewer)
    assert result["status"] == "complete", result
    assert result["evidence"]["source_files"][1]["sha256"] == digest(field)


@pytest.mark.parametrize("damage", ["asset", "library", "sources", "native_claim", "surfaces"])
def test_incomplete_viewer_assets_or_false_native_claim_are_rejected(workspace, damage):
    def edit(target):
        if damage == "asset": (target / "3Dmol-LICENSE.txt").unlink()
        elif damage == "library": (target / "3Dmol-min.js").write_text("changed")
        else:
            path = target / "viewer-manifest.json"; data = json.loads(path.read_text())
            if damage == "sources": data["source_files"] = []
            if damage == "native_claim": data["native_vmd_render"] = True
            if damage == "surfaces": data["scientific_surfaces"] = [{"isovalue": 1}]
            write_json(path, data)
    result = perform(workspace, "viewer", {"input": str(workspace[2])}, callback=lambda argv: native_viewer(argv, edit))
    assert result["status"] == "failed"
    assert "viewer" not in result["artifacts"]


@pytest.mark.parametrize("operation,params", [
    ("compile", {"spec": "hidden-scene.json"}),
    ("compile", {"spec": {"inputs": [{"path": "relative.xyz"}]}}),
    ("compile", {"spec": {"inputs": [], "api_key": "never-written"}}),
    ("compile", {"spec": {}, "runtime_python": "/bin/python"}),
    ("viewer", {"input": "/missing.xyz", "title": "x"}),
    ("viewer", {"input": "/missing.xyz", "timeout_seconds": True}),
])
def test_invalid_requests_do_not_launch_subprocess_or_write_credentials(workspace, operation, params):
    result = execute("vmd-figure-studio", operation, params, workspace[0], workspace[1],
                     lambda *args, **kwargs: pytest.fail("Invalid request executed"))
    assert result["status"] == "failed"
    assert not (workspace[0] / "request.json").exists()
    assert "never-written" not in json.dumps(result)


def test_missing_python_runtime_blocks_without_writing_request(workspace):
    workspace[-1]["python_executable"] = None
    result = perform(workspace, "compile", callback=lambda argv: pytest.fail("Missing runtime executed"))
    assert result["status"] == "blocked"
    assert not (workspace[0] / "request.json").exists()


def test_render_reserves_cleanup_time_before_coordinator_stage_deadline(workspace):
    def native(argv):
        assert argv[3] == "run"
        assert int(argv[argv.index("--timeout") + 1]) == 12
        native_bundle(argv)
    run = runner(native)
    run.remaining_seconds = lambda: 20.5
    result = execute("vmd-figure-studio", "render", {"spec": workspace[3]}, workspace[0], workspace[1], run)
    assert result["status"] == "complete", result


def test_short_remaining_render_budget_retains_compiled_bundle_and_blocks(workspace):
    def native(argv):
        assert argv[3] == "compile" and "--vmd" not in argv
        native_bundle(argv)
    run = runner(native)
    run.remaining_seconds = lambda: 8
    result = execute("vmd-figure-studio", "render", {"spec": workspace[3]}, workspace[0], workspace[1], run)
    assert result["status"] == "blocked"
    assert "scene" in result["artifacts"] and "figure" not in result["artifacts"]
    assert "remaining stage time" in result["reason"]


def test_exhausted_stage_budget_launches_no_vmd_or_compiler(workspace):
    run = runner(lambda argv: pytest.fail("Expired budget launched a subprocess"))
    run.remaining_seconds = lambda: .5
    result = execute("vmd-figure-studio", "render", {"spec": workspace[3]}, workspace[0], workspace[1], run)
    assert result["status"] == "blocked"
    assert not (workspace[0] / "request.json").exists()


def test_actual_installed_skill_compile_and_independent_viewer(tmp_path):
    """Optional local forward test; never launches VMD or a browser."""
    installed = Path.home() / ".codex/skills/vmd-figure-studio"
    if not (installed / "scripts/vmd_studio.py").is_file():
        pytest.skip("VMD Figure Studio is not installed on this test host")
    runtime = vmd_adapter.resolve_runtime(installed)
    if not runtime.get("python_executable"):
        pytest.skip("Compatible VMD Figure Studio Python is not configured on this host")
    source = tmp_path / "water.xyz"
    source.write_text("3\nOriginal unrelaxed fixture\nO 0 0 0\nH .8 0 0\nH 0 .8 0\n")
    skills = tmp_path / "skills"; skills.mkdir(); (skills / "vmd-figure-studio").symlink_to(installed, target_is_directory=True)
    def run(argv, cwd=None, timeout=600, env=None):
        assert argv[3] in {"compile", "viewer"}
        process = subprocess.run(argv, cwd=cwd, timeout=timeout, capture_output=True, text=True)
        assert process.returncode == 0, process.stderr
        return {"exit_code": process.returncode, "timed_out": False}
    for operation in ("compile", "viewer"):
        output = tmp_path / operation; output.mkdir()
        params = {"input": str(source)} if operation == "viewer" else {"spec": {
            "schema": "vmd-figure-studio-scene-v1", "inputs": [{"path": str(source), "type": "xyz", "role": "structure", "expected_atoms": 3}],
            "representations": [{"style": "CPK", "selection": "all", "color": "Element"}]}}
        result = execute("vmd-figure-studio", operation, params, output, skills, run)
        assert result["status"] == "complete", result
        assert "figure" not in result["artifacts"]
