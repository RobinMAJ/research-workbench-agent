"""Codex planning boundary tests with a local fake executable, never a model call."""
from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import tempfile
import textwrap
import unittest
from unittest import mock


PACKAGE_ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(PACKAGE_ROOT / "scripts"), str(PACKAGE_ROOT)]

from research_workbench import planner
from research_workbench.core import WorkflowError
from research_workbench.registry import Registry


class PlannerBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="workbench-planner-test-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.workspace = self.root / "workspace"
        self.workspace.mkdir()
        self.source = self.workspace / "source.sdf"
        self.source.write_text("Authoritative source fixture; no chemistry is evaluated.\n")
        self.source_bytes = self.source.read_bytes()
        self.skills = self.root / "skills"
        self.skills.mkdir()
        self.output = self.root / "planning"
        self.agent_root = self.root / "agent"
        (self.agent_root / "agents").mkdir(parents=True)
        (self.agent_root / "agents" / "research_workbench.toml").write_text(
            'developer_instructions = "Plan only; preserve scientific uncertainty."\n'
        )
        self.catalog = self.root / "catalog"
        self.catalog.mkdir()
        (self.catalog / "fixture.json").write_text(json.dumps({
            "schema_version": 1,
            "id": "fixture-forcefield",
            "version": "1.0.0",
            "skill": "forcefield-generator",
            "description": "Planner fixture; no computational adapter",
            "operations": ["generate"],
            "adapter": "instruction-only",
            "limits": {},
            "side_effects": [],
        }))
        self.registry = Registry(self.catalog)
        self.decision_file = self.root / "mock-decision.json"
        self.arguments = self.root / "mock-arguments.json"
        self.prompt_capture = self.root / "mock-prompt.txt"
        self.binary = self.root / "fake-codex"
        self.binary.write_text(f"#!{sys.executable}\n" + textwrap.dedent("""
            import json
            import os
            from pathlib import Path
            import sys
            import time

            args = sys.argv[1:]
            Path(os.environ['RWB_TEST_ARGUMENTS']).write_text(json.dumps(args))
            Path(os.environ['RWB_TEST_PROMPT']).write_text(sys.stdin.read())
            behavior = os.environ.get('RWB_TEST_BEHAVIOR', 'normal')
            if behavior == 'timeout':
                time.sleep(10)
            if behavior == 'failure':
                print('Deliberate fake Codex failure', file=sys.stderr)
                raise SystemExit(7)
            if behavior == 'no_response':
                raise SystemExit(0)
            output = Path(args[args.index('-o') + 1])
            if behavior == 'malformed':
                output.write_text('{invalid JSON')
            else:
                output.write_bytes(Path(os.environ['RWB_TEST_DECISION']).read_bytes())
            print(json.dumps({'type': 'mock_planner_event'}))
        """))
        self.binary.chmod(0o700)

    def ready_plan(self):
        return {
            "schema_version": 1,
            "title": "Read-only planning fixture",
            "steps": [{
                "id": "molecule",
                "capability": "fixture-forcefield",
                "operation": "generate",
                "depends_on": [],
                "params": {"spec": {
                    "molecule": {"sdf": "source.sdf"},
                    "formal_charge": 0,
                    "multiplicity": 1,
                    "backend": "gaff2",
                    "charge_method": "am1bcc",
                    "boundary": "isolated",
                }},
                "timeout_seconds": 600,
            }],
        }

    def decision(self, *, status="ready", plan=None, questions=None):
        return {
            "status": status,
            "plan_json": json.dumps(plan or self.ready_plan()) if status == "ready" else "",
            "questions": questions or [],
            "assumptions": ["No parameterization or scientific validation has been performed."],
        }

    def request(self, decision=None, *, behavior="normal", timeout=5, text="Prepare the supplied molecule"):
        self.decision_file.write_text(json.dumps(decision or self.decision()))
        variables = {
            "RESEARCH_WORKBENCH_CODEX": str(self.binary),
            "RWB_TEST_DECISION": str(self.decision_file),
            "RWB_TEST_ARGUMENTS": str(self.arguments),
            "RWB_TEST_PROMPT": str(self.prompt_capture),
            "RWB_TEST_BEHAVIOR": behavior,
        }
        with mock.patch.dict(os.environ, variables), mock.patch.object(planner, "ROOT", self.agent_root):
            return planner.plan_request(text, self.workspace, self.output, self.registry, self.skills, timeout=timeout)

    def test_ready_decision_writes_validated_plan_with_workspace_paths(self):
        returned = self.request()
        self.assertEqual(returned["status"], "ready")
        plan = json.loads((self.output / "plan.json").read_text())
        path = plan["steps"][0]["params"]["spec"]["molecule"]["sdf"]
        self.assertEqual(Path(path), self.source)
        self.assertEqual(self.source.read_bytes(), self.source_bytes)
        status = json.loads((self.output / "planner-status.json").read_text())
        self.assertEqual(status["status"], "ready")
        self.assertEqual(status["assumptions"], returned["assumptions"])

    def test_codex_launch_enforces_read_only_planning_and_stdin_prompt(self):
        self.request()
        args = json.loads(self.arguments.read_text())
        self.assertEqual(args[0], "exec")
        self.assertEqual(args[args.index("--sandbox") + 1], "read-only")
        self.assertIn("--ephemeral", args)
        self.assertIn("--output-schema", args)
        self.assertEqual(args[-1], "-")
        for forbidden in ["--yolo", "--dangerously-bypass-approvals-and-sandbox", "--full-auto"]:
            self.assertNotIn(forbidden, args)
        self.assertNotIn("danger-full-access", args)
        self.assertIn("PLANNING ONLY", self.prompt_capture.read_text())
        self.assertEqual(self.source.read_bytes(), self.source_bytes)

    def test_needs_input_preserves_questions_and_does_not_create_executable_plan(self):
        decision = self.decision(status="needs_input", questions=["Which protonation state should be used?"])
        returned = self.request(decision)
        self.assertEqual(returned["status"], "needs_input")
        self.assertEqual(returned["questions"], decision["questions"])
        self.assertFalse((self.output / "plan.json").exists())
        self.assertEqual(json.loads((self.output / "planner-status.json").read_text())["status"], "needs_input")

    def test_nonzero_codex_exit_retains_failure_evidence_without_plan(self):
        with self.assertRaises(WorkflowError):
            self.request(behavior="failure")
        status = json.loads((self.output / "planner-status.json").read_text())
        self.assertEqual(status["status"], "failed")
        self.assertEqual(status["exit_code"], 7)
        self.assertIn("Deliberate fake Codex failure", (self.output / "codex.stderr.log").read_text())
        self.assertFalse((self.output / "plan.json").exists())

    def test_success_exit_without_decision_is_failure(self):
        with self.assertRaises(WorkflowError):
            self.request(behavior="no_response")
        self.assertEqual(json.loads((self.output / "planner-status.json").read_text())["status"], "failed")
        self.assertFalse((self.output / "plan.json").exists())

    def test_timeout_is_failure_and_retains_prompt_and_logs(self):
        with self.assertRaises(WorkflowError):
            self.request(behavior="timeout", timeout=0.1)
        status = json.loads((self.output / "planner-status.json").read_text())
        self.assertEqual(status["status"], "failed")
        self.assertIn("timed out", status["reason"])
        self.assertTrue((self.output / "request.txt").is_file())
        self.assertTrue((self.output / "codex.events.jsonl").is_file())
        self.assertFalse((self.output / "plan.json").exists())

    def test_malformed_decision_json_is_a_workflow_error(self):
        with self.assertRaises(WorkflowError):
            self.request(behavior="malformed")
        self.assertTrue((self.output / "decision.json").is_file())
        self.assertFalse((self.output / "plan.json").exists())

    def test_ready_decision_with_unresolved_questions_is_rejected(self):
        with self.assertRaises(WorkflowError):
            self.request(self.decision(questions=["Which charge state?"]))
        self.assertFalse((self.output / "plan.json").exists())

    def test_ready_plan_cannot_invent_an_unknown_capability(self):
        plan = self.ready_plan()
        plan["steps"][0]["capability"] = "unregistered-simulation"
        with self.assertRaises(WorkflowError):
            self.request(self.decision(plan=plan))
        self.assertFalse((self.output / "plan.json").exists())

    def test_empty_request_is_rejected_before_codex_launch(self):
        with self.assertRaises(WorkflowError):
            self.request(text="   ")
        self.assertFalse(self.arguments.exists())
        self.assertFalse(self.output.exists())

    def test_existing_planning_directory_is_not_overwritten(self):
        self.output.mkdir()
        sentinel = self.output / "sentinel.txt"
        sentinel.write_text("Preserve existing planning evidence\n")
        with self.assertRaises(WorkflowError):
            self.request()
        self.assertFalse(self.arguments.exists())
        self.assertEqual(sentinel.read_text(), "Preserve existing planning evidence\n")


if __name__ == "__main__":
    unittest.main()
