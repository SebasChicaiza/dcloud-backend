"""Tests for manifest.build_manifest — chunk plan correctness + warnings."""
from __future__ import annotations

from dna_node.manifest import build_manifest


def test_exact_multiple():
    m = build_manifest("r", size_a=1024, size_b=1024, chunk_size=256)
    assert m.comparable_size == 1024
    assert len(m.chunks) == 4
    assert [c.start for c in m.chunks] == [0, 256, 512, 768]
    assert [c.end for c in m.chunks] == [256, 512, 768, 1024]
    assert [c.chunk_index for c in m.chunks] == [0, 1, 2, 3]
    assert m.chunks[0].chunk_id == "chunk_000000"
    assert m.warnings == []


def test_trailing_partial_chunk():
    m = build_manifest("r", size_a=1000, size_b=1000, chunk_size=300)
    assert len(m.chunks) == 4
    assert m.chunks[-1].start == 900
    assert m.chunks[-1].end == 1000
    assert m.chunks[-1].size == 100


def test_unequal_sizes_emit_warning_and_truncate():
    m = build_manifest("r", size_a=1024, size_b=900, chunk_size=256)
    assert m.comparable_size == 900
    assert m.chunks[-1].end == 900
    assert len(m.warnings) == 1
    assert "differ" in m.warnings[0].lower()


def test_deterministic():
    m1 = build_manifest("r", 1024, 1024, 256)
    m2 = build_manifest("r", 1024, 1024, 256)
    assert m1.to_json() == m2.to_json()
