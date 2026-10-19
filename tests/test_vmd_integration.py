"""Runtime discovery, planner readiness and immutable VMD handoffs."""
from pathlib import Path
import json
import os
import sys
from unittest import mock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from research_workbench.core import WorkflowError, execute_plan, process_runner
from research_workbench.registry import Registry
from research_workbench.vmd_runtime import resolve_runtime, visualizer_readiness


@pytest.fixture
def runtime_home(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.delenv("VMD_STUDIO_PYTHON", raising=False)
    monkeypatch.delenv("VMD_EXECUTABLE", raising=False)
    monkeypatch.setenv("PATH", "")
    skill = tmp_path / "skills" / "vmd-figure-studio"
    (skill / "scripts").mkdir(parents=True)
    (skill / "scripts/vmd_studio.py").write_text("# Contract fixture; never executed\n")
    (skill / "SKILL.md").write_text("VMD test instructions\n")
    vendor = skill / "assets/vendor"; vendor.mkdir(parents=True)
    (vendor / "3Dmol-min.js").write_text("// vendor fingerprint fixture\n")
    py = skill / ".venv/bin/python"; py.parent.mkdir(parents=True)
    py.symlink_to(sys.executable)
    return skill


def test_venv_path_is_preserved_and_readiness_is_not_a_render(runtime_home):
    runtime = resolve_runtime(runtime_home)
    assert runtime["python_executable"] == str(runtime_home / ".venv/bin/python")
    readiness = visualizer_readiness(runtime_home.parent)["vmd-figure-studio"]
    assert readiness["portable_compile_available"]
    assert readiness["offline_viewer_available"]
    assert readiness["discovery_only"]


def test_configured_app_uses_native_binary_and_never_graphical_launcher(runtime_home, tmp_path):
    app = tmp_path / "VMD with spaces.app"
    binary = app / "Contents/vmd/vmd_MACOSXARM64"
    binary.parent.mkdir(parents=True); binary.write_text("native fixture"); binary.chmod(0o700)
    settings = Path(os.environ["XDG_CONFIG_HOME"]) / "vmd-figure-studio/runtime.json"
    settings.parent.mkdir(parents=True)
    settings.write_text(json.dumps({"vmd_executable": str(app), "unrelated_setting": "DO_NOT_COPY_THIS_VALUE"}))
    runtime = resolve_runtime(runtime_home)
    assert runtime["vmd_executable"] == str(binary)
    assert "DO_NOT_COPY_THIS_VALUE" not in json.dumps(runtime)


def test_invalid_explicit_python_does_not_silently_use_another_environment(runtime_home, monkeypatch):
    monkeypatch.setenv("VMD_STUDIO_PYTHON", "/missing/explicit-python")
    result = resolve_runtime(runtime_home)
    assert result["python_executable"] is None
    assert result["errors"]
    assert not visualizer_readiness(runtime_home.parent)["vmd-figure-studio"]["portable_compile_available"]


def test_invalid_explicit_native_path_does_not_fall_back(runtime_home, monkeypatch):
    monkeypatch.setenv("VMD_EXECUTABLE", "/missing/explicit-vmd")
    result = resolve_runtime(runtime_home)
    assert result["vmd_executable"] is None
    assert result["vmd_source"] == "VMD_EXECUTABLE"


def test_malformed_local_settings_are_reported_without_echoing_content(runtime_home):
    settings = Path(os.environ["XDG_CONFIG_HOME"]) / "vmd-figure-studio/runtime.json"
    settings.parent.mkdir(parents=True)
    settings.write_text("DO_NOT_ECHO_BAD_SETTINGS")
    result = resolve_runtime(runtime_home)
    assert result["errors"]
    assert "DO_NOT_ECHO_BAD_SETTINGS" not in json.dumps(result)


def test_stage_remaining_budget_is_available_without_starting_a_process(tmp_path):
    with mock.patch("research_workbench.core.time.monotonic", return_value=100):
        run = process_runner(tmp_path, 20)
    with mock.patch("research_workbench.core.time.monotonic", return_value=112.5):
        assert run.remaining_seconds() == 7.5
    assert not list(tmp_path.iterdir())


def _compile_plan(source):
    return {"schema_version": 1, "title": "Inline VMD scene provenance", "steps": [{
        "id": "portable", "capability": "vmd-figure-studio", "operation": "compile",
        "depends_on": [], "params": {"spec": {"inputs": [{"path": str(source), "type": "xyz", "role": "structure"}]}}
    }]}


def _fixture_executor(capability, operation, params, output, skills, run):
    artifact = output / "scene.vmd"; artifact.write_text("# Nonexecuted scene fixture\n")
    return {"status": "complete", "reason": "Only provenance is tested", "artifacts": {"scene": str(artifact)}, "evidence": {"scientific_acceptance": "pending"}}


def test_nested_scene_inputs_are_hashed_and_source_tampering_blocks_resume(runtime_home, tmp_path):
    source = tmp_path / "atoms.xyz"; source.write_text("1\nsource\nH 0 0 0\n")
    plan = _compile_plan(source); out = tmp_path / "run"
    manifest = execute_plan(plan, out, runtime_home.parent, Registry(), executor=_fixture_executor)
    assert str(source) in manifest["source_sha256"]
    source.write_text("1\nchanged\nH 1 0 0\n")
    with pytest.raises(WorkflowError):
        execute_plan(plan, out, runtime_home.parent, Registry(), resume=True, executor=_fixture_executor)


def test_vendor_library_change_blocks_resume(runtime_home, tmp_path):
    source = tmp_path / "atoms.xyz"; source.write_text("1\nsource\nH 0 0 0\n")
    plan = _compile_plan(source); out = tmp_path / "run"
    execute_plan(plan, out, runtime_home.parent, Registry(), executor=_fixture_executor)
    (runtime_home / "assets/vendor/3Dmol-min.js").write_text("// changed browser code\n")
    with pytest.raises(WorkflowError):
        execute_plan(plan, out, runtime_home.parent, Registry(), resume=True, executor=_fixture_executor)


def test_changed_vmd_executable_blocks_resume(runtime_home, tmp_path, monkeypatch):
    binary = tmp_path / "vmd"; binary.write_text("native fixture A"); binary.chmod(0o700)
    monkeypatch.setenv("VMD_EXECUTABLE", str(binary))
    source = tmp_path / "atoms.xyz"; source.write_text("1\nsource\nH 0 0 0\n")
    plan = _compile_plan(source); out = tmp_path / "run"
    execute_plan(plan, out, runtime_home.parent, Registry(), executor=_fixture_executor)
    binary.write_text("native fixture B")
    with pytest.raises(WorkflowError):
        execute_plan(plan, out, runtime_home.parent, Registry(), resume=True, executor=_fixture_executor)
