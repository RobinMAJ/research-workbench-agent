# Validation record

Validation began on 2026-10-10; final integration and extension checks completed on 2026-10-11. This record concerns technical integration, not scientific acceptance.

## Automated checks

The test suite passed 91 cases: 35 coordinator/state/evidence tests, 11 planner subprocess contract tests, 33 adapter/molecular-handoff tests and 12 extension-registration/execution tests. The coordinator uses Python's standard library; running the complete test suite also requires pytest, NumPy and RDKit in the existing force-field development runtime.

```sh
python3.12 -m pytest tests -q
```

Covered behavior includes dependency blocking, direct/transitive artifact references, hash drift, run locks, incomplete-state refusal, no overwrite, planner timeout/invalid output, complete requested force-field exports, credential-free MP blocking, lexical virtual-environment interpreter paths, and proper graph-preserving molecular coordinate transfer. A reflected chiral molecule, internal distortion or invalid atom mapping is rejected. Extension tests exercise actual trusted-adapter registration and execution, deferred imports, dataclass support, instruction-only blocking and code-drift refusal on resume.

## Real local LiF(100)/DMF workflow

The example reused the previously acquired local LiF crystal and PubChem CID 6228 explicit-hydrogen DMF SDF. It did not retrieve a new API key, perform a network database request, optimize the structure or evaluate adsorption energies.

| Stage | Observed outcome |
| --- | --- |
| LiF(100) surface | Configured geometry/export screens passed; 288 slab atoms. |
| DMF adsorption | 300-atom starting structure; carbonyl oxygen above the selected surface Li at 2.2 Angstrom. |
| Molecular handoff | 12 source-index atoms mapped bijectively; preserved graph, charges and stereochemistry; no bond inference. |
| Isolated DMF GAFF2/AM1-BCC | Molecular technical gate and requested OpenMM, Amber and GROMACS import/roundtrip checks passed. |
| Interactive views | Native structure and molecular viewer bundles retained; structure view opened in a browser and visibly rotated. |
| Native VESTA export | Scale-4 PNG exported through the installed zsh wrapper. |
| Whole LiF/DMF force field | Blocked: independently sourced LiF parameters and validated LiF-DMF cross interactions are absent. |

The original demonstration explicitly enabled the source skill's bounded charge-roundoff policy; raw and corrected charges remain recorded. Export comparisons use the source skill's reported reader/energy/force checks and do not establish a native GROMACS production simulation.

Visual inspection confirmed that the structure viewer displays the adsorption model and responds to rotation. The default-style VESTA PNG has substantial whitespace and a top view that does not resolve vertical adsorption height; it is an export demonstration, not an approved publication figure. Human visual approval and scientific acceptance remain pending.

The local integration fixtures and complete scientific run bundles are not included in this source distribution. Their results are summarized here as technical integration evidence. The repository includes a runnable molecule-only workflow using public DMF SMILES and an extension example; see the README for exact commands. Native result bundles were not modified to retrofit approval.

## Live natural-language planner

The real Codex CLI, using its existing ChatGPT login and read-only planner sandbox, returned a valid single-step plan for public ethanol SMILES `CCO`. The coordinator validated the typed plan, then the local force-field skill completed molecular preparation. No charge calculation, optimization or force-field assignment was performed in this preparation-only case.

The live planner test used published skill instructions and public molecular information in a separate workspace. Local LiF/DMF adapter execution used no external model call. For a new planning request, the user's configured Codex model service may receive the request and task-relevant input context; use a supplied typed plan when no new model planning call is intended.

## Remaining boundaries

These examples do not validate every crystal phase, grain-boundary model, force-field chemistry or future extension. Native VESTA linked-import scenes currently need an explicit dependency-bundle adapter or agent-assisted source-skill handling. Interrupted stages require a process/output audit and a new run; resume does not silently retry them. Availability checks alone do not benchmark dependencies.
