"""Preserve an authoritative explicit-H SDF graph while moving mapped coordinates.

Runs only in the separately discovered force-field runtime (RDKit and NumPy).
No bond perception, hydrogen addition, charge assignment or optimization occurs.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shlex
import sys


MAX_ATOMS = 2000
RIGID_TOLERANCE_ANGSTROM = 0.005


def _hash(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _write(path, value):
    with Path(path).open("x") as stream:
        stream.write(json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n")


def _read_geometry(path):
    """Read explicit coordinate formats without relying on guessed topology."""
    if path.stat().st_size > 16 * 1024 * 1024:
        raise ValueError("Geometry exceeds the bounded input-size limit")
    if path.suffix.lower() == ".json":
        data = json.loads(path.read_text())
        if data.get("@class") not in {"Structure", "Slab", "Interface", "GrainBoundary"}:
            raise ValueError("Geometry JSON must be an explicit pymatgen Structure representation")
        sites = data.get("sites", [])
        if not isinstance(sites, list) or not 1 <= len(sites) <= MAX_ATOMS:
            raise ValueError("Geometry site count exceeds its bound")
        symbols, coordinates, properties = [], [], []
        lattice = data.get("lattice", {}).get("matrix")
        for site in sites:
            species = site.get("species")
            if not isinstance(species, list) or len(species) != 1 or abs(species[0].get("occu", 1) - 1) > 1e-8:
                raise ValueError("Geometry must contain fully occupied ordered sites")
            symbols.append(species[0]["element"])
            xyz = site.get("xyz")
            if xyz is None:
                abc = site.get("abc")
                if lattice is None or abc is None:
                    raise ValueError("Geometry is missing explicit Cartesian/fractional coordinates")
                xyz = [sum(float(abc[j]) * float(lattice[j][i]) for j in range(3)) for i in range(3)]
            coordinates.append(xyz)
            properties.append(site.get("properties", {}))
        return symbols, coordinates, properties, "MSON Cartesian coordinates in Angstrom"
    if path.suffix.lower() not in {".xyz", ".extxyz"}:
        raise ValueError("Handoff geometry must be single-frame XYZ/extxyz or Structure JSON; CIF/POSCAR do not provide molecular index authority")
    lines = path.read_text().splitlines()
    if len(lines) < 3:
        raise ValueError("Incomplete XYZ geometry")
    try:
        count = int(lines[0].strip())
    except ValueError:
        raise ValueError("XYZ atom count must be an integer") from None
    if not 1 <= count <= MAX_ATOMS or len(lines) < count + 2 or any(line.strip() for line in lines[count + 2:]):
        raise ValueError("XYZ must contain exactly one bounded frame")
    header = {}
    for token in shlex.split(lines[1]):
        if "=" in token:
            key, value = token.split("=", 1)
            header[key] = value
    fields = header.get("Properties", "species:S:1:pos:R:3").split(":")
    if len(fields) % 3:
        raise ValueError("Malformed extended XYZ Properties declaration")
    slices, width = {}, 0
    for i in range(0, len(fields), 3):
        size = int(fields[i + 2])
        if not 1 <= size <= 100 or fields[i] in slices:
            raise ValueError("Invalid extended XYZ field width or duplicate field")
        slices[fields[i]] = (width, width + size)
        width += size
    if "species" not in slices or "pos" not in slices or slices["species"][1] - slices["species"][0] != 1 or slices["pos"][1] - slices["pos"][0] != 3:
        raise ValueError("XYZ must declare species and Cartesian position fields")
    symbols, coordinates, properties = [], [], []
    for line in lines[2:count + 2]:
        values = shlex.split(line)
        if len(values) != width:
            raise ValueError("XYZ row width does not match its Properties declaration")
        a, b = slices["species"]
        symbols.append(values[a])
        a, b = slices["pos"]
        coordinates.append([float(x) for x in values[a:b]])
        prop = {}
        if "component" in slices:
            a, b = slices["component"]
            if b - a != 1:
                raise ValueError("XYZ component field must be scalar")
            prop["component"] = values[a]
        if "source_index" in slices:
            a, b = slices["source_index"]
            if b - a != 1:
                raise ValueError("XYZ source_index field must be scalar")
            prop["source_index"] = int(values[a])
        properties.append(prop)
    return symbols, coordinates, properties, "single-frame XYZ Cartesian coordinates in Angstrom"


def _identity(mol):
    from rdkit import Chem
    return {"canonical_isomeric_smiles": Chem.MolToSmiles(mol, isomericSmiles=True, allHsExplicit=True),
            "atoms": [{"index": a.GetIdx(), "element": a.GetSymbol(), "formal_charge": a.GetFormalCharge(),
                       "isotope": a.GetIsotope(), "chiral_tag": str(a.GetChiralTag()),
                       "radical_electrons": a.GetNumRadicalElectrons()} for a in mol.GetAtoms()],
            "bonds": [{"begin": b.GetBeginAtomIdx(), "end": b.GetEndAtomIdx(), "type": str(b.GetBondType()),
                       "stereo": str(b.GetStereo()), "stereo_atoms": list(b.GetStereoAtoms())} for b in mol.GetBonds()]}


def _mapped_indices(symbols, properties, atom_map, count):
    if atom_map is not None:
        data = json.loads(atom_map.read_text())
        rows = data.get("atom_mapping")
        if not isinstance(rows, list):
            raise ValueError("Atom map must use the structure builder atom_mapping schema")
        rows = [row for row in rows if row.get("component") == "adsorbate"]
        mode = "explicit adsorbate source_index/export_index bijection"
    else:
        rows = [{"source_index": p.get("source_index"), "export_index": i, "species": symbols[i]}
                for i, p in enumerate(properties) if p.get("component") == "adsorbate"]
        if rows:
            mode = "embedded adsorbate source_index bijection"
        elif len(symbols) == count:
            rows = [{"source_index": i, "export_index": i, "species": species} for i, species in enumerate(symbols)]
            mode = "caller-supplied source order; element sequence and proper rigid-coordinate fit verified"
        else:
            raise ValueError("Combined geometry needs an explicit adsorbate atom map; molecular identity is never inferred from neighbors")
    if len(rows) != count:
        raise ValueError("Mapped adsorbate atom count does not match the authoritative SDF")
    pairs, exported = {}, set()
    for row in rows:
        source, target = row.get("source_index"), row.get("export_index")
        if isinstance(source, bool) or not isinstance(source, int) or not 0 <= source < count or source in pairs:
            raise ValueError("Adsorbate source indices are not a complete unique bijection")
        if isinstance(target, bool) or not isinstance(target, int) or not 0 <= target < len(symbols) or target in exported:
            raise ValueError("Adsorbate export indices are invalid or duplicate")
        if row.get("species") != symbols[target]:
            raise ValueError("Atom map element disagrees with geometry")
        component = properties[target].get("component")
        if component is not None and component != "adsorbate":
            raise ValueError("Atom map points to a substrate/other component")
        embedded_source = properties[target].get("source_index")
        if embedded_source is not None and embedded_source != source:
            raise ValueError("Embedded source index disagrees with the supplied atom map")
        pairs[source] = target
        exported.add(target)
    return [pairs[i] for i in range(count)], mode


def handoff(graph_sdf, geometry, atom_map, output):
    from rdkit import Chem
    from rdkit.Geometry import Point3D
    import numpy as np
    paths = [graph_sdf, geometry] + ([atom_map] if atom_map else [])
    hashes = {str(p): _hash(p) for p in paths}
    if graph_sdf.suffix.lower() != ".sdf" or graph_sdf.stat().st_size > 16 * 1024 * 1024:
        raise ValueError("Authoritative graph must be a bounded SDF file")
    supplier = Chem.SDMolSupplier(str(graph_sdf), removeHs=False, sanitize=True)
    if len(supplier) != 1 or supplier[0] is None:
        raise ValueError("SDF must contain exactly one sanitizable molecule")
    mol = supplier[0]
    if not 1 <= mol.GetNumAtoms() <= MAX_ATOMS or len(Chem.GetMolFrags(mol)) != 1:
        raise ValueError("SDF must contain one connected molecule within the atom budget")
    if mol.GetNumConformers() != 1 or not mol.GetConformer().Is3D():
        raise ValueError("SDF needs one explicit 3D conformer")
    if any(a.GetNumImplicitHs() or a.GetNumExplicitHs() for a in mol.GetAtoms()):
        raise ValueError("SDF hydrogens must be explicit atom records; the handoff never adds atoms")
    identity = _identity(mol)
    symbols, positions, properties, coordinate_format = _read_geometry(geometry)
    positions = np.asarray(positions, dtype=float)
    if positions.shape != (len(symbols), 3) or not np.isfinite(positions).all():
        raise ValueError("Geometry coordinates must be complete and finite")
    indices, mapping_mode = _mapped_indices(symbols, properties, atom_map, mol.GetNumAtoms())
    if [symbols[i] for i in indices] != [a.GetSymbol() for a in mol.GetAtoms()]:
        raise ValueError("Source-index element sequence does not match the SDF")
    original = np.asarray(mol.GetConformer().GetPositions())
    selected = positions[indices]
    if not np.isfinite(original).all():
        raise ValueError("SDF coordinates must be finite")
    left = original - original.mean(axis=0)
    right = selected - selected.mean(axis=0)
    u, singular_values, vt = np.linalg.svd(left.T @ right)
    rotation = u @ np.diag([1, 1, np.linalg.det(u @ vt)]) @ vt
    fitted = left @ rotation + selected.mean(axis=0)
    residuals = np.linalg.norm(fitted - selected, axis=1)
    maximum = float(residuals.max(initial=0))
    if maximum > RIGID_TOLERANCE_ANGSTROM:
        raise ValueError("Mapped coordinates are not the preserved proper rigid SDF geometry; review reordering, periodic wrapping, reflection or internal distortion")
    candidate = Chem.Mol(mol)
    conformer = candidate.GetConformer()
    for i, xyz in enumerate(selected):
        conformer.SetAtomPosition(i, Point3D(*map(float, xyz)))
    if _identity(candidate) != identity:
        raise ValueError("Molecular identity changed during coordinate transfer")
    positioned = output / "positioned.sdf"
    writer = Chem.SDWriter(str(positioned))
    writer.SetProps([])
    writer.write(candidate)
    writer.close()
    reread = Chem.SDMolSupplier(str(positioned), removeHs=False, sanitize=True)
    if len(reread) != 1 or reread[0] is None or _identity(reread[0]) != identity:
        raise ValueError("SDF serialization changed atom order, bonds, stereochemistry or formal charges")
    rounding = float(np.linalg.norm(np.asarray(reread[0].GetConformer().GetPositions()) - selected, axis=1).max(initial=0))
    if rounding > 0.0001:
        raise ValueError("Positioned SDF coordinate serialization exceeded its 0.0001 Angstrom tolerance")
    if hashes != {str(p): _hash(p) for p in paths}:
        raise ValueError("A molecular handoff input changed during transfer")
    evidence = {"status": "molecular_handoff_pass", "source_sha256": hashes, "identity": identity,
                "mapping_mode": mapping_mode, "source_to_geometry_indices": indices,
                "coordinate_format": coordinate_format, "rigid_rotation_row_vector": rotation.tolist(),
                "rotation_determinant": float(np.linalg.det(rotation)), "rigid_singular_values": singular_values.tolist(),
                "maximum_rigid_fit_residual_angstrom": maximum, "rigid_tolerance_angstrom": RIGID_TOLERANCE_ANGSTROM,
                "sdf_roundtrip_max_coordinate_residual_angstrom": rounding,
                "bond_inference": "none", "hydrogen_addition": "none", "charge_assignment": "none",
                "sdf_non_graph_properties": "omitted from positioned output", "scientific_acceptance": "pending"}
    _write(output / "mapping-evidence.json", evidence)
    return mol.GetNumAtoms()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--graph-sdf", type=Path, required=True)
    parser.add_argument("--geometry", type=Path, required=True)
    parser.add_argument("--atom-map", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    output = args.output.resolve()
    if output.exists():
        print("Handoff output must be a new directory", file=sys.stderr)
        return 2
    output.mkdir(parents=True)
    manifest = {"schema_version": 1, "status": "failed", "scientific_acceptance": "pending"}
    try:
        count = handoff(args.graph_sdf.resolve(), args.geometry.resolve(), args.atom_map.resolve() if args.atom_map else None, output)
        manifest.update(status="molecular_handoff_pass", atom_count=count)
        code = 0
    except Exception as error:
        manifest.update(error_type=type(error).__name__, error=str(error))
        print(type(error).__name__ + ": " + str(error), file=sys.stderr)
        code = 2
    manifest["outputs_sha256"] = {str(p.relative_to(output)): _hash(p) for p in sorted(output.rglob("*")) if p.is_file()}
    _write(output / "manifest.json", manifest)
    print(json.dumps({"status": manifest["status"], "scientific_acceptance": "pending"}))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
