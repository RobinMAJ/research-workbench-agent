# Attribution and license scope

The original Research Workbench agent instructions, launcher, orchestration code, tests and documentation are covered by the MIT [LICENSE](LICENSE). Copyright (c) 2026 Jiabin Ma.

The installed source skills are separate dependencies. Research Workbench calls their declared interfaces and preserves their provenance; it does not replace their licenses or claim authorship of their scientific methods. Consult each source skill's own license, notices, validation documentation and dependency terms:

- `atomistic-structure-builder`: original MIT workflow integration, with separately licensed ASE, pymatgen, spglib and other dependencies. Its vendored 3Dmol.js renderer and bundled components retain the notices in that skill's `assets/vendor/` directory. Generated offline viewers retain renderer notices.
- `forcefield-generator`: original MIT workflow integration, audits and fitting helper. AmberTools/GAFF2, OpenFF, OpenMM, ParmEd, RDKit, parameter data and other dependencies retain their own licenses and authorship. AuToFF inspired that skill's capability design; AuToFF application code and parameter databases are not bundled by Research Workbench.
- `vesta-figure-maker`: original workflow integration is separate from the VESTA application. VESTA is vendor-licensed software, not an open-source dependency supplied by this package. Users must comply with its applicable terms.

- `vmd-figure-studio`: the original Python/Tcl companion is a separate source Skill. Native VMD and Tachyon are installed and licensed separately. Its offline viewer uses 3Dmol.js under its own BSD-3-Clause and bundled dependency notices; those assets remain with the source Skill or generated viewer bundle and are not relicensed by Research Workbench.

Materials Project content, user-provided molecular structures, QM reference data, generated parameter sets and subsequently downloaded data retain their applicable source terms. This package's license does not relicense them. Cite the actual database, force field, charge method and fitting/reference method used in a scientific result.

Codex is an external runtime. Its account access, service terms and usage limits apply separately. Future extension authors must declare their skill and adapter provenance and preserve required third-party notices; registration is not a license grant.
