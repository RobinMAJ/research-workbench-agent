"""Real CLI registration and extension dispatch without external services."""
from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
import hashlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest


PACKAGE_ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(PACKAGE_ROOT / "scripts"), str(PACKAGE_ROOT)]

from research_workbench import cli
from research_workbench.core import WorkflowError, execute_plan
from research_workbench.registry import Registry


EXAMPLE_DIRECTORY = PACKAGE_ROOT / "examples" / "extensions"


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


class ExtensionRegistrationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="workbench-extension-test-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.registry_directory = self.root / "registry"
        self.skills = self.root / "skills"
        self.skills.mkdir()
        self.descriptor = EXAMPLE_DIRECTORY / "artifact-summary.json"
        self.adapter = EXAMPLE_DIRECTORY / "artifact_summary.py"
        self.native = self.root / "native-manifest.json"
        self.native_data = {
            "status": "geometry_screen_pass",
            "mode": "adsorption",
            "scientific_state": "unrelaxed_starting_geometry",
            "visual_review": "pending",
            "candidate_count": 1,
            "output_sha256": {"candidate.cif": "declared_only_not_verified"},
            "private_metadata": {"source_path": "Do not copy arbitrary manifest metadata"},
        }
        self.native.write_text(json.dumps(self.native_data, indent=2) + "\n")
        self.output = self.root / "run"

    def command(self, arguments):
        stdout, stderr = io.StringIO(), io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            code = cli.main(arguments)
        return code, stdout.getvalue(), stderr.getvalue()

    def register(self, *, descriptor=None, adapter=None, trust=True):
        args = ["register", "--descriptor", str(descriptor or self.descriptor),
                "--registry", str(self.registry_directory)]
        if adapter is not False:
            args += ["--adapter-file", str(adapter or self.adapter)]
        if trust:
            args += ["--trust-code"]
        return self.command(args)

    def plan(self, capability="artifact-summary", operation="summarize", params=None):
        return {
            "schema_version": 1,
            "title": "Registered adapter workflow",
            "steps": [{"id": "summary", "capability": capability, "operation": operation,
                       "depends_on": [], "params": params if params is not None else {"manifest": str(self.native)},
                       "timeout_seconds": 30}],
        }

    def execute(self, plan=None, *, resume=False):
        return execute_plan(plan or self.plan(), self.output, skills_root=self.skills,
                            registry=Registry(self.registry_directory), resume=resume)

    def inventory(self):
        if not self.registry_directory.exists():
            return {}
        return {str(path.relative_to(self.registry_directory)): path.read_bytes()
                for path in self.registry_directory.rglob("*") if path.is_file()}

    def instrumented_adapter(self):
        marker = self.root / "import-marker.txt"
        adapter = self.root / "instrumented-adapter.py"
        # Append import-time evidence after the original future import. This is
        # a deliberate trusted test side effect, not part of the shipped example.
        adapter.write_text(self.adapter.read_text() +
                           f"\nwith Path({str(marker)!r}).open('a') as marker_file:\n"
                           "    marker_file.write('imported\\n')\n")
        return adapter, marker

    def test_real_registered_adapter_executes_and_records_summary_hash(self):
        before = self.native.read_bytes()
        code, stdout, stderr = self.register()
        self.assertEqual(code, 0, stderr)
        registration = json.loads(stdout)
        self.assertEqual(registration["status"], "registered")
        stored = self.registry_directory / "artifact-summary.json"
        module = self.registry_directory / "extensions" / "artifact_summary.py"
        self.assertEqual(registration["sha256"], sha256(stored))
        self.assertEqual(module.read_bytes(), self.adapter.read_bytes())
        manifest = self.execute()
        self.assertEqual(manifest["status"], "complete")
        artifact = manifest["stages"]["summary"]["artifacts"]["summary"]
        self.assertEqual(artifact["sha256"], sha256(artifact["path"]))
        summary = json.loads(Path(artifact["path"]).read_text())
        self.assertEqual(summary["source_sha256"], sha256(self.native))
        self.assertEqual(summary["reported_metadata"]["status"], "geometry_screen_pass")
        self.assertEqual(summary["reported_metadata"]["visual_review"], "pending")
        self.assertEqual(summary["declared_artifact_count"], 1)
        self.assertEqual(summary["scientific_acceptance"], "not_established")
        self.assertNotIn("private_metadata", summary)
        self.assertFalse(manifest["stages"]["summary"]["details"]["artifact_hashes_verified"])
        self.assertEqual(manifest["scientific_acceptance"], "pending")
        self.assertEqual(self.native.read_bytes(), before)

    def test_register_without_code_trust_rejects_without_writing_registry(self):
        code, _, stderr = self.register(trust=False)
        self.assertEqual(code, 2)
        self.assertIn("--trust-code", stderr)
        self.assertEqual(self.inventory(), {})
        self.assertFalse(self.registry_directory.exists())

    def test_trust_without_adapter_source_rejects_without_writes(self):
        code, _, _ = self.register(adapter=False)
        self.assertEqual(code, 2)
        self.assertEqual(self.inventory(), {})
        self.assertFalse(self.registry_directory.exists())

    def test_duplicate_registration_preserves_existing_descriptor_and_code(self):
        self.assertEqual(self.register()[0], 0)
        before = self.inventory()
        code, _, _ = self.register()
        self.assertEqual(code, 2)
        self.assertEqual(self.inventory(), before)

    def test_module_name_collision_cannot_overwrite_registered_code(self):
        self.assertEqual(self.register()[0], 0)
        before = self.inventory()
        value = json.loads(self.descriptor.read_text())
        value["id"] = "artifact-summary-alternative"
        alternative = self.root / "alternative.json"
        alternative.write_text(json.dumps(value))
        code, _, _ = self.register(descriptor=alternative)
        self.assertEqual(code, 2)
        self.assertEqual(self.inventory(), before)
        self.assertFalse((self.registry_directory / "artifact-summary-alternative.json").exists())

    def test_registration_and_discovery_do_not_import_code_but_dispatch_does(self):
        adapter, marker = self.instrumented_adapter()
        code, _, stderr = self.register(adapter=adapter)
        self.assertEqual(code, 0, stderr)
        self.assertFalse(marker.exists(), "Registration must not import executable code")
        registry = Registry(self.registry_directory)
        self.assertIn("artifact-summary", {entry["id"] for entry in registry.catalog()})
        registry.fingerprint("artifact-summary", self.skills)
        self.assertFalse(marker.exists(), "Discovery and fingerprinting must only read metadata/source")
        manifest = self.execute()
        self.assertEqual(manifest["status"], "complete")
        self.assertEqual(marker.read_text(), "imported\n")

    def test_registered_adapter_can_use_dataclass_module_introspection(self):
        adapter = self.root / "dataclass-adapter.py"
        adapter.write_text(self.adapter.read_text() + """

from dataclasses import dataclass

@dataclass(frozen=True)
class ExtensionLabel:
    scope: str = "metadata_only"

_native_execute = execute

def execute(capability, operation, params, output_dir, skills_root, run_process):
    label = ExtensionLabel()
    result = _native_execute(capability, operation, params, output_dir, skills_root, run_process)
    result["details"]["dataclass_scope"] = label.scope
    return result
""")
        code, _, stderr = self.register(adapter=adapter)
        self.assertEqual(code, 0, stderr)
        manifest = self.execute()
        self.assertEqual(manifest["status"], "complete")
        self.assertEqual(manifest["stages"]["summary"]["details"]["dataclass_scope"], "metadata_only")
        artifact = manifest["stages"]["summary"]["artifacts"]["summary"]
        self.assertEqual(artifact["sha256"], sha256(artifact["path"]))

    def test_code_tampering_refuses_resume_before_reimport(self):
        adapter, marker = self.instrumented_adapter()
        self.assertEqual(self.register(adapter=adapter)[0], 0)
        manifest = self.execute()
        self.assertEqual(manifest["status"], "complete")
        stored_code = self.registry_directory / "extensions" / "artifact_summary.py"
        stored_code.write_text(stored_code.read_text() + "\n# Changed after accepted run\n")
        with self.assertRaises(WorkflowError):
            self.execute(resume=True)
        self.assertEqual(marker.read_text(), "imported\n")

    def test_unchanged_resume_does_not_reimport_adapter(self):
        adapter, marker = self.instrumented_adapter()
        self.assertEqual(self.register(adapter=adapter)[0], 0)
        first = self.execute()
        resumed = self.execute(resume=True)
        self.assertEqual(resumed["status"], "complete")
        self.assertEqual(resumed["stages"]["summary"]["artifacts"], first["stages"]["summary"]["artifacts"])
        self.assertEqual(marker.read_text(), "imported\n")

    def test_registered_instruction_only_skill_is_blocked_without_execution(self):
        marker = self.root / "instruction-executed.txt"
        skill = self.skills / "fixture-conversational-skill"
        (skill / "scripts").mkdir(parents=True)
        (skill / "SKILL.md").write_text(
            "---\nname: fixture-conversational-skill\ndescription: Conversation fixture\n---\n"
        )
        (skill / "scripts" / "run.sh").write_text(f"#!/bin/sh\nprintf executed > '{marker}'\n")
        value = {
            "schema_version": 1, "id": "conversation-fixture", "version": "1.0.0",
            "skill": "fixture-conversational-skill", "description": "Instructions without an execution adapter",
            "operations": ["assist"], "adapter": "instruction-only", "limits": {}, "side_effects": [],
        }
        descriptor = self.root / "conversation.json"
        descriptor.write_text(json.dumps(value))
        code, _, stderr = self.command(["register", "--descriptor", str(descriptor),
                                       "--registry", str(self.registry_directory)])
        self.assertEqual(code, 0, stderr)
        manifest = self.execute(self.plan("conversation-fixture", "assist", {}))
        self.assertEqual(manifest["status"], "blocked")
        self.assertEqual(manifest["stages"]["summary"]["status"], "blocked")
        self.assertFalse(marker.exists())
        self.assertEqual(manifest["scientific_acceptance"], "pending")

    def test_malformed_manifest_fails_without_claiming_summary_success(self):
        self.assertEqual(self.register()[0], 0)
        self.native.write_text("{not valid JSON")
        manifest = self.execute()
        self.assertEqual(manifest["status"], "failed")
        self.assertEqual(manifest["stages"]["summary"]["status"], "failed")
        self.assertEqual(manifest["stages"]["summary"]["artifacts"], {})

    def test_duplicate_manifest_keys_are_not_silently_collapsed(self):
        self.assertEqual(self.register()[0], 0)
        self.native.write_text('{"status":"failed","status":"complete"}')
        manifest = self.execute()
        self.assertEqual(manifest["status"], "failed")
        self.assertIn("Duplicate native-manifest key", manifest["stages"]["summary"]["reason"])


if __name__ == "__main__":
    unittest.main()
