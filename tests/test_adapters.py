"""Adapter status, provenance, path-safety and molecular handoff contracts."""
from pathlib import Path
import hashlib
import json
import struct
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from research_workbench.adapters import execute


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value))


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def hashed_manifest(directory, value, field="output_sha256", name="manifest.json"):
    value = dict(value)
    value[field] = {str(p.relative_to(directory)): digest(p) for p in directory.rglob("*") if p.is_file() and p.name != name}
    write_json(directory / name, value)
    return directory / name


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    monkeypatch.delenv("ATOMISTIC_BUILDER_PYTHON", raising=False)
    monkeypatch.delenv("FF_GENERATOR_PYTHON", raising=False)
    skills = tmp_path / "skills"
    for skill in ["atomistic-structure-builder", "forcefield-generator", "vesta-figure-maker"]:
        script = skills / skill / "scripts" / ("vesta_export.sh" if skill == "vesta-figure-maker" else "run.sh")
        script.parent.mkdir(parents=True)
        script.write_text("# mocked launcher; never executed\n")
        python = skills / skill / ".venv" / "bin" / "python"
        python.parent.mkdir(parents=True)
        python.symlink_to(sys.executable)
    output = tmp_path / "attempt"
    output.mkdir()
    return output, skills


def runner(callback, code=0):
    def run(argv, cwd=None, timeout=600, env=None):
        assert isinstance(argv, list) and all(isinstance(arg, str) for arg in argv)
        assert 1 <= timeout <= 3600
        callback(argv, cwd, env)
        return {"exit_code": code, "stdout_file": None, "stderr_file": None, "timed_out": False}
    return run


def result_path(argv):
    return Path(argv[argv.index("--output") + 1])


def structure_result(directory, status="geometry_screen_pass", label="surface_demo", run_status="complete"):
    directory.mkdir(parents=True)
    candidate = directory / label
    candidate.mkdir()
    for name in ["structure.json", "atom-map.json", "build-metadata.json"]:
        write_json(candidate / name, {})
    for name in ["checks.json", "export-checks.json"]:
        write_json(candidate / name, {"status": status})
    for name in ["structure.cif", "POSCAR", "structure.extxyz", "viewer.html", "preview.png"]:
        (candidate / name).write_text("synthetic contract fixture")
    return hashed_manifest(directory, {"schema_version": 1, "status": run_status,
         "scientific_state": "unrelaxed_starting_geometries", "candidates": [
             {"label": label, "directory": label, "status": status}]})


def test_build_pass_requires_explicit_candidate_and_verifies_artifacts(workspace):
    output, skills = workspace
    def native(argv, cwd, env):
        assert argv[:3] == ["/bin/sh", str(skills / "atomistic-structure-builder/scripts/run.sh"), "build"]
        assert cwd == skills / "atomistic-structure-builder"
        assert Path(env["ATOMISTIC_BUILDER_PYTHON"]).samefile(sys.executable)
        assert env["ATOMISTIC_BUILDER_PYTHON"] == str(skills / "atomistic-structure-builder/.venv/bin/python")
        assert json.loads(Path(argv[argv.index("--spec") + 1]).read_text())["mode"] == "surface"
        structure_result(result_path(argv))
    result = execute("atomistic-structure-builder", "build", {"spec": {"mode": "surface"}, "candidate": "surface_demo"}, output, skills, runner(native))
    assert result["status"] == "complete"
    assert all(Path(path).is_absolute() for path in result["artifacts"].values())
    assert result["artifacts"]["geometry"].endswith("structure.extxyz")
    assert result["details"]["visual_review"] == "pending"
    assert result["details"]["energetic_stability"] == "not_evaluated"


@pytest.mark.parametrize("params", [
    {"spec": {"mode": "surface"}},
    {"spec": {"mode": "surface"}, "candidate": "x", "runtime_python": "/bin/python"},
    {"spec": {"mode": "surface", "api_key": "must-not-be-written"}, "candidate": "x"},
    {"spec": {"mode": "surface"}, "candidate": "x", "timeout_seconds": True},
])
def test_bad_adapter_fields_fail_before_execution_or_request_write(workspace, params):
    output, skills = workspace
    result = execute("atomistic-structure-builder", "build", params, output, skills,
                     lambda *args, **kwargs: pytest.fail("Bad request launched a subprocess"))
    assert result["status"] == "failed"
    if "api_key" in params.get("spec", {}):
        assert not (output / "request.json").exists()
        assert "must-not-be-written" not in json.dumps(result)


def test_rejected_selected_candidate_is_blocked(workspace):
    output, skills = workspace
    result = execute("atomistic-structure-builder", "build", {"spec": {"mode": "surface"}, "candidate": "surface_demo"}, output, skills,
                     runner(lambda argv, *_: structure_result(result_path(argv), status="rejected")))
    assert result["status"] == "blocked"
    assert "structure" not in result["artifacts"]


def test_all_candidates_rejected_exit_one_is_blocked(workspace):
    output, skills = workspace
    result = execute("atomistic-structure-builder", "build", {"spec": {"mode": "surface"}, "candidate": "surface_demo"}, output, skills,
                     runner(lambda argv, *_: structure_result(result_path(argv), status="rejected", run_status="all_candidates_rejected"), code=1))
    assert result["status"] == "blocked"


def test_visualization_references_original_verified_bundle_without_copying(workspace, tmp_path):
    output, skills = workspace
    manifest = structure_result(tmp_path / "native")
    result = execute("visualization", "collect", {"source_manifest": str(manifest), "candidate": "surface_demo"}, output, skills,
                     lambda *args, **kwargs: pytest.fail("Collection launched a subprocess"))
    assert result["status"] == "complete"
    assert Path(result["artifacts"]["viewer"]).parent.parent == manifest.parent
    assert list(output.iterdir()) == []
    assert "never copied" in result["details"]["collection_policy"]


def test_visualization_tampered_viewer_fails(workspace, tmp_path):
    output, skills = workspace
    manifest = structure_result(tmp_path / "native")
    (manifest.parent / "surface_demo/viewer.html").write_text("tampered")
    result = execute("visualization", "collect", {"source_manifest": str(manifest), "candidate": "surface_demo"}, output, skills, None)
    assert result["status"] == "failed"
    assert "hash mismatch" in result["reason"]


@pytest.mark.parametrize("malicious_path", ["../outside.txt", "/tmp/outside.txt", "..\\outside.txt"])
def test_manifest_artifact_traversal_fails(workspace, tmp_path, malicious_path):
    output, skills = workspace
    manifest = tmp_path / "native/manifest.json"
    write_json(manifest, {"schema_version": 1, "status": "complete", "output_sha256": {malicious_path: "0" * 64}})
    result = execute("visualization", "collect", {"source_manifest": str(manifest), "candidate": "x"}, output, skills, None)
    assert result["status"] == "failed"


def test_manifest_symlink_artifacts_fail(workspace, tmp_path):
    output, skills = workspace
    manifest = structure_result(tmp_path / "native")
    viewer = manifest.parent / "surface_demo/viewer.html"
    content = viewer.read_text()
    external = tmp_path / "external.html"
    external.write_text(content)
    viewer.unlink()
    viewer.symlink_to(external)
    result = execute("visualization", "collect", {"source_manifest": str(manifest), "candidate": "surface_demo"}, output, skills, None)
    assert result["status"] == "failed" and "symlink" in result["reason"]


@pytest.mark.parametrize("code,native_status,normalized", [(3, "compatibility_blocked", "blocked"), (0, "compatibility_metadata_pass", "complete"), (2, "failed", "failed")])
def test_mixture_native_outcomes_are_not_promoted(workspace, code, native_status, normalized):
    output, skills = workspace
    def native(argv, *_):
        write_json(result_path(argv) / "checks.json", {"status": native_status, "problems": ["missing LiF-DMF"]})
    result = execute("forcefield-generator", "audit-mixture", {"spec": {}}, output, skills, runner(native, code))
    assert result["status"] == normalized
    assert result["details"]["scientific_acceptance"] == "pending"
    assert Path(result["artifacts"]["manifest"]).name == "adapter-manifest.json"


def molecular_result(directory, incomplete=False):
    directory.mkdir()
    for name in ["input.sdf", "reference-system.xml", "system.xml"]:
        (directory / name).write_text("synthetic contract fixture")
    for name in ["atom-map.json", "parameters.json"]:
        write_json(directory / name, {})
    write_json(directory / "checks.json", {"status": "parameter_contract_pass"})
    write_json(directory / "export-checks.json", {} if incomplete else {"openmm": {"status": "openmm_reader_roundtrip_pass"}})
    return hashed_manifest(directory, {"status": "parameter_contract_pass", "exports": ["openmm"]}, field="outputs_sha256")


@pytest.mark.parametrize("incomplete,expected", [(False, "complete"), (True, "failed")])
def test_forcefield_requires_all_requested_native_exports(workspace, incomplete, expected):
    output, skills = workspace
    result = execute("forcefield-generator", "generate", {"spec": {"exports": ["openmm"]}}, output, skills,
                     runner(lambda argv, *_: molecular_result(result_path(argv), incomplete)))
    assert result["status"] == expected
    if not incomplete:
        assert "parameters" in result["artifacts"] and "openmm" in result["artifacts"]
        assert result["details"]["scientific_acceptance"] == "pending"


def test_geometry_audit_has_a_hashed_wrapper(workspace, tmp_path):
    output, skills = workspace
    source = tmp_path / "slab.cif"
    source.write_text("synthetic geometry input")
    def native(argv, *_):
        assert str(source) in argv and "--slab" in argv and "--min-vacuum" in argv
        write_json(result_path(argv) / "checks.json", {"status": "geometry_screen_pass", "scientific_state": "unrelaxed_starting_geometry"})
    result = execute("atomistic-structure-builder", "audit", {"input": str(source), "slab": True, "min_vacuum": 12.0}, output, skills, runner(native))
    assert result["status"] == "complete"
    report = json.loads(Path(result["artifacts"]["manifest"]).read_text())
    assert report["outputs_sha256"]["checks.json"] == digest(Path(result["artifacts"]["checks"]))


def test_vesta_uses_zsh_new_png_and_pending_review(workspace, tmp_path):
    output, skills = workspace
    scene = tmp_path / "scene.cif"
    scene.write_text("synthetic CIF contract fixture")
    def native(argv, cwd, env):
        assert argv[0] == "/bin/zsh" and argv[-1] == "2"
        assert cwd == scene.parent and env["VESTA_EXPORT_TIMEOUT_SECONDS"] == "90"
        Path(argv[-2]).write_bytes(b"\x89PNG\r\n\x1a\n" + struct.pack(">I", 13) + b"IHDR" + struct.pack(">II", 640, 480))
    result = execute("vesta-figure-maker", "render", {"scene": str(scene), "scale": 2}, output, skills, runner(native))
    assert result["status"] == "complete"
    assert result["details"]["width_pixels"] == 640
    assert result["details"]["visual_review"] == "pending"


def test_linked_vesta_scene_requires_dependency_bundle(workspace, tmp_path):
    output, skills = workspace
    scene = tmp_path / "scene.vesta"
    scene.write_text('IMPORT_STRUCTURE\n"elsewhere.cif"\n')
    result = execute("vesta-figure-maker", "render", {"scene": str(scene)}, output, skills,
                     lambda *args, **kwargs: pytest.fail("Linked scene launched VESTA"))
    assert result["status"] == "blocked"


def test_timed_out_native_run_is_failed(workspace):
    output, skills = workspace
    result = execute("forcefield-generator", "prepare", {"spec": {}}, output, skills,
                     lambda *args, **kwargs: {"exit_code": -15, "timed_out": True})
    assert result["status"] == "failed" and result["evidence"]["timed_out"] is True


def test_mp_fetch_retains_provisional_choices_and_new_ids(workspace, monkeypatch):
    output, skills = workspace
    monkeypatch.setenv("MP_API_KEY", "synthetic-mock-value-never-recorded")
    def native(argv, *_):
        root = result_path(argv)
        candidate = root / "mp-aaaaabru"
        candidate.mkdir(parents=True)
        for name in ["structure.json", "structure.cif", "viewer.html"]:
            (candidate / name).write_text("synthetic MP contract fixture")
        provenance = {"material_id": "mp-aaaaabru", "selection_status": "provisional", "export_complete": True}
        hashed_manifest(candidate, provenance, field="files_sha256", name="mp-provenance.json")
        write_json(root / "retrieval.json", {"status": "requires_explicit_material_id_selection", "ambiguity": False,
              "candidates": [{"candidate_directory": "mp-aaaaabru", "material_id": "mp-aaaaabru", "structure_sha256": digest(candidate / "structure.json")}]})
    result = execute("atomistic-structure-builder", "mp-fetch", {"query": {"formula": "LiF"}}, output, skills, runner(native))
    assert result["status"] == "complete"
    assert result["details"]["selection_status"] == "provisional"
    assert "structure" not in result["artifacts"]


def test_mp_fetch_missing_local_credential_blocks_without_prompt_or_launch(workspace, monkeypatch):
    output, skills = workspace
    monkeypatch.delenv("MP_API_KEY", raising=False)
    result = execute("atomistic-structure-builder", "mp-fetch", {"query": {"formula": "LiF"}}, output, skills,
                     lambda *args, **kwargs: pytest.fail("Missing credential launched an interactive downloader"))
    assert result["status"] == "blocked"
    assert result["evidence"]["api_key_help_url"] == "https://next-gen.materialsproject.org/api"
    assert not (output / "query.json").exists()


def test_runtime_override_preserves_venv_executable_symlink(workspace, monkeypatch, tmp_path):
    output, skills = workspace
    custom = tmp_path / "custom-venv/bin/python"
    custom.parent.mkdir(parents=True)
    custom.symlink_to(sys.executable)
    monkeypatch.setenv("FF_GENERATOR_PYTHON", str(custom))
    def native(argv, cwd, env):
        assert env["FF_GENERATOR_PYTHON"] == str(custom)
        root = result_path(argv)
        root.mkdir()
        (root / "input.sdf").write_text("synthetic input")
        write_json(root / "atom-map.json", {})
        hashed_manifest(root, {"status": "molecular_preflight_pass"}, field="outputs_sha256")
    result = execute("forcefield-generator", "prepare", {"spec": {}}, output, skills, runner(native))
    assert result["status"] == "complete"


def test_mp_selection_checks_selection_record_hash(workspace, tmp_path):
    output, skills = workspace
    source = tmp_path / "retrieval.json"
    write_json(source, {})
    def native(argv, *_):
        root = result_path(argv)
        root.mkdir()
        for name in ["structure.json", "structure.cif", "viewer.html"]:
            (root / name).write_text("synthetic selected crystal")
        selection = root / "source-selection.json"
        write_json(selection, {"material_id": "mp-149"})
        report = {"material_id": "mp-149", "selection_status": "selected", "export_complete": True,
                  "selection_record_sha256": digest(selection)}
        hashed_manifest(root, report, field="files_sha256", name="mp-provenance.json")
        selection.write_text("tampered selection")
    result = execute("atomistic-structure-builder", "mp-select", {"source_manifest": str(source), "material_id": "mp-149", "reason": "Explicit source choice"}, output, skills, runner(native))
    assert result["status"] == "failed" and "hash" in result["reason"]


def handoff_fixture(tmp_path):
    Chem = pytest.importorskip("rdkit.Chem")
    AllChem = pytest.importorskip("rdkit.Chem.AllChem")
    np = pytest.importorskip("numpy")
    mol = Chem.AddHs(Chem.MolFromSmiles("F[C@](Cl)(Br)I"))
    assert AllChem.EmbedMolecule(mol, randomSeed=47) == 0
    source = tmp_path / "graph.sdf"
    writer = Chem.SDWriter(str(source)); writer.SetProps([]); writer.write(mol); writer.close()
    # Use serialized source coordinates as the immutable graph's geometry authority.
    mol = Chem.SDMolSupplier(str(source), removeHs=False)[0]
    coords = np.asarray(mol.GetConformer().GetPositions())
    rotation = np.array([[0., -1., 0.], [1., 0., 0.], [0., 0., 1.]])
    moved = coords @ rotation + np.array([5., 7., 9.])
    order = list(reversed(range(mol.GetNumAtoms())))
    rows = [{"source_index": i, "export_index": order.index(i) + 1, "component": "adsorbate", "species": mol.GetAtomWithIdx(i).GetSymbol()} for i in range(mol.GetNumAtoms())]
    mapping = tmp_path / "atom-map.json"
    write_json(mapping, {"atom_mapping": rows})
    geometry = tmp_path / "structure.extxyz"
    def write_geometry(values):
        lines = [str(mol.GetNumAtoms() + 1), 'Properties=species:S:1:pos:R:3:component:S:1', "Cu 0 0 0 substrate"]
        lines += [mol.GetAtomWithIdx(i).GetSymbol() + " " + " ".join(f"{x:.8f}" for x in values[i]) + " adsorbate" for i in order]
        geometry.write_text("\n".join(lines) + "\n")
    write_geometry(moved)
    return source, geometry, mapping, moved, write_geometry


def test_mapping_worker_preserves_graph_and_uses_explicit_export_bijection(tmp_path):
    from research_workbench.mapping_worker import main
    source, geometry, mapping, _, _ = handoff_fixture(tmp_path)
    original_hash = digest(source)
    output = tmp_path / "result"
    assert main(["--graph-sdf", str(source), "--geometry", str(geometry), "--atom-map", str(mapping), "--output", str(output)]) == 0
    evidence = json.loads((output / "mapping-evidence.json").read_text())
    assert evidence["bond_inference"] == "none"
    assert evidence["rotation_determinant"] == pytest.approx(1.)
    assert evidence["maximum_rigid_fit_residual_angstrom"] < 1e-6
    assert digest(source) == original_hash


@pytest.mark.parametrize("failure", ["duplicate_source_index", "element_mismatch", "reflection", "distortion", "no_map_for_combined_geometry"])
def test_mapping_worker_rejects_identity_or_geometry_changes(tmp_path, failure):
    from research_workbench.mapping_worker import main
    source, geometry, mapping, moved, write_geometry = handoff_fixture(tmp_path)
    argv = ["--graph-sdf", str(source), "--geometry", str(geometry), "--atom-map", str(mapping), "--output", str(tmp_path / "result")]
    if failure == "duplicate_source_index":
        data = json.loads(mapping.read_text()); data["atom_mapping"][1]["source_index"] = 0; write_json(mapping, data)
    elif failure == "element_mismatch":
        data = json.loads(mapping.read_text()); data["atom_mapping"][0]["species"] = "H"; write_json(mapping, data)
    elif failure == "reflection":
        moved[:, 0] *= -1; write_geometry(moved)
    elif failure == "distortion":
        moved[0, 0] += .5; write_geometry(moved)
    else:
        i = argv.index("--atom-map"); del argv[i:i + 2]
    assert main(argv) == 2
    manifest = json.loads((tmp_path / "result/manifest.json").read_text())
    assert manifest["status"] == "failed"
    assert not (tmp_path / "result/positioned.sdf").exists()


def test_capability_metadata_exposes_every_supported_operation_schema():
    from research_workbench.adapters import OPERATIONS
    root = Path(__file__).resolve().parents[1] / "capabilities"
    for capability, operations in OPERATIONS.items():
        metadata = json.loads((root / (capability + ".json")).read_text())
        assert metadata["schema_version"] == 1
        assert metadata["id"] == capability
        assert set(metadata["operations"]) == operations == set(metadata["parameters"])
        for schema in metadata["parameters"].values():
            assert schema["additionalProperties"] is False
            assert set(schema["required"]).issubset(schema["properties"])
            assert "runtime_python" not in schema["properties"]
