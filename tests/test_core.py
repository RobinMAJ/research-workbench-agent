"""Observable coordinator contracts, using only stdlib and isolated fixtures."""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest


PACKAGE_ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(PACKAGE_ROOT / "scripts"), str(PACKAGE_ROOT)]

from research_workbench.core import WorkflowError, execute_plan
from research_workbench.registry import Registry


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class CoreContractTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="workbench-core-test-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.output = self.root / "run"
        self.skills = self.root / "skills"
        self.skill = self.skills / "forcefield-generator"
        (self.skill / "scripts").mkdir(parents=True)
        (self.skill / "SKILL.md").write_text(
            "---\nname: forcefield-generator\ndescription: Test fixture\n---\n"
        )
        (self.skill / "scripts" / "run.sh").write_text("#!/bin/sh\nexit 0\n")
        (self.skill / "scripts" / "ffbuilder.py").write_text(
            '"""Fingerprint fixture; never executed."""\n'
        )
        self.catalog = self.root / "capabilities"
        self.catalog.mkdir()
        (self.catalog / "fixture.json").write_text(json.dumps({
            "schema_version": 1,
            "id": "fixture-forcefield",
            "version": "1.0.0",
            "skill": "forcefield-generator",
            "description": "Coordinator-only fixture; executor is explicitly injected",
            "operations": ["generate"],
            "adapter": "instruction-only",
            "limits": {},
            "side_effects": [],
        }))
        self.calls = []

    def step(self, identity, *, depends_on=(), source=None):
        molecule = {"smiles": "CN(C)C=O"}
        if source is not None:
            molecule = {"sdf": source}
        return {
            "id": identity,
            "capability": "fixture-forcefield",
            "operation": "generate",
            "depends_on": list(depends_on),
            "params": {
                "spec": {
                    "description": identity,
                    "molecule": molecule,
                    "formal_charge": 0,
                    "multiplicity": 1,
                    "backend": "gaff2",
                    "charge_method": "am1bcc",
                    "exports": ["openmm"],
                    "boundary": "isolated",
                }
            },
            "timeout_seconds": 600,
        }

    def plan(self, *steps):
        return {"schema_version": 1, "title": "Coordinator contract fixture", "steps": list(steps)}

    def executor(self, outcomes=None, observations=None):
        outcomes = outcomes or {}

        def invoke(capability, operation, params, attempt_dir, skills_root, run_process):
            identity = params["spec"]["description"]
            self.calls.append(identity)
            if observations is not None:
                observations[identity] = copy.deepcopy(params)
            attempt = Path(attempt_dir)
            attempt.mkdir(parents=True, exist_ok=True)
            artifact = attempt / "model.sdf"
            artifact.write_text(f"Coordinator fixture for {identity}; not chemistry data.\n")
            outcome = outcomes.get(identity, "complete")
            return {
                "status": outcome,
                "reason": "deliberate fixture block" if outcome == "blocked" else "fixture result",
                "artifacts": {"model": str(artifact)},
                "evidence": {
                    "visual_review": "pending",
                    "scientific_acceptance": "pending",
                    "parameter_contract": "not_evaluated_by_mock",
                },
                "details": {"fixture": True},
            }

        return invoke

    def execute(self, plan, *, executor=None, resume=False):
        return execute_plan(
            plan,
            self.output,
            skills_root=self.skills,
            registry=Registry(self.catalog),
            resume=resume,
            executor=executor or self.executor(),
        )

    def artifact_path(self, manifest, identity="alpha", key="model"):
        path = Path(manifest["stages"][identity]["artifacts"][key]["path"])
        return path if path.is_absolute() else self.output / path

    def modify_manifest(self, mutate):
        path = self.output / "manifest.json"
        data = json.loads(path.read_text())
        mutate(data)
        path.write_text(json.dumps(data, indent=2) + "\n")

    def test_complete_run_records_hashed_artifact_and_pending_evidence(self):
        manifest = self.execute(self.plan(self.step("alpha")))
        self.assertEqual(manifest["status"], "complete")
        stage = manifest["stages"]["alpha"]
        self.assertEqual(stage["status"], "complete")
        artifact = stage["artifacts"]["model"]
        self.assertEqual(artifact["sha256"], sha256(self.artifact_path(manifest)))
        self.assertEqual(stage["evidence"]["visual_review"], "pending")
        self.assertEqual(stage["evidence"]["scientific_acceptance"], "pending")
        self.assertNotIn(manifest.get("scientific_acceptance"), {"accepted", "validated", "complete"})
        self.assertNotIn(manifest.get("visual_review"), {"approved", "reviewed", "complete"})

    def test_default_registry_exposes_the_three_existing_skills(self):
        identifiers = {entry["id"] for entry in Registry().catalog()}
        self.assertTrue(
            {"atomistic-structure-builder", "forcefield-generator", "vesta-figure-maker"} <= identifiers,
            f"Builtin capabilities are missing: {identifiers}",
        )

    def test_blocked_stage_blocks_descendants_but_independent_stage_completes(self):
        plan = self.plan(
            self.step("blocked"),
            self.step("child", depends_on=["blocked"]),
            self.step("grandchild", depends_on=["child"]),
            self.step("independent"),
        )
        manifest = self.execute(plan, executor=self.executor({"blocked": "blocked"}))
        self.assertEqual(manifest["status"], "blocked")
        self.assertEqual(manifest["stages"]["blocked"]["status"], "blocked")
        self.assertEqual(manifest["stages"]["child"]["status"], "dependency_blocked")
        self.assertEqual(manifest["stages"]["grandchild"]["status"], "dependency_blocked")
        self.assertEqual(manifest["stages"]["independent"]["status"], "complete")
        self.assertEqual(self.calls, ["blocked", "independent"])

    def test_failed_stage_retains_diagnostics_and_does_not_execute_descendant(self):
        manifest = self.execute(
            self.plan(self.step("alpha"), self.step("child", depends_on=["alpha"]), self.step("independent")),
            executor=self.executor({"alpha": "failed"}),
        )
        self.assertEqual(manifest["status"], "failed")
        self.assertEqual(manifest["stages"]["alpha"]["status"], "failed")
        self.assertEqual(manifest["stages"]["child"]["status"], "dependency_blocked")
        self.assertEqual(manifest["stages"]["independent"]["status"], "complete")
        self.assertTrue(self.artifact_path(manifest).is_file())
        self.assertEqual(self.calls, ["alpha", "independent"])

    def test_direct_dependency_reference_resolves_to_recorded_artifact(self):
        observations = {}
        manifest = self.execute(
            self.plan(self.step("alpha"), self.step("beta", depends_on=["alpha"], source={"$ref": "alpha:model"})),
            executor=self.executor(observations=observations),
        )
        self.assertEqual(manifest["status"], "complete")
        source = observations["beta"]["spec"]["molecule"]["sdf"]
        self.assertEqual(Path(source).resolve(), self.artifact_path(manifest).resolve())
        self.assertEqual(self.calls, ["alpha", "beta"])

    def test_transitive_dependency_reference_resolves(self):
        observations = {}
        manifest = self.execute(
            self.plan(
                self.step("alpha"),
                self.step("beta", depends_on=["alpha"]),
                self.step("gamma", depends_on=["beta"], source={"$ref": "alpha:model"}),
            ),
            executor=self.executor(observations=observations),
        )
        self.assertEqual(manifest["status"], "complete")
        self.assertEqual(
            Path(observations["gamma"]["spec"]["molecule"]["sdf"]).resolve(),
            self.artifact_path(manifest).resolve(),
        )

    def test_dependency_order_is_resolved_before_dispatch(self):
        manifest = self.execute(
            self.plan(self.step("beta", depends_on=["alpha"]), self.step("alpha"))
        )
        self.assertEqual(manifest["status"], "complete")
        self.assertEqual(self.calls, ["alpha", "beta"])

    def test_missing_dependency_artifact_fails_consumer_without_dispatching_it(self):
        manifest = self.execute(
            self.plan(
                self.step("alpha"),
                self.step("beta", depends_on=["alpha"], source={"$ref": "alpha:absent"}),
                self.step("independent"),
            )
        )
        self.assertEqual(manifest["status"], "failed")
        self.assertEqual(manifest["stages"]["alpha"]["status"], "complete")
        self.assertEqual(manifest["stages"]["beta"]["status"], "failed")
        self.assertEqual(manifest["stages"]["independent"]["status"], "complete")
        self.assertEqual(self.calls, ["alpha", "independent"])

    def test_reference_without_dependency_is_rejected_before_execution(self):
        plan = self.plan(self.step("alpha"), self.step("beta", source={"$ref": "alpha:model"}))
        with self.assertRaises(WorkflowError):
            self.execute(plan)
        self.assertEqual(self.calls, [])

    def test_unknown_reference_stage_is_rejected_before_execution(self):
        with self.assertRaises(WorkflowError):
            self.execute(self.plan(self.step("alpha", source={"$ref": "absent:model"})))
        self.assertEqual(self.calls, [])

    def test_unknown_dependency_is_rejected_before_execution(self):
        with self.assertRaises(WorkflowError):
            self.execute(self.plan(self.step("alpha", depends_on=["absent"])))
        self.assertEqual(self.calls, [])

    def test_cycle_is_rejected_before_execution(self):
        plan = self.plan(self.step("alpha", depends_on=["beta"]), self.step("beta", depends_on=["alpha"]))
        with self.assertRaises(WorkflowError):
            self.execute(plan)
        self.assertEqual(self.calls, [])

    def test_duplicate_stage_identity_is_rejected(self):
        with self.assertRaises(WorkflowError):
            self.execute(self.plan(self.step("alpha"), self.step("alpha")))
        self.assertEqual(self.calls, [])

    def test_unknown_capability_is_rejected(self):
        step = self.step("alpha")
        step["capability"] = "not-registered"
        with self.assertRaises(WorkflowError):
            self.execute(self.plan(step))
        self.assertEqual(self.calls, [])

    def test_unknown_operation_is_rejected(self):
        step = self.step("alpha")
        step["operation"] = "infer-periodic-surface-chemistry"
        with self.assertRaises(WorkflowError):
            self.execute(self.plan(step))
        self.assertEqual(self.calls, [])

    def test_missing_recognized_source_file_is_rejected(self):
        with self.assertRaises(WorkflowError):
            self.execute(self.plan(self.step("alpha", source=str(self.root / "missing.sdf"))))
        self.assertEqual(self.calls, [])

    def test_relative_source_file_is_resolved_using_plan_base_directory(self):
        source = self.root / "source.sdf"
        source.write_text("Coordinator source fixture; no chemistry parsing.\n")
        plan = self.plan(self.step("alpha", source="source.sdf"))
        plan["_base_dir"] = str(self.root)
        observations = {}
        manifest = self.execute(plan, executor=self.executor(observations=observations))
        self.assertEqual(manifest["status"], "complete")
        self.assertEqual(Path(observations["alpha"]["spec"]["molecule"]["sdf"]).resolve(), source)

    def test_completed_resume_reuses_verified_results_without_executor_call(self):
        plan = self.plan(self.step("alpha"))
        first = self.execute(plan)
        self.calls.clear()
        resumed = self.execute(plan, resume=True)
        self.assertEqual(resumed["status"], "complete")
        self.assertEqual(resumed["stages"]["alpha"]["artifacts"], first["stages"]["alpha"]["artifacts"])
        self.assertEqual(self.calls, [])

    def test_blocked_resume_does_not_silently_retry_or_unblock_descendants(self):
        plan = self.plan(self.step("alpha"), self.step("child", depends_on=["alpha"]))
        self.execute(plan, executor=self.executor({"alpha": "blocked"}))
        self.calls.clear()
        resumed = self.execute(plan, resume=True)
        self.assertEqual(resumed["status"], "blocked")
        self.assertEqual(resumed["stages"]["child"]["status"], "dependency_blocked")
        self.assertEqual(self.calls, [])

    def test_failed_resume_does_not_silently_retry(self):
        plan = self.plan(self.step("alpha"))
        self.execute(plan, executor=self.executor({"alpha": "failed"}))
        self.calls.clear()
        resumed = self.execute(plan, resume=True)
        self.assertEqual(resumed["status"], "failed")
        self.assertEqual(self.calls, [])

    def test_artifact_tampering_prevents_resume(self):
        plan = self.plan(self.step("alpha"))
        manifest = self.execute(plan)
        self.artifact_path(manifest).write_text("Changed artifact bytes\n")
        self.calls.clear()
        with self.assertRaises(WorkflowError):
            self.execute(plan, resume=True)
        self.assertEqual(self.calls, [])

    def test_missing_artifact_prevents_resume(self):
        plan = self.plan(self.step("alpha"))
        manifest = self.execute(plan)
        self.artifact_path(manifest).unlink()
        self.calls.clear()
        with self.assertRaises(WorkflowError):
            self.execute(plan, resume=True)
        self.assertEqual(self.calls, [])

    def test_changed_source_bytes_prevent_resume(self):
        source = self.root / "source.sdf"
        source.write_text("Source version one\n")
        plan = self.plan(self.step("alpha", source=str(source)))
        self.execute(plan)
        source.write_text("Source version two\n")
        self.calls.clear()
        with self.assertRaises(WorkflowError):
            self.execute(plan, resume=True)
        self.assertEqual(self.calls, [])

    def test_changed_plan_prevents_resume(self):
        plan = self.plan(self.step("alpha"))
        self.execute(plan)
        changed = copy.deepcopy(plan)
        changed["steps"][0]["params"]["spec"]["backend"] = "openff"
        self.calls.clear()
        with self.assertRaises(WorkflowError):
            self.execute(changed, resume=True)
        self.assertEqual(self.calls, [])

    def test_changed_adapter_source_prevents_resume(self):
        plan = self.plan(self.step("alpha"))
        self.execute(plan)
        (self.skill / "scripts" / "run.sh").write_text("#!/bin/sh\nexit 9\n")
        self.calls.clear()
        with self.assertRaises(WorkflowError):
            self.execute(plan, resume=True)
        self.assertEqual(self.calls, [])

    def test_source_change_during_dispatch_fails_integrity_acceptance(self):
        source = self.root / "source.sdf"
        source.write_text("Original source bytes\n")
        ordinary = self.executor()

        def mutating_executor(*args):
            result = ordinary(*args)
            source.write_text("Source changed while the tool was executing\n")
            return result

        manifest = self.execute(
            self.plan(self.step("alpha", source=str(source))), executor=mutating_executor
        )
        self.assertEqual(manifest["status"], "failed")
        self.assertIn(str(source), manifest["integrity_failure"]["changed_sources"])
        self.assertTrue(self.artifact_path(manifest).is_file(), "Diagnostic artifacts must survive")
        self.assertEqual(manifest["scientific_acceptance"], "pending")

    def test_skill_code_change_during_dispatch_fails_integrity_acceptance(self):
        code = self.skill / "scripts" / "ffbuilder.py"
        ordinary = self.executor()

        def mutating_executor(*args):
            result = ordinary(*args)
            code.write_text('"""Changed after execution began."""\n')
            return result

        manifest = self.execute(self.plan(self.step("alpha")), executor=mutating_executor)
        self.assertEqual(manifest["status"], "failed")
        self.assertTrue(manifest["integrity_failure"]["tool_changed"])
        self.assertTrue(self.artifact_path(manifest).is_file(), "Diagnostic artifacts must survive")
        self.assertEqual(manifest["scientific_acceptance"], "pending")

    def test_running_stage_is_not_accepted_even_under_complete_manifest(self):
        plan = self.plan(self.step("alpha"))
        self.execute(plan)
        self.modify_manifest(lambda data: data["stages"]["alpha"].update(status="running"))
        self.calls.clear()
        with self.assertRaises(WorkflowError):
            self.execute(plan, resume=True)
        self.assertEqual(self.calls, [])

    def test_incomplete_manifest_status_is_not_accepted(self):
        plan = self.plan(self.step("alpha"))
        self.execute(plan)
        self.modify_manifest(lambda data: data.update(status="running"))
        self.calls.clear()
        with self.assertRaises(WorkflowError):
            self.execute(plan, resume=True)
        self.assertEqual(self.calls, [])

    def test_unknown_saved_stage_status_is_not_promoted_to_complete(self):
        plan = self.plan(self.step("alpha"))
        self.execute(plan)
        self.modify_manifest(lambda data: data["stages"]["alpha"].update(status="unverified"))
        self.calls.clear()
        with self.assertRaises(WorkflowError):
            self.execute(plan, resume=True)
        self.assertEqual(self.calls, [])

    def test_complete_manifest_with_missing_stage_is_not_accepted(self):
        plan = self.plan(self.step("alpha"))
        self.execute(plan)
        self.modify_manifest(lambda data: data.update(stages={}))
        self.calls.clear()
        with self.assertRaises(WorkflowError):
            self.execute(plan, resume=True)
        self.assertEqual(self.calls, [])

    def test_existing_run_is_not_overwritten_without_resume(self):
        plan = self.plan(self.step("alpha"))
        manifest = self.execute(plan)
        before = self.artifact_path(manifest).read_bytes()
        self.calls.clear()
        with self.assertRaises(WorkflowError):
            self.execute(plan)
        self.assertEqual(self.artifact_path(manifest).read_bytes(), before)
        self.assertEqual(self.calls, [])

    def test_retained_run_lock_prevents_resume(self):
        plan = self.plan(self.step("alpha"))
        self.execute(plan)
        (self.output / ".run.lock").write_text("Unresolved prior process; audit required.\n")
        self.calls.clear()
        with self.assertRaisesRegex(WorkflowError, "[Ll]ock"):
            self.execute(plan, resume=True)
        self.assertEqual(self.calls, [])
        self.assertTrue((self.output / ".run.lock").exists())

    def test_normal_terminal_results_release_run_lock(self):
        manifest = self.execute(self.plan(self.step("alpha")), executor=self.executor({"alpha": "failed"}))
        self.assertEqual(manifest["status"], "failed")
        self.assertFalse((self.output / ".run.lock").exists())

    def test_concurrent_execution_is_rejected_by_run_lock(self):
        plan = self.plan(self.step("alpha"))
        entered, release = threading.Event(), threading.Event()
        results, errors = [], []
        ordinary = self.executor()

        def slow_executor(*args):
            entered.set()
            if not release.wait(5):
                raise RuntimeError("Test synchronization timeout")
            return ordinary(*args)

        def run_first():
            try:
                results.append(self.execute(plan, executor=slow_executor))
            except Exception as error:
                errors.append(error)

        thread = threading.Thread(target=run_first, daemon=True)
        thread.start()
        try:
            self.assertTrue(entered.wait(5), "First executor did not start")
            with self.assertRaisesRegex(WorkflowError, "[Ll]ock"):
                self.execute(plan, resume=True)
        finally:
            release.set()
            thread.join(5)
        self.assertFalse(thread.is_alive(), "First run did not finish")
        self.assertEqual(errors, [])
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["status"], "complete")
        self.assertEqual(self.calls, ["alpha"])


if __name__ == "__main__":
    unittest.main()
