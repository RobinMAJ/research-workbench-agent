# VESTA and VMD visualization routing

Research Workbench selects a visualization route from the requested scientific
presentation, supplied data and discovered runtime readiness. The Agent and
natural-language planner make this choice using the relevant installed Skill
instructions. There is no keyword classifier that changes a typed plan behind
the user's back. A supplied JSON plan executes its declared capability.

## Selection order

1. **Honor the user's explicit renderer.** A request for VESTA remains a VESTA
   task; a request for VMD remains a VMD task. An unavailable tool must be
   explained. Prepare authorized portable work or report the missing dependency;
   do not silently rename an alternative image as the requested tool's output.
2. **Choose by the scientific view.** Use the defaults below when no tool is
   specified. Read the source Skill's relevant schema and format documentation.
3. **Check readiness.** The Workbench reports discovered source instructions,
   launcher/interpreter paths and native runtime paths without launching a
   renderer or reading authentication files. Discovery is not proof that a
   native export will succeed. Actual execution and artifact checks establish
   the technical result.
4. **State the decision.** Planning `assumptions` name the selected renderer or
   independent inspection route, the reason, and any missing runtime or
   portable-only limitation. Explain the same decision to the user.

| Requested presentation | Default route | Reason and boundary |
| --- | --- | --- |
| Periodic crystal, coordination polyhedra or Miller planes | VESTA | Crystal-specific representation and existing VESTA display settings. |
| Existing embedded `.vesta` scene | VESTA | Preserve the intended reproducible scene. Linked-import scenes require explicit dependency handling. |
| Molecule, protein, cartoon or molecular surface | VMD | Molecular selections, topology and biological representations. |
| Topology/trajectory pair, selected frames or trajectory movie | VMD | Explicit atom ordering, frame identity and consistent camera. |
| Orbital lobes, ESP or potential-colored molecular surface | VMD | Signed fields and separate surface/map definitions with explicit units and range. |
| Headless render, consistent batch or rotation movie | VMD | Scriptable native rendering and a shared scene recipe. A rotation movie is not MD. |
| Crystal electron density or difference density | Choose from the desired view | Both tools can be relevant. Polyhedra/planes favor VESTA; scripted mapped surfaces or frame/batch comparisons may favor VMD. The file extension alone does not choose. |
| Offline 3D inspection without native VMD | Independent viewer | The VMD Skill's `viewer` operation produces a 3Dmol.js artifact. It is not a native VMD or VESTA render. |

The defaults guide the Agent; they do not override an explicit user request.
When no renderer was fixed and the preferred runtime is unavailable, the Agent
may select an available, scientifically suitable alternative within the
authorized task and explain the change. If representation or physical meaning
would change, ask for the missing scientific choice instead.

## Installed dependencies

Research Workbench coordinates separately installed source Skills; it does not
bundle VESTA or VMD. Install the relevant source Skill and its Python dependencies
using that Skill's English README:

- [VESTA Figure Maker](https://github.com/RobinMAJ/vesta-figure-maker)
- [VMD Figure Studio](https://github.com/RobinMAJ/vmd-figure-studio)

Use `research-workbench doctor` and `research-workbench capabilities` from an
authorized execution session to inspect current integration readiness. The
read-only natural-language planner receives a discovery snapshot and does not
launch a renderer, install software or change runtime settings.

VMD is separately licensed and obtained by the user. The VMD Skill discovers an
explicit native executable, its local runtime configuration, or an executable
on `PATH`. A configured Python interpreter is distinct from the native VMD
application. Python and Tcl alone do not replace VMD's native renderer.

## Operation contracts

### Native VESTA export

```json
{
  "id": "crystal_figure",
  "capability": "vesta-figure-maker",
  "operation": "render",
  "depends_on": [],
  "params": {"scene": "crystal.cif", "scale": 4},
  "timeout_seconds": 600
}
```

The `scene` is an existing local CIF/POSCAR or embedded VESTA file. The adapter
retains native export and image-integrity evidence. A default structure export
does not by itself establish a polished or approved publication figure.

### VMD portable scene or native render

`compile` and `render` take an **inline native scene object** under `params.spec`.
The adapter writes that object in its new stage directory. This avoids asking
the read-only planner to create a scene file. Original sources are named under
`spec.inputs[].path` so the coordinator resolves and hashes them.

The following is a complete small plan for a supplied coordinate trajectory:

```json
{
  "schema_version": 1,
  "title": "Prepare a portable trajectory scene",
  "steps": [
    {
      "id": "trajectory_scene",
      "capability": "vmd-figure-studio",
      "operation": "compile",
      "depends_on": [],
      "params": {
        "spec": {
          "schema": "vmd-figure-studio-scene-v1",
          "title": "Selected trajectory frames",
          "inputs": [
            {"path": "trajectory.xyz", "type": "xyz", "role": "structure"}
          ],
          "representations": [{"style": "CPK"}],
          "camera": {"center": [0, 0, 0], "height": 12},
          "animation": {"frames": [0, 10, 20]},
          "output": {"width_px": 1600, "height_px": 1200, "dpi": 300, "format": "png"}
        }
      },
      "timeout_seconds": 600
    }
  ]
}
```

The example requires the supplied file to contain those frames. Its camera is a
presentation default, not a suitable physical scale for every system. Replace
it from the inspected geometry. XYZ does not carry molecular topology or all
periodic metadata; use a matched native topology/trajectory pair when needed,
with atom ordering explicitly confirmed according to the source scene schema.

Use operation `render` with the same inline contract to request native VMD
rendering. The optional `params.timeout_seconds` is bounded by the stage deadline.
Follow the installed VMD scene schema for selections, representations, field
inputs, palettes, camera, output dimensions and animations. Arbitrary Tcl or
shell hooks are not accepted by that declarative schema.

Portable compilation exports `scene`, `compiled_manifest` and `manifest`
artifacts. Compilation is not rendering. Native rendering publishes `figure`
and `preview` only when the source renderer reports verified output and the
coordinator can validate the declared files. Native execution also retains its
`native_manifest` when available. If VMD is unavailable, rendering is explicitly
blocked while portable scene artifacts can remain available.

To render portable output on another machine, install the source VMD Skill and
a separately licensed VMD runtime there, transfer the complete scene bundle,
and follow the source Skill's `render` command. Keep source data, manifests,
runtime version and actual output evidence together. Remote execution is a
separate authorized action; producing the bundle does not submit remote work.

### Independent offline inspection

```json
{
  "id": "offline_inspection",
  "capability": "vmd-figure-studio",
  "operation": "viewer",
  "depends_on": [],
  "params": {
    "input": "molecule.xyz",
    "input_format": "xyz",
    "title": "Independent molecular inspection",
    "volumes": [
      {"path": "signed-field.dx", "format": "dx", "isovalue": 0.05, "units": "arbitrary_unit", "signed": true}
    ]
  },
  "timeout_seconds": 600
}
```

`title` and `volumes` are optional. Field formats for this adapter are `dx` or
`cube`; provide a positive threshold magnitude and the actual scalar units.
`arbitrary_unit` is suitable only for a field that is genuinely defined that
way, such as an explicitly labeled analytical demonstration. The operation
does not need native VMD. It creates a bundled offline viewer and manifest;
browser/WebGL inspection is a separate observable check, not inferred from
successful HTML generation.

## Multi-stage handoffs

Later stages can reference an earlier declared artifact using
`{"$ref":"step_id:artifact_key"}` and must depend on that step. For example,
`inputs[].path` may refer to `{"$ref":"adsorption:geometry"}` with an explicit `xyz` reader after an
atomistic structure build. Its `structure` artifact is metadata JSON, not a
native coordinate file. The coordinator resolves and verifies the original
artifact hash before the VMD adapter uses it. A viewer can similarly use a
structure or molecular geometry artifact as `params.input` when its actual
format is supported.

Choose only artifact keys that the producing adapter actually exports. The
VMD native operations require inline `spec`; they do not accept a compiled
scene path in place of that object. The Workbench's extensible registry remains
the contract authority for future operations and new Skills.

VMD operations expose their own `viewer`, `figure` and scene artifacts directly.
The older `visualization.collect` utility supports native structure-builder and
force-field manifests; do not pass a VMD manifest to that utility.

## Scientific and visual acceptance

- Geometry alone cannot supply density, orbitals or electrostatic potential.
  Request the original calculated field and the missing scientific definition;
  do not synthesize a field or launch an unrequested electronic calculation.
- Record quantity, units, dataset/spin/orbital index, sign convention and any
  potential reference zero. Define a mapped surface independently from the
  mapped scalar, range and units. A numerical colorbar is required for a
  quantitative color map.
- Preserve atom ordering, periodic cells and field registration. Difference
  fields need compatible grids and a scientifically valid reference; neither a
  matching file extension nor a visible surface establishes compatibility.
- For comparisons, hold camera, projection, physical scale, radius scales,
  scalar ranges and output dimensions consistent. Independent auto-fitting is
  not a common physical scale.
- Inspect every requested figure/frame for clipping, complete geometry,
  registration, sign colors, legible labels and comparison scale. Keep actual
  inspection evidence and disclose unverified browser/runtime behavior.
- `rendered_verified` is a technical result. `compiled_unrendered`, a blocked
  native runtime, an HTML viewer, or a successful process exit is not a finished
  native figure. Technical success does not imply scientific validity or human
  approval.

The broad format coverage and additional field/analysis/layout/movie tools live
in the source VMD Skill. This integration initially automates `compile`,
`render` and `viewer`; other source operations require an agent-assisted route
or a future explicit adapter. See [extensions.md](extensions.md) to add more
capabilities without changing the Agent's scientific boundaries.
