import sqlite3
import unittest

from rule_engine.source_policy import append_strict_source_policy


class StrictSourcePolicyTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(":memory:")
        self.db.executescript("""
            CREATE TABLE documents (
                doc_id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                edition TEXT NOT NULL,
                canonical_candidate INTEGER NOT NULL
            );
            CREATE TABLE chunks (
                rowid INTEGER PRIMARY KEY,
                doc_id TEXT NOT NULL,
                chunk_id TEXT NOT NULL,
                title TEXT NOT NULL,
                section TEXT,
                page INTEGER,
                line_start INTEGER,
                line_end INTEGER,
                text TEXT NOT NULL
            );
            CREATE VIRTUAL TABLE chunks_fts USING fts5(text);
        """)
        documents = [
            ("dmg-2024", "Dungeon Master's Guide 2024", "2024", 1),
            ("dmg-alt", "Dungeon Master's Guide 2024", "2024", 0),
            ("phb-2024", "Player's Handbook 2024", "2024", 1),
            ("mm-2025", "Monster Manual 2025", "2025", 0),
            ("ravenloft", "Van Richten's Guide to Ravenloft", "not explicit", 0),
            ("eberron", "Eberron: Forge of the Artificer", "not explicit", 0),
            ("dragon-delves", "Dragon Delves", "not explicit", 0),
            ("phb-2014", "Player's Handbook 2014", "2014", 1),
        ]
        for index, doc in enumerate(documents, start=1):
            doc_id, title, _edition, _canonical = doc
            self.db.execute("INSERT INTO documents VALUES (?, ?, ?, ?)", doc)
            self.db.execute(
                "INSERT INTO chunks VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (index, doc_id, f"chunk-{index}", title, "Test", None, 1, 1, "concentration test"),
            )
            self.db.execute("INSERT INTO chunks_fts(rowid, text) VALUES (?, ?)", (index, "concentration test"))

    def tearDown(self):
        self.db.close()

    def search_ids(self, requested_edition=None):
        sql = """
            SELECT d.doc_id
            FROM chunks_fts
            JOIN chunks AS c ON c.rowid = chunks_fts.rowid
            JOIN documents AS d ON d.doc_id = c.doc_id
            WHERE chunks_fts MATCH ?
        """
        sql, args = append_strict_source_policy(sql, ["concentration"], requested_edition)
        return {row[0] for row in self.db.execute(sql, args)}

    def test_default_search_is_strict_and_uses_canonical_duplicate(self):
        self.assertEqual(self.search_ids(), {"dmg-2024", "phb-2024"})

    def test_requested_edition_can_narrow_but_not_expand_scope(self):
        self.assertEqual(self.search_ids("2024"), {"dmg-2024", "phb-2024"})
        self.assertEqual(self.search_ids("2025"), set())
        self.assertEqual(self.search_ids("2014"), set())
        self.assertEqual(self.search_ids("not explicit"), set())


if __name__ == "__main__":
    unittest.main()
