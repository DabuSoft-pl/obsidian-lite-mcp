import json
import os
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(__file__))
import obsidian_lite_mcp as server


class VaultTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        files = {
            "A.md": "---\ntags:\n  - adr\nstatus: \"przyjęta\"\nprojekt: \"[[B]]\"\n---\nTreść A, link [[Sub/C|alias]] i [[Brak]] #inline\n",
            "Sub/C.md": "Notatka C linkuje [[A#Sekcja]]\n",
            "B.md": "kolor `#f2a541` w kodzie to nie tag\n```\n#nie\n```\n",
            ".obsidian/x.md": "ukryte [[A]]\n",
            "Proj/P.md": "---\nrepo:\n  - my-repo\n  - other\n---\n# P\nOpis projektu\n## Etap\nkrok [[B]]\n```\n# komentarz bash\n```\ndalej\n## Koniec\nx\n",
            "Proj/Dec/ADR-1.md": "---\ntags: [adr]\nstatus: przyjęta\n---\nDecyzja\n",
            "Proj/Plan.md": "- [ ] zrobić X\n- [x] zrobione\n",
        }
        for rel, text in files.items():
            p = os.path.join(self.dir.name, rel)
            os.makedirs(os.path.dirname(p), exist_ok=True)
            with open(p, "w", encoding="utf-8") as f:
                f.write(text)
        for i, rel in enumerate(["Proj/Plan.md", "Proj/Dec/ADR-1.md", "Proj/P.md"]):
            os.utime(os.path.join(self.dir.name, rel), (0, 86400 * (i + 1)))
        self.notes = server.load(self.dir.name)

    def tearDown(self):
        self.dir.cleanup()

    def test_index_skips_dot_dirs_and_parses_frontmatter(self):
        self.assertEqual(sorted(self.notes), ["A.md", "B.md", "Proj/Dec/ADR-1.md", "Proj/P.md", "Proj/Plan.md", "Sub/C.md"])
        self.assertEqual(self.notes["A.md"]["props"]["status"], "przyjęta")
        self.assertEqual(self.notes["A.md"]["tags"], {"adr", "inline"})
        self.assertEqual(self.notes["B.md"]["tags"], set())

    def test_resolve(self):
        self.assertEqual(server.resolve("C", self.notes), "Sub/C.md")
        self.assertEqual(server.resolve("sub/c.md", self.notes), "Sub/C.md")
        self.assertIsNone(server.resolve("Brak", self.notes))

    def test_search_filters(self):
        self.assertEqual(server.search(self.notes, tag="adr"), "A.md (~54 tok)\nProj/Dec/ADR-1.md (~23 tok)")
        self.assertEqual(server.search(self.notes, property="status=przyj"), "A.md (~54 tok)\nProj/Dec/ADR-1.md (~23 tok)")
        self.assertEqual(server.search(self.notes, property="projekt=B"), "A.md (~54 tok)")
        self.assertIn("Sub/C.md", server.search(self.notes, query="linkuje"))
        self.assertEqual(server.search(self.notes, query="zzz"), "No results.")

    def test_read_links_and_backlinks(self):
        out = server.read(self.notes, "A")
        self.assertIn("Links: B.md, Sub/C.md, Brak (missing)", out)
        self.assertIn("Backlinks: Sub/C.md", out)
        self.assertRaises(ValueError, server.read, self.notes, "nic")

    def test_read_section(self):
        out = server.read(self.notes, "P#etap")
        self.assertIn("# komentarz bash", out)
        self.assertIn("dalej", out)
        self.assertNotIn("Koniec", out)
        self.assertIn("Links: B.md", out)
        self.assertRaises(ValueError, server.read, self.notes, "P#nie ma")

    def test_outline(self):
        self.assertEqual(server.read(self.notes, "P", outline_only=True),
                         "# Proj/P.md (~59 tok)\nP (~9 tok)\n  Etap (~25 tok)\n  Koniec (~6 tok)")

    def test_context(self):
        out = server.context(self.notes, "/home/u/code/other/src")
        self.assertEqual(out, "Obsidian vault: repo `other` → [[P]]. Opis projektu\n"
                              "ADR przyjęta: ADR-1\nOpen tasks: [[Plan]] 1\n"
                              "Recently changed: [[P]] 1970-01-04, [[ADR-1]] 1970-01-03, [[Plan]] 1970-01-02")
        self.assertEqual(server.context(self.notes, "/home/u/unknown"), "")

    def test_search_ranks_by_hits(self):
        self.assertTrue(server.search(self.notes, query="link").startswith("Sub/C.md"))  # 1 hit each: C has newer mtime
        out = server.search(self.notes, query="a").splitlines()
        self.assertEqual(out[0].split(" ")[0], "A.md")

    def test_remember(self):
        os.mkdir(os.path.join(self.dir.name, "Inbox"))
        self.assertEqual(server.remember(self.dir.name, "Inbox", "Lekcja", "Treść [[A]]", ["#x"]), "Saved: Inbox/Lekcja.md")
        with open(os.path.join(self.dir.name, "Inbox/Lekcja.md"), encoding="utf-8") as f:
            text = f.read()
        self.assertTrue(text.startswith("---\ntags:\n  - claude\n  - x\ncreated: "))
        self.assertTrue(text.endswith("---\nTreść [[A]]\n"))
        self.assertRaises(ValueError, server.remember, self.dir.name, "Inbox", "Lekcja", "drugi raz")  # no overwrite
        for bad in ["../A", "a/b", ".ukryta", "", "x" * 121, "[[x]]"]:
            self.assertRaises(ValueError, server.remember, self.dir.name, "Inbox", bad, "t")
        self.assertFalse(os.path.exists(os.path.join(self.dir.name, "A.md.md")))

    def test_remember_only_when_enabled(self):
        req = {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
        self.assertNotIn("remember", [t["name"] for t in server.handle(req, self.dir.name)["tools"]])
        self.assertIn("remember", [t["name"] for t in server.handle(req, self.dir.name, "Inbox")["tools"]])
        call = {"method": "tools/call", "params": {"name": "remember", "arguments": {"title": "x", "text": "y"}}}
        self.assertTrue(server.handle(call, self.dir.name)["isError"])
        self.assertFalse(os.path.exists(os.path.join(self.dir.name, "x.md")))

    def test_stdio_roundtrip(self):
        reqs = [
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18"}},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "read", "arguments": {"name": "C"}}},
            {"jsonrpc": "2.0", "id": 3, "method": "nope"},
        ]
        p = subprocess.run([sys.executable, server.__file__, self.dir.name], capture_output=True, text=True,
                           input="".join(json.dumps(r) + "\n" for r in reqs), timeout=10)
        replies = [json.loads(l) for l in p.stdout.splitlines()]
        self.assertEqual([r["id"] for r in replies], [1, 2, 3])
        self.assertIn("Backlinks: A.md", replies[1]["result"]["content"][0]["text"])
        self.assertEqual(replies[2]["error"]["code"], -32601)


if __name__ == "__main__":
    unittest.main()
