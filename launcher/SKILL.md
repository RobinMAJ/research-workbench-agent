---
name: research-workbench
description: Route scientific requests to the Research Workbench custom Agent and its deterministic capability runner. Coordinate structure, molecular force fields, fitting and extensible research workflows, automatically choosing VESTA or VMD visualization from the request and runtime readiness.
---

# Research Workbench launcher

This skill is the discovery and invocation router for the persistent Codex custom Agent `research_workbench` and the `research_workbench` Python runner. The scientific instructions remain in the installed source skills and their adapters.

## Route the request

1. Delegate the research task to the custom Agent `research_workbench` when the current Codex runtime supports custom agents. Include the user's objective, workspace, inputs, constraints and approval boundaries. Report delegation only after the runtime confirms it.
2. Inspect readiness with the installed launcher's `doctor` and `capabilities` commands. Read the relevant source skill for scientific decisions and its declared adapter contract for execution. A listed instruction-only skill requires agent-assisted work rather than an executable plan step.
3. For a natural-language request through the local CLI, use `ask` to obtain a typed plan from the read-only Codex planner and execute ready operations. Use `--plan-only` when the user requests planning. This uses the existing Codex login and consumes Codex usage.
4. For an existing ready plan, use `run`. Use `resume` to verify and reuse a terminal recorded run. Audit interrupted or running evidence before starting a new run; the runner refuses automatic continuation. Preserve new-directory and integrity requirements. Inspect the results and return the reported artifacts and remaining scientific limitations.

## Select visualization

Honor an explicit VESTA or VMD request. Otherwise, prefer VESTA for periodic
crystals, coordination polyhedra, Miller planes and existing `.vesta` scenes;
prefer VMD for molecules, proteins, trajectories, orbital/ESP surfaces,
potential-colored surfaces, headless rendering, consistent batches and movies.
For overlapping crystal density work, select by the desired representation and
comparison rather than by file extension. Inspect runtime readiness, state the
selected renderer and reason, and record both in planning assumptions.

Use the VMD adapter's `compile` or `render` operation with an **inline native
scene spec**, with original files under `inputs[].path`. The read-only planner
must not create scene files. `compile` yields a portable scene and manifests;
native rendering remains pending. Missing native VMD does not prevent portable
compilation or the independent `viewer` operation. The viewer is an offline
3Dmol.js inspection artifact, not a native VMD image. Keep an explicitly requested
renderer when unavailable, explain the missing dependency and preserve portable
work rather than silently substituting another image.

Check physical quantity, field units, sign/reference conventions and atom/field
registration before using density, orbital or potential data. Geometry alone
cannot supply these fields. Read `docs/visualization-routing.md` in the installed
Workbench package for route examples, artifact handoffs and limitations.

Resolve this skill's directory before invoking its launcher:

```sh
sh scripts/run.sh doctor
sh scripts/run.sh capabilities
sh scripts/run.sh ask --request 'Prepare the supplied molecule and audit its identity.' --workspace /absolute/workspace --output /absolute/new-run
sh scripts/run.sh run --plan /absolute/plan.json --output /absolute/new-run
sh scripts/run.sh resume --output /absolute/existing-run
```

The launcher requires Python 3.11 or newer and probes supported installed interpreters. `RESEARCH_WORKBENCH_PYTHON` may select one explicitly. Global `--registry DIRECTORY` and `--skills-root DIRECTORY` precede the command; `register` has its own required registry argument. Use runtime `--help` and the current adapter descriptors for capability names and parameter schemas.

Ask only for critical missing scientific choices while continuing independent inspection. Keep database retrieval, geometry screens, parameter/export checks and scientific acceptance distinct. Deliver available offline interactive 3D views; native VESTA/VMD figures require a reproducible scene and actual visual inspection. State whether an output is a native render, a portable unrendered scene or an independent viewer. Never infer human approval.
