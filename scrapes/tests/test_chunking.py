"""Tests for the Markdown-aware chunker."""

from __future__ import annotations

from scrapes.chunking import chunk_markdown


def test_empty_input_returns_empty():
    assert chunk_markdown("") == []
    assert chunk_markdown("   \n\n  ") == []


def test_single_short_paragraph_is_one_chunk():
    md = "This is a single short paragraph of text."
    chunks = chunk_markdown(md, budget=500, overlap=0)
    assert len(chunks) == 1
    assert "single short paragraph" in chunks[0].text
    assert chunks[0].heading_path == []


def test_paragraph_exceeds_budget_falls_back_to_sentences():
    # Force a tiny budget so even short paragraphs overflow and split on sentences.
    md = (
        "First sentence here. Second sentence continues. Third sentence follows. "
        "Fourth sentence wraps. Fifth sentence ends it all."
    )
    chunks = chunk_markdown(md, budget=12, overlap=0)
    assert len(chunks) >= 2
    # Every piece should be within budget.
    for c in chunks:
        assert c.token_count <= 12 + 4  # small slack for decoding round-trip


def test_multiple_headings_produce_correct_heading_path():
    md = "\n".join(
        [
            "# Top",
            "",
            "Intro paragraph.",
            "",
            "## Sub A",
            "",
            "Sub A body.",
            "",
            "### Sub A1",
            "",
            "Deep body.",
            "",
            "## Sub B",
            "",
            "Sub B body.",
        ]
    )
    # Small budget so each heading+body pair becomes its own chunk.
    chunks = chunk_markdown(md, budget=20, overlap=0)
    paths = [c.heading_path for c in chunks]

    assert ["Top"] in paths
    assert ["Top", "Sub A"] in paths
    assert ["Top", "Sub A", "Sub A1"] in paths
    assert ["Top", "Sub B"] in paths


def test_oversize_fenced_code_falls_back_to_sentence_split():
    # Build a fence whose body blows through the budget.
    code_body = ". ".join(f"statement_{i}" for i in range(60)) + "."
    md = f"```\n{code_body}\n```"
    chunks = chunk_markdown(md, budget=40, overlap=0)
    # Must have split: more than one chunk, each within budget tolerance.
    assert len(chunks) > 1
    for c in chunks:
        assert c.token_count <= 40 + 8  # overlap-decoding slack


def test_indonesian_unicode_roundtrips_cleanly():
    md = (
        "# Haji Furoda\n\n"
        "Haji Furoda adalah haji khusus menggunakan visa undangan dari Kerajaan "
        "Arab Saudi, tanpa antrean dan berangkat di tahun yang sama."
    )
    chunks = chunk_markdown(md, budget=500, overlap=0)
    assert len(chunks) == 1
    assert "Haji Furoda" in chunks[0].text
    assert chunks[0].heading_path == ["Haji Furoda"]
    assert "Arab Saudi" in chunks[0].text
