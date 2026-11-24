import unittest
from unittest.mock import Mock, patch

from orchestrator.config import RagConfig
from orchestrator.index_docs import markdown_chunks
from orchestrator.rag import CHUNK_CHARS, EMBEDDING_MODEL, SolrRetriever


class RagTests(unittest.TestCase):
    def test_markdown_locations_and_unique_ids_for_repeated_fragments(self):
        long_line = "x" * (CHUNK_CHARS * 2)
        markdown = f"# Document\n\n## Section\n```sh\n# comment\n```\n{long_line}"
        chunks = list(markdown_chunks("docs/example.md", markdown))
        self.assertEqual([chunk["heading"] for chunk in chunks], ["Document", "Section", "Section", "Section"])
        self.assertEqual([chunk["line"] for chunk in chunks], [1, 3, 7, 7])
        self.assertEqual(len({chunk["id"] for chunk in chunks}), len(chunks))
        self.assertEqual(chunks, list(markdown_chunks("docs/example.md", markdown)))

    def test_hybrid_combines_rankings_and_queries_one_snapshot(self):
        snapshot = "a" * 64
        manifest = {"snapshot": snapshot, "embedding_model": EMBEDDING_MODEL}

        def hit(id):
            return {"id": id, "path": "README.md", "line": 3, "heading": "Setup", "text": f"Passage {id}"}

        retriever = SolrRetriever(RagConfig(method="hybrid"))
        encode = Mock(return_value=[1.0])
        with patch.object(retriever, "_select", side_effect=[
            [manifest], [hit("keyword"), hit("both")], [hit("vector"), hit("both")], [manifest],
        ]) as select:
            actual, sources = retriever.retrieve("Where is setup?", 3, encode)
        self.assertEqual(actual, snapshot)
        self.assertEqual([source.id for source in sources], ["both", "keyword", "vector"])
        encode.assert_called_once_with("Where is setup?")
        for call in select.call_args_list[1:3]:
            self.assertIn(f"snapshot:{snapshot}", call.kwargs["filters"])

    def test_index_change_during_retrieval_requires_a_new_preparation(self):
        retriever = SolrRetriever(RagConfig())
        with patch.object(retriever, "_select", side_effect=[
            [{"snapshot": "a" * 64}], [], [{"snapshot": "b" * 64}],
        ]):
            with self.assertRaisesRegex(ValueError, "changed during retrieval"):
                retriever.retrieve("setup", 1, None)
