import json
import os
import subprocess
import sys
import tempfile
import time
import unicodedata
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
            "Zw/Żółw.md": "pancerz\n",
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
        self.assertEqual(sorted(self.notes), ["A.md", "B.md", "Proj/Dec/ADR-1.md", "Proj/P.md", "Proj/Plan.md", "Sub/C.md", "Zw/Żółw.md"])
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
        os.utime(os.path.join(self.dir.name, "A.md"), (0, 10**9))  # explicit: Windows mtime can tie with C's
        self.notes = server.load(self.dir.name)
        self.assertTrue(server.search(self.notes, query="link").startswith("Sub/C.md"))  # 1 hit each: C has newer mtime
        out = server.search(self.notes, query="a").splitlines()
        self.assertEqual(out[0].split(" ")[0], "A.md")

    def test_broken_links_and_orphans(self):
        self.assertEqual(server.search(self.notes, links="broken"), "A.md (~54 tok)\n    broken: [[Brak]]")
        orphans = {l.split(" ")[0] for l in server.search(self.notes, links="orphans").splitlines()}
        self.assertEqual(orphans, {"Proj/P.md", "Proj/Dec/ADR-1.md", "Proj/Plan.md", "Zw/Żółw.md"})
        self.assertEqual(server.search(self.notes, tag="adr", links="orphans"), "Proj/Dec/ADR-1.md (~23 tok)")
        self.assertRaises(ValueError, server.search, self.notes, links="dead")
        with open(os.path.join(self.dir.name, "B.md"), "a", encoding="utf-8") as f:
            f.write("![[pic.png]] [[Nowa]]\n")
        os.utime(os.path.join(self.dir.name, "B.md"), (0, 10**9))
        out = server.search(server.load(self.dir.name), links="broken")
        self.assertTrue(out.endswith("B.md (~36 tok)\n    broken: [[Nowa]]"))  # attachments are not reported

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
        reqs_text = "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in reqs)
        reqs_text += "{not json\n" + '[1, 2]\n' + json.dumps({"jsonrpc": "2.0", "id": 4, "method": "tools/call",
                                                            "params": {"name": "read", "arguments": {"name": 5}}}) + "\n"
        reqs_text += json.dumps({"jsonrpc": "2.0", "id": 5, "method": "tools/call",
                                 "params": {"name": "read", "arguments": {"name": "Żółw"}}}, ensure_ascii=False) + "\n"
        p = subprocess.run([sys.executable, server.__file__, self.dir.name], capture_output=True, text=True,
                           encoding="utf-8", input=reqs_text, timeout=10)
        replies = [json.loads(l) for l in p.stdout.splitlines()]
        self.assertEqual([r.get("id") for r in replies], [1, 2, 3, None, None, 4, 5])  # server survived bad lines
        self.assertEqual(replies[0]["result"]["serverInfo"]["version"], server.__version__)
        self.assertIn("Backlinks: A.md", replies[1]["result"]["content"][0]["text"])
        self.assertEqual(replies[2]["error"]["code"], -32601)
        self.assertEqual([replies[3]["error"]["code"], replies[4]["error"]["code"]], [-32700, -32600])
        self.assertTrue(replies[5]["result"]["isError"])
        self.assertIn("pancerz", replies[6]["result"]["content"][0]["text"])  # UTF-8 both ways


class FeaturesTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.v = self.dir.name

    def tearDown(self):
        self.dir.cleanup()
        server._CACHE.pop(os.path.abspath(self.v), None)

    def write(self, rel, text):
        p = os.path.join(self.v, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            f.write(text)
        return p

    def test_nfd_file_names_match_nfc_links(self):
        self.write(unicodedata.normalize("NFD", "Żółw.md"), "pancerz")
        self.write("A.md", "[[Żółw]]")
        notes = server.load(self.v)
        self.assertEqual(server.resolve("Żółw", notes), "Żółw.md")
        self.assertEqual(notes.backlinks("Żółw.md"), ["A.md"])

    def test_aliases(self):
        self.write("Long name.md", "---\naliases: [Short, Inny]\n---\nx")
        self.write("B.md", "see [[short]]")
        notes = server.load(self.v)
        self.assertEqual(server.resolve("Short", notes), "Long name.md")
        self.assertEqual(notes.backlinks("Long name.md"), ["B.md"])

    def test_markdown_links(self):
        self.write("Dir/X.md", "[up](../B.md) [enc](<Sub Dir/Y.md>) [pct](Sub%20Dir/Y.md#h) [web](https://e.com/a.md)")
        self.write("B.md", "b")
        self.write("Dir/Sub Dir/Y.md", "y")
        notes = server.load(self.v)
        out = server.read(notes, "X")
        self.assertIn("Links: B.md, Dir/Sub Dir/Y.md\n", out)
        self.assertEqual(notes.backlinks("Dir/Sub Dir/Y.md"), ["Dir/X.md"])

    def test_links_ignore_code_and_handle_table_pipes(self):
        self.write("B.md", "b")
        self.write("A.md", "| x | [[B\\|bee]] |\n`[[Fake]]`\n```\n[[Fake2]]\n```\n[m](mailto:x@y.md)")
        self.assertEqual(server.load(self.v)["A.md"]["links"], ["B"])

    def test_obsidian_excluded_files(self):
        self.write(".obsidian/app.json", json.dumps({"userIgnoreFilters": ["Archive/", "/^Tmp-\\d+/", "/[/"]}))
        for rel in ["Archive/Old.md", "Tmp-1.md", "Tmp-x.md", "Keep.md"]:
            self.write(rel, "x")
        self.assertEqual(sorted(server.load(self.v)), ["Keep.md", "Tmp-x.md"])

    def test_big_note_returns_outline_unless_full(self):
        body = "# Big\n" + "## Part\n" + "słowo " * (server.MAX_READ_TOKENS // 2 + 10)
        self.write("Big.md", body)
        notes = server.load(self.v)
        out = server.read(notes, "Big")
        self.assertIn("Part (~", out)
        self.assertIn("over the 8000 limit", out)
        self.assertLess(len(out), 500)
        self.assertIn("słowo słowo", server.read(notes, "Big", full=True))

    def test_cache_rereads_only_changed_files(self):
        p = self.write("A.md", "one")
        self.write("B.md", "two")
        first = server.load(self.v)
        second = server.load(self.v)
        self.assertIs(first["B.md"], second["B.md"])
        with open(p, "w", encoding="utf-8") as f:
            f.write("changed, longer")
        os.utime(p, (0, 10))
        third = server.load(self.v)
        self.assertEqual(third["A.md"]["text"], "changed, longer")
        self.assertIs(third["B.md"], first["B.md"])
        os.remove(p)
        self.assertEqual(sorted(server.load(self.v)), ["B.md"])

    def test_backlinks_scale(self):
        n = 3000
        for i in range(n):
            self.write(f"d{i % 30}/n{i}.md", " ".join(f"[[n{(i * 7 + k) % n}]]" for k in range(10)))
        start = time.time()
        notes = server.load(self.v)
        out = server.read(notes, "n42")
        self.assertIn("Backlinks: ", out)
        self.assertLess(time.time() - start, 10)  # was ~40 s at this size before the name index

    def test_versions_match(self):
        with open(os.path.join(os.path.dirname(server.__file__), "server.json"), encoding="utf-8") as f:
            meta = json.load(f)
        self.assertEqual({meta["version"], meta["packages"][0]["version"]}, {server.__version__})


if __name__ == "__main__":
    unittest.main()
