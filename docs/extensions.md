# Declared skill extensions

Research Workbench can discover new scientific routes without adding a hard-coded branch to its coordinator. Each route has a descriptor naming a source skill or coordinator utility, operations, adapter, limits and side effects. Registering metadata makes that route visible; it does not establish that its dependencies are ready or that its methods are scientifically validated. See the [repository README](../README.md) for installation and the initial source-skill dependencies.

## Descriptor schema

An extension directory contains descriptor JSON files directly in that directory and optional adapter modules under `extensions/`:

```text
local-registry/
  spectrum-audit.json
  extensions/
    spectrum_adapter.py
```

The following descriptor illustrates an agent-assisted route; replace its skill name with an installed skill:

```json
{
  "schema_version": 1,
  "id": "spectrum-audit",
  "version": "1.0.0",
  "skill": "your-spectrum-skill",
  "description": "Inspect supplied spectral evidence using the source skill's method.",
  "operations": ["inspect"],
  "adapter": "instruction-only",
  "limits": {
    "input": "Supplied spectra and method metadata",
    "execution": "Agent-assisted; no automated adapter"
  },
  "side_effects": ["Writes a local evidence report within the authorized workspace"]
}
```

All fields shown are required. `schema_version` is `1`. IDs, skill IDs and operation names match `[a-z][a-z0-9-]{0,63}`; operations are a nonempty list with unique names. `version`, `description` and `adapter` are nonempty strings. `skill` names an installed source skill; coordinator utilities may use `null`, but an instruction-only route requires a skill ID. `limits` is an object and `side_effects` is an array. Choose a new ID; duplicate IDs, including collisions with built-ins, are rejected.

Supported adapter values are:

| Value | Behavior |
| --- | --- |
| `instruction-only` | Advertise an Agent route to the installed skill. A deterministic plan step returns `blocked` because no executor is supplied. |
| `python:MODULE:FUNCTION` | Load `extensions/MODULE.py` only when executing the registered operation, then call `FUNCTION`. |
| `builtin` | Reserved for the distribution's built-in capability IDs; it cannot declare a new executor. |

The registry is local metadata, not a remote plugin marketplace. Source-skill instructions remain authoritative for scientific method choices and required evidence. An instruction-only route can still be useful in the Agent conversation without pretending to be an executable adapter.

## Register an instruction-only route

```sh
sh "${CODEX_HOME:-$HOME/.codex}/skills/research-workbench/scripts/run.sh" register \
  --descriptor /absolute/spectrum-audit.json \
  --registry /absolute/local-registry
```

These examples use the installed launcher and work from any directory. From the cloned source directory, `sh scripts/bootstrap.sh ...` is equivalent. A standalone Python installation can use `python3 -m research_workbench ...` with Python 3.11+ and the package available on `PYTHONPATH`. The `register` command has its own required `--registry` argument. Subsequent discovery and execution use the global registry option before the command:

```sh
sh "${CODEX_HOME:-$HOME/.codex}/skills/research-workbench/scripts/run.sh" --registry /absolute/local-registry capabilities
sh "${CODEX_HOME:-$HOME/.codex}/skills/research-workbench/scripts/run.sh" --registry /absolute/local-registry run --plan /absolute/plan.json --output /absolute/new-run
```

Global `--skills-root DIRECTORY` also precedes the command and selects a nondefault installed-skills directory. The default is `skills` under `CODEX_HOME` when set, otherwise `~/.codex/skills`.

Registration validates the descriptor and stores it in the specified local registry. It does not install the named source skill or its dependencies. Preserve the descriptor's source and version, review readiness, and use a new descriptor/version when its contract changes.

## Register trusted Python code

Change the descriptor's adapter to `python:spectrum_adapter:execute` and explicitly register the reviewed module:

```sh
sh "${CODEX_HOME:-$HOME/.codex}/skills/research-workbench/scripts/run.sh" register \
  --descriptor /absolute/spectrum-audit.json \
  --adapter-file /absolute/spectrum_adapter.py \
  --trust-code \
  --registry /absolute/local-registry
```

`--trust-code` is a deliberate acknowledgement that this local module is executable code. Registration without that acknowledgement does not authorize Python adapter code. Review the module and its dependencies before registering it. Descriptor loading and capability discovery do not import modules; execution does. The Python adapter runs in the coordinator's process with its local permissions and is not a separate security sandbox.

The module and function names are simple Python identifiers. A descriptor points to one local file, not an arbitrary import path. The registry records descriptor and adapter fingerprints for provenance and resume checks. Registration does not grant permission to publish, send messages, launch remote computations or perform undeclared side effects.

## Included metadata-summary example

The source distribution includes [artifact-summary.json](../examples/extensions/artifact-summary.json) and [artifact_summary.py](../examples/extensions/artifact_summary.py). This coordinator utility has `skill: null` and a `summarize` operation. It hashes a supplied local native manifest and copies a small whitelist of scalar status/count fields into `summary.json`. Those fields remain source declarations: the example does not verify native artifact hashes, infer chemistry or establish scientific acceptance.

After reviewing the module, register the installed example explicitly from any working directory:

```sh
workbench_skill="${CODEX_HOME:-$HOME/.codex}/skills/research-workbench"
sh "$workbench_skill/scripts/run.sh" register \
  --descriptor "$workbench_skill/examples/extensions/artifact-summary.json" \
  --adapter-file "$workbench_skill/examples/extensions/artifact_summary.py" \
  --trust-code \
  --registry /absolute/local-registry
sh "$workbench_skill/scripts/run.sh" --registry /absolute/local-registry capabilities
```

An example plan step is:

```json
{
  "id": "metadata-summary",
  "capability": "artifact-summary",
  "operation": "summarize",
  "depends_on": ["molecule"],
  "params": {"manifest": {"$ref": "molecule:manifest"}}
}
```

Add this step after a stage named `molecule` that declares a `manifest` artifact, and execute the plan with the registered directory. The descriptor's optional `parameters` schema describes the accepted input; the adapter enforces its exact parameter keys and a 1 MiB input limit. It rejects duplicate JSON keys and nonfinite values and refuses to overwrite its output. Extension code is still trusted local code, so this example's boundaries do not sandbox other adapters.

## Callable contract

The adapter is called with positional arguments:

```python
def execute(capability, operation, params, attemptdir, skills_root, run_process):
    ...
```

| Argument | Meaning |
| --- | --- |
| `capability`, `operation` | Declared capability ID and requested operation. Reject unsupported combinations. |
| `params` | Nested parameter object after input normalization and declared artifact-reference resolution. Validate its scientific and implementation schema. |
| `attemptdir` | Existing stage attempt directory. Write distinct local outputs here; do not assume the directory itself is absent. |
| `skills_root` | Root directory of installed source skills. Read the named skill and execute its supported interface. |
| `run_process` | Logged subprocess helper: `run_process(argv, cwd=None, timeout=600, env=None) -> dict`. Use a list of arguments; no request-to-shell interpolation. |

Return a JSON-compatible dictionary:

```python
{
    "status": "complete",  # or "blocked" or "failed"
    "reason": "Configured input and output checks passed.",
    "artifacts": {"report": "/absolute/local/report.json"},
    "evidence": {"scientific_acceptance": "pending"},
    "details": {"method": "Declared source method and version"}
}
```

Artifact values must be existing local files with stable, descriptive keys. The runner hashes them before exposing `{"$ref":"stage_id:artifact_key"}` to later stages. Prefer outputs under the attempt directory; when referring to an existing native bundle, retain that bundle and verify its own manifest rather than claiming that a copied HTML file carries all scientific evidence.

`complete` means only that the operation met its declared technical contract. Use `blocked` for a missing scientific input, dependency or explicit choice; use `failed` for rejected technical checks or execution failure. Preserve partial artifacts and logs as diagnostic evidence. Include relevant method metadata, input/output mappings, source hashes and observed checks. Do not promote scientific or human acceptance through the adapter's execution status.

## Adapter acceptance and limits

Before registering an execution route, exercise the behavior that makes its result trustworthy:

- a realistic positive case with verified source and output artifacts;
- missing dependencies and unsupported or malformed inputs;
- failed scientific/technical gates with preserved negative evidence;
- nested artifact references, atom/data ordering or unit conversions where applicable;
- changed input, descriptor, adapter or artifact hashes on resume;
- timeout and subprocess failures, bounded output size and declared side effects;
- absence of secrets from plans, logs, shared viewers and descriptors.

Document which checks are technical, which are scientific, and which remain unresolved. A descriptor's `limits` and `side_effects` are declarations; they do not automatically enforce a sandbox or validate a scientific claim. Explicit enforcement belongs in the adapter, the source skill and host permissions. Cite the source skill's actual methods, dependency versions and licenses; do not relabel another project's implementation or parameter data as original Workbench code.
