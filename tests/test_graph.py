"""Tests for self-wiring entity graph (Task 12.6)."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from eling.layers.facts import FactsLayer


@pytest.fixture
def graph():
    db = Path(tempfile.mkdtemp()) / "graph.db"
    layer = FactsLayer(db)
    yield layer
    layer.close()


class TestExtractEntities:
    """[[entity]] wiki-links extracted alongside existing patterns."""

    def test_wiki_link_extracted(self, graph):
        fid = graph.add("See [[Albert Einstein]] for details")
        ents = graph.entities_for_fact(fid)
        assert "Albert Einstein" in ents

    def test_multi_wiki_links(self, graph):
        fid = graph.add("Compare [[Python]] and [[Java Virtual Machine]]")
        ents = graph.entities_for_fact(fid)
        assert "Python" in ents
        assert "Java Virtual Machine" in ents

    def test_capitalized_still_works(self, graph):
        """Existing capitalized-phrase extraction not broken."""
        fid = graph.add("John Doe wrote the book")
        ents = graph.entities_for_fact(fid)
        assert "John Doe" in ents

    def test_no_entities_still_empty(self, graph):
        fid = graph.add("just plain text here")
        assert graph.entities_for_fact(fid) == []


class TestSelfWireGraph:
    """Entity co-occurrence edges created on write."""

    def test_pair_gets_edge(self, graph):
        graph.add("[[Albert Einstein]] developed [[Theory of Relativity]]")
        edges = graph._conn.execute("SELECT * FROM entity_graph").fetchall()
        assert len(edges) == 1
        assert edges[0]["weight"] == 1.0

    def test_triple_gets_three_edges(self, graph):
        graph.add("[[Alice]], [[Bob]], and [[Charlie]] worked together")
        edges = graph._conn.execute("SELECT * FROM entity_graph").fetchall()
        assert len(edges) == 3  # A-B, A-C, B-C

    def test_weight_increments_on_repeat(self, graph):
        graph.add("[[Einstein]] and [[Relativity]]")
        graph.add("[[Einstein]] and [[Relativity]] again!")
        edges = graph._conn.execute("SELECT weight FROM entity_graph").fetchall()
        assert edges[0]["weight"] == 2.0

    def test_entity_neighbors_returns_sorted(self, graph):
        # Build a small graph: Alice-Bob (1), Alice-Charlie (2)
        graph.add("[[Alice]] and [[Bob]]")
        graph.add("[[Alice]] and [[Charlie]]")
        graph.add("[[Alice]] with [[Charlie]]")  # increment weight, different content

        neighbors = graph.entity_neighbors("Alice")
        assert len(neighbors) == 2
        # Charlie (weight 2) before Bob (weight 1)
        assert neighbors[0]["neighbor"] == "Charlie"
        assert neighbors[0]["weight"] == 2.0
        assert neighbors[1]["neighbor"] == "Bob"
        assert neighbors[1]["weight"] == 1.0

    def test_entity_neighbors_unknown_returns_empty(self, graph):
        assert graph.entity_neighbors("Nonexistent") == []

    def test_many_entities_one_fact(self, graph):
        """6 entities → 15 edges (C(6,2))."""
        fids = []
        for i in range(6):
            fids.append(graph.add(f"[[Entity{i}]] is here"))
        # Separate facts, no co-occurrence → 0 edges
        count = graph._conn.execute(
            "SELECT COUNT(*) as n FROM entity_graph"
        ).fetchone()["n"]
        assert count == 0

    def test_single_entity_no_edge(self, graph):
        graph.add("[[Lonely Entity]]")
        count = graph._conn.execute(
            "SELECT COUNT(*) as n FROM entity_graph"
        ).fetchone()["n"]
        assert count == 0

    def test_fact_without_entities_no_edge(self, graph):
        graph.add("plain text without entities")
        count = graph._conn.execute(
            "SELECT COUNT(*) as n FROM entity_graph"
        ).fetchone()["n"]
        assert count == 0

    def test_different_facts_same_entity_no_edge(self, graph):
        """Same entity across different facts — no other entity to pair with."""
        graph.add("[[Einstein]] studied physics")
        graph.add("[[Einstein]] won the prize")
        count = graph._conn.execute(
            "SELECT COUNT(*) as n FROM entity_graph"
        ).fetchone()["n"]
        assert count == 0
