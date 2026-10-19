# Research Workbench Agent

Research Workbench coordinates scientific requests across installed skills and executes supported local workflows with typed plans, explicit artifact handoffs and recorded evidence. Its initial capabilities cover atomistic structures, molecular force fields, automatic VESTA/VMD figure routing, native exports, portable VMD scenes and offline interactive 3D views. New research skills can join through a declared capability registry.

Repository and share link: [RobinMAJ/research-workbench-agent](https://github.com/RobinMAJ/research-workbench-agent).

The distribution has three working parts:

| Part | Role |
| --- | --- |
| Codex custom Agent `research_workbench` | Clarifies the research objective, resolves critical scientific choices, reads relevant source skills, plans work and inspects results. |
| Python package `research_workbench` | Validates typed plans, runs deterministic adapters, records hashes and statuses, and checks recorded evidence before resume. |
| Launcher skill `$research-workbench` | Makes the Agent and runner discoverable in Codex and routes a request to them. |

The Agent is defined in [agents/research_workbench.toml](agents/research_workbench.toml), with the standalone `name`, `description` and `developer_instructions` fields documented by Codex. It inherits the parent session's model, reasoning effort and permissions. See the [official custom agent documentation](https://learn.chatgpt.com/docs/agent-configuration/subagents#custom-agents).

The installed Agent definition is reusable across Codex sessions. Each invocation runs through the current Codex runtime; plans and recorded run artifacts carry the execution state between invocations.

## Install and check readiness

Use Python 3.11 or newer for the coordinator, Git for cloning, and a signed-in Codex CLI for natural-language planning. The coordinator uses the standard library. Its deterministic runner can execute a supplied plan without a model call; scientific operations use the separate source skills and their environments.

Install the Agent and launcher:

```sh
git clone https://github.com/RobinMAJ/research-workbench-agent.git
cd research-workbench-agent
sh scripts/bootstrap.sh install
```

Install the scientific routes you need:

| Source skill | Setup requirements |
| --- | --- |
| [Atomistic Structure Builder](https://github.com/RobinMAJ/atomistic-structure-builder) | Python 3.12+; its setup script creates a skill-local virtual environment. Materials Project retrieval also needs local API access. |
| [Force-field Generator](https://github.com/RobinMAJ/forcefield-generator) | Conda; its setup script creates a pinned Python 3.12 prefix with AmberTools/GAFF2 and OpenFF dependencies. The environment was tested on macOS arm64. |
| [VESTA Figure Maker](https://github.com/RobinMAJ/vesta-figure-maker) | VESTA installed separately. The packaged native-export adapter supports macOS with `/Applications/VESTA.app`. |
| [VMD Figure Studio](https://github.com/RobinMAJ/vmd-figure-studio) | Python 3.10+ and its requirements; native VMD is separately installed/licensed. Portable scene compilation and independent offline preview work without native VMD. |

For a fresh installation of all four source skills:

```sh
workbench_skills="${CODEX_HOME:-$HOME/.codex}/skills"
mkdir -p "$workbench_skills"
git clone https://github.com/RobinMAJ/atomistic-structure-builder.git "$workbench_skills/atomistic-structure-builder"
git clone https://github.com/RobinMAJ/forcefield-generator.git "$workbench_skills/forcefield-generator"
git clone https://github.com/RobinMAJ/vesta-figure-maker.git "$workbench_skills/vesta-figure-maker"
git clone https://github.com/RobinMAJ/vmd-figure-studio.git "$workbench_skills/vmd-figure-studio"
sh "$workbench_skills/atomistic-structure-builder/scripts/setup_environment.sh"
sh "$workbench_skills/forcefield-generator/scripts/setup_environment.sh"
python3.12 -m venv "$workbench_skills/vmd-figure-studio/.venv"
"$workbench_skills/vmd-figure-studio/.venv/bin/python" -m pip install \
  -r "$workbench_skills/vmd-figure-studio/requirements.txt"
```

If a skill is already installed, inspect and reuse it rather than cloning or replacing its environment. The structure setup includes the MP client by default; add `--without-mp` for local crystal files only. `ATOMISTIC_BUILDER_BASE_PYTHON` can select an existing Python 3.12+ base interpreter. Conda must be on `PATH`, or set `FF_GENERATOR_CONDA` to its executable. Setup downloads scientific dependencies; the Workbench installer itself does not install them. A WebGL-capable browser is needed for interactive views.

If native VMD is available, configure it through the source Skill (replace the
sample path with an actual executable or macOS app):

```sh
"$workbench_skills/vmd-figure-studio/.venv/bin/python" \
  "$workbench_skills/vmd-figure-studio/scripts/vmd_studio.py" configure --vmd "/path/to/VMD.app"
```

Without native VMD, skip native configuration and use portable `compile` or
independent `viewer` operations. An existing VMD Studio runtime configuration
is reused. `VMD_STUDIO_PYTHON` selects the VMD Skill's interpreter explicitly;
`VMD_EXECUTABLE` selects the native executable. The interpreter must have the
source Skill's dependencies. The Workbench does not install or redistribute VMD.

The bootstrap script selects an existing supported interpreter; it does not install or change Python. If the system `python3` is too old, set `RESEARCH_WORKBENCH_PYTHON` to an existing Python 3.11+ executable before installation. Alternatively, use a supported interpreter directly, for example `python3.12 scripts/install.py`. From the cloned directory, bootstrap also supports commands such as `sh scripts/bootstrap.sh doctor` and `sh scripts/bootstrap.sh capabilities`.

The installer uses `CODEX_HOME` when set, otherwise `~/.codex`. It copies the package, built-in descriptors, documentation and launcher into `skills/research-workbench`, and the custom Agent into `agents/research_workbench.toml`. `--codex-home DIRECTORY` selects a different Codex home, for example `sh scripts/bootstrap.sh install --codex-home /absolute/codex-home`. An existing target with different content is refused rather than overwritten. Source skills and scientific dependencies are not installed by this command.

Start a new Codex session after installation so it can discover the Agent and launcher. Ask: “Use the `research_workbench` Agent to prepare my scientific workflow,” or invoke `$research-workbench` with your request and files.

The installed launcher can be called from any working directory:

```sh
sh "${CODEX_HOME:-$HOME/.codex}/skills/research-workbench/scripts/run.sh" doctor
sh "${CODEX_HOME:-$HOME/.codex}/skills/research-workbench/scripts/run.sh" capabilities
```

Use your configured Codex home if it differs from `~/.codex`. The launcher probes for Python 3.11 or newer: a valid `RESEARCH_WORKBENCH_PYTHON`, versioned `python3.14` through `python3.11` on `PATH`, then `python3`, and finally the installed force-field skill's Python. It gives a clear error if none is available. The source-skill interpreter overrides `ATOMISTIC_BUILDER_PYTHON`, `FF_GENERATOR_PYTHON` and `VMD_STUDIO_PYTHON` select those scientific runtimes when needed. Doctor reports observed readiness; listing a capability does not establish that its tools are available or that a scientific task has run.

For a standalone Python CLI distribution, build a wheel locally with an existing Python 3.11+ environment containing setuptools 77+ and wheel:

```sh
python3.12 -m pip wheel --no-build-isolation --no-deps --no-index \
  --wheel-dir /absolute/new-wheel-directory .
python3.12 -m pip install --no-deps --no-index \
  --target /absolute/python-packages \
  /absolute/new-wheel-directory/research_workbench_agent-0.2.0-py3-none-any.whl
PYTHONPATH=/absolute/python-packages python3.12 -m research_workbench capabilities
```

The wheel contains the capability descriptors and Agent instructions as package resources, so the CLI works from an unrelated working directory. This installs the Python runner into the selected target; use the Codex installer above to register the custom Agent and launcher. Scientific source skills and their environments remain separate dependencies.

## Update an existing installation

Update the cloned repository with `git pull --ff-only`, then inspect its release
notes and preserve your current installed launcher and Agent definition before
running the installer again. Put backups outside the discoverable `skills/`
directory. The installer deliberately refuses to overwrite a differing target.
Keep external registries and source-Skill environments intact. For a staged
check, install with `--codex-home /absolute/new-staging-home`, inspect readiness,
and then replace only the managed Workbench launcher/Agent after preserving
the old files. Use a new Codex session for the updated Agent definition.

Do not migrate an active scientific run by swapping its coordinator code.
Recorded runs pin coordinator and Skill fingerprints; changed code requires
inspection and a new run rather than automatic continuation. Existing terminal
evidence is preserved with the previous installation and its original version.

## Plan from a research request

`ask` runs a read-only Codex planner, saves its structured planning result, and passes a ready typed plan to the deterministic runner:

```sh
sh "${CODEX_HOME:-$HOME/.codex}/skills/research-workbench/scripts/run.sh" ask \
  --request 'Prepare the supplied DMF molecule, generate a GAFF2 candidate with AM1-BCC charges, and deliver its checks and interactive 3D view.' \
  --workspace /absolute/project \
  --output /absolute/new-workbench-run
```

The installed launcher works from any directory and selects a supported interpreter. From the cloned source directory, `sh scripts/bootstrap.sh ask ...` is equivalent. A standalone Python installation can use `python3 -m research_workbench ...` with Python 3.11+ and the package available on `PYTHONPATH`. Add `--plan-only` to save the plan without executing scientific adapters. Global `--registry DIRECTORY` and `--skills-root DIRECTORY` options precede the command, for example `sh "${CODEX_HOME:-$HOME/.codex}/skills/research-workbench/scripts/run.sh" --registry /absolute/local-registry capabilities`.

This path uses the existing local Codex CLI login; no new paid API service is required. Codex planning still consumes your Codex usage, and account limits apply. `codex exec` supports read-only execution, structured output and saved authentication as described in the [official non-interactive documentation](https://learn.chatgpt.com/docs/non-interactive-mode). A request and non-secret workspace context are sent to Codex for planning; deterministic adapter execution is local unless a declared operation uses an external service.

Give the Agent the input files and scientific choices that matter: authoritative phase or molecular identity, charge and multiplicity, intended boundary conditions, backend and charge method, surface orientation, geometry constraints, and relevant validation thresholds. It asks for critical missing decisions and continues independent work when possible. Planning can report missing inputs or readiness barriers; those are not executable scientific results.

For Materials Project, obtain access through its [API portal](https://next-gen.materialsproject.org/api) and configure the secret locally through the structure skill. Never paste a key into chat or include it in a plan, descriptor, viewer or shared output. Select the intended material explicitly and record the selection reason. XRD can support candidate-phase identification, but a trace alone does not necessarily determine a unique crystal structure.

## Run a typed plan

```sh
sh "${CODEX_HOME:-$HOME/.codex}/skills/research-workbench/scripts/run.sh" run --plan /absolute/plan.json --output /absolute/new-run
sh "${CODEX_HOME:-$HOME/.codex}/skills/research-workbench/scripts/run.sh" resume --output /absolute/existing-run
```

New runs require a new output directory. A plan contains `schema_version: 1`, a nonempty `title` and `steps`. Each step declares `id`, `capability`, `operation`, `depends_on` and `params`; `timeout_seconds` defaults to 600 and is bounded to 1–3600 seconds. IDs and dependencies must be valid and acyclic. File inputs are resolved relative to the plan file. Use a declared dependency and a nested `{"$ref":"step_id:artifact_key"}` to pass an artifact from one completed stage to another. The runner checks the referenced file's hash before use.

Resume verifies and reuses a terminal recorded run with status `complete`, `blocked` or `failed`. It checks the recorded plan, input hashes, artifacts, coordinator code, relevant skill/adapter fingerprints and runtime metadata. It does not retry failed stages or continue interrupted/running stages. Audit interrupted evidence and recorded processes before starting a new run; use a new run for changed scientific choices or inputs.

### Molecule-only example

The complete [DMF example plan](examples/workflows/dmf-gaff2.json) requires no separate input file. It prepares neutral, closed-shell DMF from explicit SMILES, generates an isolated GAFF2 candidate from the prepared SDF, requests OpenMM/Amber/GROMACS exports, and collects the existing offline 3D viewer.

From the cloned source directory:

```sh
sh scripts/bootstrap.sh run --plan examples/workflows/dmf-gaff2.json --output /absolute/new-run
```

After Codex installation, from any working directory:

```sh
sh "${CODEX_HOME:-$HOME/.codex}/skills/research-workbench/scripts/run.sh" run \
  --plan "${CODEX_HOME:-$HOME/.codex}/skills/research-workbench/examples/workflows/dmf-gaff2.json" \
  --output ./dmf-run
```

The output directory must be new. SMILES preparation produces a seeded conformer; it is not preserved experimental geometry. AM1-BCC requires the force-field skill's AmberTools environment. The example explicitly enables a bounded charge-roundoff correction policy; review that choice before adapting the plan to other chemistry.

### Structure-to-molecule handoff

A selected adsorption structure supplies coordinates and an atom map, while an authoritative explicit-hydrogen SDF supplies the molecular bond graph. The `handoff` operation preserves that graph and verifies mapped coordinates; it does not infer bonds from XYZ, CIF or POSCAR proximity. A periodic slab remains a separate scientific system: parameterizing the extracted molecule does not supply inorganic or cross-interaction parameters.

For a preceding structure `build` stage named `adsorption`, these later steps demonstrate the handoff and native rendering:

```json
[
  {
    "id": "positioned-molecule",
    "capability": "forcefield-generator",
    "operation": "handoff",
    "depends_on": ["adsorption"],
    "params": {
      "graph_sdf": "inputs/authoritative-molecule.sdf",
      "geometry": {"$ref": "adsorption:geometry"},
      "atom_map": {"$ref": "adsorption:atom_map"}
    }
  },
  {
    "id": "native-figure",
    "capability": "vesta-figure-maker",
    "operation": "render",
    "depends_on": ["adsorption"],
    "params": {
      "scene": {"$ref": "adsorption:structure_cif"},
      "scale": 4
    }
  }
]
```

This is a plan fragment; add it to a complete plan after the configured build step and supply the SDF. Select an explicit structure candidate in the build params. Direct CIF/POSCAR rendering uses VESTA's default presentation. Prepare an embedded `.vesta` scene when a reproducible styled scientific figure is needed. The native render adapter records image export; visual inspection and any requested human approval remain separate.

## Current execution routes

| Capability | Deterministic operations | Evidence boundary |
| --- | --- | --- |
| `atomistic-structure-builder` | `build`, `audit`, `mp-fetch`, `mp-select` | Selected structure and configured geometry/export checks; database retrieval and explicit material selection. |
| `forcefield-generator` | `prepare`, `generate`, `audit`, `audit-mixture`, `handoff` | Generation for one connected, closed-shell organic molecule; isolated native audits, supplied compatibility metadata and graph/coordinate mapping. |
| `vesta-figure-maker` | `render` | Native export from an embedded scene or explicit periodic structure; actual visual review remains pending. |
| `vmd-figure-studio` | `compile`, `render`, `viewer` | Inline scenes with hash-verified input bundles; verified native frames, portable unrendered scenes or independent offline viewers. |
| `visualization` | `collect` | Hash-verified references to the source result's offline viewer and evidence bundle. |

Use `capabilities` for the installed catalog and each source skill's documentation for its scientific input schema. Supplied torsion fitting and specialist workflows can also be coordinated through the relevant source skill in the Agent conversation; only operations declared by the installed execution registry can be placed in a deterministic plan.

## Automatic choice between VESTA and VMD

The Agent and the read-only natural-language planner select the visualization
route from the requested scientific view and installed readiness. They record
the chosen renderer and reason in planning assumptions. A supplied typed plan
executes its declared route; the runner does not rewrite it.

| Request | Default selection |
| --- | --- |
| Explicit VESTA or VMD request | Honor the requested tool; explain a missing runtime. |
| Periodic crystals, coordination polyhedra, Miller planes, embedded VESTA scenes | VESTA |
| Molecules/proteins, trajectories, orbitals, ESP, potential-mapped surfaces | VMD |
| Headless rendering, consistent batches, rotation sequences | VMD |
| Crystal density/difference-density figures | Decide from the desired representation; both tools can be appropriate. |
| No native VMD and an interactive inspection request | Independent offline 3Dmol.js viewer |
| No native VMD and a native VMD figure request | Prepare an authorized portable scene or report the blocked native render. |

Examples for `$research-workbench` or the CLI `ask` command:

> Show the coordination polyhedra and (100) plane of this crystal. Choose the
> appropriate visualization tool and explain the choice.

> Render frames 0, 20 and 40 of this supplied trajectory with one camera and
> physical scale. Choose the suitable renderer.

> Map this supplied potential onto the supplied density surface. Preserve the
> numerical units and range, and retain the native scene and evidence.

> I have no VMD installed. Deliver an offline rotatable view and a portable
> native scene that I can render on another authorized machine later.

Read [the routing and operation guide](docs/visualization-routing.md) for full
inline VMD plan examples, field definitions, artifact handoffs and no-local-VMD
instructions. The adapter materializes `params.spec` in a new stage directory;
all `spec.inputs[].path` dependencies are resolved and hashed by the coordinator.
A scene JSON filename alone is not accepted because it would hide its nested
input dependencies from the Workbench plan.

For existing workflows, a VMD input can use a declared artifact reference such
as `{"$ref":"adsorption:geometry"}` with its correct `xyz` reader. Structure
builder `structure` artifacts are metadata JSON, not native coordinate files;
use its `geometry`, `structure_cif` or `poscar` artifact as appropriate. Preserve
the complete source format and cell/topology evidence.

`compile` can complete without producing a native image. A missing native VMD
blocks `render` and retains the portable evidence instead of reporting a figure.
`viewer` bundles the original data and local browser library, and leaves WebGL
review pending. Native `render` exposes `figure`/`preview` only after verifying
the source scene/input hashes and every declared rendered image. Multi-frame
results remain together in the result bundle. Both native routes retain visual
review and scientific acceptance as separate gates.

### Runnable example without native VMD

The [portable water plan](examples/workflows/vmd-portable-water.json) uses an
included [original three-frame coordinate fixture](examples/data/README.md):

```sh
sh scripts/bootstrap.sh run --plan examples/workflows/vmd-portable-water.json \
  --output /absolute/new-portable-water-run
```

This executes VMD Skill compilation and independent offline viewer creation,
without launching native VMD. It needs the installed VMD source Skill and its
Python environment. Open the returned viewer together with its local assets.
The three frames are prescribed rigid translations, not an MD simulation.
To request native frames on an installed VMD machine, change the first step's
operation to `render` in a separate plan and use a fresh output directory.

## Deliverables and interpretation

Each run retains a normalized `plan.json`, `manifest.json`, a readable `report.md`, and an `index.html` linking artifacts. Stage directories contain inputs, subprocess logs and recorded handoff evidence. Native manifests and checks stay with their outputs. Preserve the whole run when archiving or transferring linked results.

Native structure and molecular outputs automatically provide offline interactive 3D views when supported by their source skills. Open a linked `viewer.html` locally to rotate, pan, zoom and inspect atoms. Share viewers together with the relevant scientific evidence; private provenance paths in manifests should be reviewed before sharing a whole run. A viewer is a presentation of coordinates, not a physical validation or an energy calculation.

Execution `complete` means the configured technical route completed. Structure geometry screens do not establish relaxed stability, adsorption energies or the preferred binding configuration. Molecular parameter/export checks do not establish predictive accuracy, transferability or mixed inorganic/organic compatibility. Training RMSE alone does not establish a torsion correction's scientific adequacy. Human approval is recorded only when actually supplied.

## Extend the workbench

Register a source skill as an instruction-only route, or add an explicit trusted Python adapter for deterministic execution. Descriptor discovery does not import adapter code. The source distribution includes an [artifact-summary descriptor](examples/extensions/artifact-summary.json) and [Python adapter](examples/extensions/artifact_summary.py) as a bounded local metadata example; it does not validate scientific results. See [docs/extensions.md](docs/extensions.md) for its registration commands, the schema, trust boundary, callable interface and test expectations.

## Tested examples

Version 0.2.0 passed 142 focused tests: 35 coordinator tests, 18 planner tests using a fake CLI, 33 existing adapter tests, 35 VMD adapter tests, 9 visualization runtime/integrity tests and 12 extension tests. A real Codex planner also returned a ready one-step ethanol preparation plan; its deterministic execution completed preparation with scientific acceptance pending. Fake-CLI tests and a real planner invocation are distinct evidence.

The new VMD integration also completed a real native figure, portable scene compilation and independent offline viewer creation. With native VMD deliberately unavailable, compilation/viewer creation completed while native rendering remained blocked without a figure artifact. The included portable-water plan completed without launching native VMD. Two real read-only planner tests selected VESTA for a crystal and VMD for trajectory frames 0, 5 and 11; these were planning tests, not execution of those returned plans.

The packaged DMF plan completed all three stages: molecular preparation, GAFF2 parameter generation with the requested exports, and offline-viewer collection. This establishes the reported technical workflow; scientific acceptance remains pending.

A local LiF(100)/DMF demonstration completed seven stages: slab construction, adsorption construction, verified graph/coordinate handoff, isolated DMF GAFF2 parameter generation in three export formats, structure and molecular 3D-view collection, and a native VESTA PNG export at scale 4. Its eighth stage, the full-system compatibility audit, remained blocked because the LiF force-field model and required cross interactions were missing. The overall run therefore remains **blocked**, with scientific acceptance pending. This is an unrelaxed construction and isolated molecular parameterization demonstration, not a validated adsorption force field.

See [docs/VALIDATION.md](docs/VALIDATION.md) for the recorded artifact locations, checks and limitations. The scientific fixtures and local run bundles used for validation are not bundled with this distribution. The reusable DMF SMILES plan and extension example are included.

The original distribution license and separate source-skill/dependency terms are described in [NOTICE.md](NOTICE.md). No repository publication or remote calculation is performed by installation.
