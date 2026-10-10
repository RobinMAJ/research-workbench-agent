---
name: research-workbench
description: Route scientific requests to the Research Workbench custom Agent and its deterministic capability runner. Use for coordinated structure, molecular force-field, fitting, visualization and registered research-skill workflows, including multi-stage artifact handoffs and resumable runs.
---

# Research Workbench launcher

This skill is the discovery and invocation router for the persistent Codex custom Agent `research_workbench` and the `research_workbench` Python runner. The scientific instructions remain in the installed source skills and their adapters.

## Route the request

1. Delegate the research task to the custom Agent `research_workbench` when the current Codex runtime supports custom agents. Include the user's objective, workspace, inputs, constraints and approval boundaries. Report delegation only after the runtime confirms it.
2. Inspect readiness with the installed launcher's `doctor` and `capabilities` commands. Read the relevant source skill for scientific decisions and its declared adapter contract for execution. A listed instruction-only skill requires agent-assisted work rather than an executable plan step.
3. For a natural-language request through the local CLI, use `ask` to obtain a typed plan from the read-only Codex planner and execute ready operations. Use `--plan-only` when the user requests planning. This uses the existing Codex login and consumes Codex usage.
4. For an existing ready plan, use `run`. Use `resume` to verify and reuse a terminal recorded run. Audit interrupted or running evidence before starting a new run; the runner refuses automatic continuation. Preserve new-directory and integrity requirements. Inspect the results and return the reported artifacts and remaining scientific limitations.

Resolve this skill's directory before invoking its launcher:

```sh
sh scripts/run.sh doctor
sh scripts/run.sh capabilities
sh scripts/run.sh ask --request 'Prepare the supplied molecule and audit its identity.' --workspace /absolute/workspace --output /absolute/new-run
sh scripts/run.sh run --plan /absolute/plan.json --output /absolute/new-run
sh scripts/run.sh resume --output /absolute/existing-run
```

The launcher requires Python 3.11 or newer and probes supported installed interpreters. `RESEARCH_WORKBENCH_PYTHON` may select one explicitly. Global `--registry DIRECTORY` and `--skills-root DIRECTORY` precede the command; `register` has its own required registry argument. Use runtime `--help` and the current adapter descriptors for capability names and parameter schemas.

Ask only for critical missing scientific choices while continuing independent inspection. Keep database retrieval, geometry screens, parameter/export checks and scientific acceptance distinct. Deliver available offline interactive 3D views; native VESTA figures require a scene and actual visual inspection. Never infer human approval.
