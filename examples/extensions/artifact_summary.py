"""Trusted optional extension: summarize declared native-manifest metadata only.

Register this file explicitly after local code review. Importing it has no side
effects. It performs no chemistry inference, rendering, validation or subprocesses.
"""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path


MAX_MANIFEST_BYTES = 1_048_576
REPORTED_FIELDS = (
    "status", "mode", "scientific_state", "scientific_acceptance", "visual_review",
    "candidate_count", "geometry_screen_pass_count", "atom_count",
)


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"Duplicate native-manifest key: {key}")
        result[key] = value
    return result


def _finite_float(value):
    number = float(value)
    if not math.isfinite(number):
        raise ValueError("Native manifest contains a nonfinite number")
    return number


def _invalid_constant(value):
    raise ValueError(f"Native manifest contains a nonfinite constant: {value}")


def execute(capability, operation, params, output_dir, skills_root, run_process):
    """Return an ordinary Workbench handoff; unused runtimes remain untouched."""
    if operation != "summarize" or not isinstance(params, dict) or set(params) != {"manifest"}:
        raise ValueError("summarize requires exactly one local manifest parameter")
    value = params["manifest"]
    if not isinstance(value, str) or not value or "://" in value:
        raise ValueError("manifest must name a local file")
    source = Path(value).expanduser().resolve()
    if not source.is_file():
        raise ValueError("Native manifest file is unavailable")
    if source.stat().st_size > MAX_MANIFEST_BYTES:
        raise ValueError("Native manifest exceeds the 1 MiB example-adapter budget")
    raw = source.read_bytes()
    if len(raw) > MAX_MANIFEST_BYTES:
        raise ValueError("Native manifest grew beyond the example-adapter budget")
    native = json.loads(raw, object_pairs_hook=_object, parse_float=_finite_float,
                        parse_constant=_invalid_constant)
    if not isinstance(native, dict):
        raise ValueError("Native manifest must be a JSON object")

    # Copy only scalar technical labels, not source paths, parameters or arbitrary
    # private metadata. They remain declarations, not independently verified facts.
    reported = {key: native[key] for key in REPORTED_FIELDS
                if key in native and isinstance(native[key], (str, int, float, bool, type(None)))}
    artifact_index = native.get("outputs_sha256", native.get("output_sha256", {}))
    summary = {
        "schema_version": 1,
        "summary_kind": "native_manifest_metadata",
        "source_sha256": hashlib.sha256(raw).hexdigest(),
        "reported_metadata": reported,
        "declared_artifact_count": len(artifact_index) if isinstance(artifact_index, dict) else None,
        "scientific_acceptance": "not_established",
        "scope": "Scalar metadata declarations only; artifacts and scientific claims were not validated.",
    }
    output = Path(output_dir).resolve()
    output.mkdir(parents=True, exist_ok=True)
    destination = output / "summary.json"
    with destination.open("x") as stream:
        json.dump(summary, stream, indent=2, allow_nan=False)
        stream.write("\n")
    if source.read_bytes() != raw:
        raise ValueError("Native manifest changed during summarization")
    return {
        "status": "complete",
        "reason": "Native manifest technical metadata summarized; no scientific validation performed.",
        "artifacts": {"summary": str(destination)},
        "evidence": {"metadata_summary": "complete", "scientific_acceptance": "pending",
                     "visual_review": "not_performed"},
        "details": {"source_sha256": summary["source_sha256"],
                    "reported_fields": sorted(reported), "artifact_hashes_verified": False},
    }
