#!/usr/bin/env python3
"""Read-first MCP server (stdio) for an Obsidian vault. No Obsidian app, no dependencies.

Usage: obsidian-lite-mcp /path/to/vault [--write-dir Inbox]  MCP server on stdio; --write-dir enables `remember`
       obsidian-lite-mcp context /path/to/vault CWD           project brief for the repo in CWD (notes with `repo:` property)
"""
import json
import os
import posixpath
import re
import sys
import time
import unicodedata
from urllib.parse import unquote

__version__ = "1.0.0"

WIKI_RE = re.compile(r"!?\[\[([^\]|#]*)[^\]]*\]\]")
MD_LINK_RE = re.compile(r"\]\((?:<([^>]+?\.md)>|([^)\s]+?\.md))(?:#[^)]*)?\)")
TAG_RE = re.compile(r"(?<![\w/#&])#([^\W\d][\w/-]*)")
CODE_RE = re.compile(r"```.*?```|`[^`\n]*`", re.S)
TASK_RE = re.compile(r"^\s*- \[ \] ", re.M)
MAX_RESULTS = 20
MAX_READ_TOKENS = 8000  # bigger reads return the outline instead, unless full=true
DATE_KEY = os.environ.get("VAULT_DATE_KEY", "created")  # frontmatter key for the date on notes `remember` creates


def nfc(s):
    """macOS may hand back NFD file names; links are typed in NFC. Compare everything in NFC."""
    return unicodedata.normalize("NFC", s)


def parse_frontmatter(text):
    """Return (props, body). shortcut: handles scalars, [a, b] and '- item' lists only; nested YAML is skipped."""
    if not text.startswith("---\n"):
        return {}, text
    end = text.find("\n---", 4)
    if end == -1:
        return {}, text
    props, key = {}, None
    for line in text[4:end].splitlines():
        item = re.match(r"\s+-\s*(.*)", line)
        if item and key is not None:
            if not isinstance(props[key], list):
                props[key] = []
            props[key].append(item.group(1).strip().strip("\"'"))
            continue
        kv = re.match(r"([^\s:][^:]*):\s*(.*)", line)
        if not kv:
            continue
        key, val = kv.group(1).strip(), kv.group(2).strip()
        if val.startswith("[") and val.endswith("]") and not val.startswith("[["):
            props[key] = [v.strip().strip("\"'") for v in val[1:-1].split(",") if v.strip()]
        else:
            props[key] = val.strip("\"'")
    return props, text[end + 4:].lstrip("\n")


def as_list(value):
    return [value] if isinstance(value, str) else value or []


def links_in(text, rel):
    """Link targets in text: [[wikilinks]] by name, and [x](note.md) links as vault-relative paths."""
    out = [nfc(l.strip()) for l in WIKI_RE.findall(text) if l.strip()]
    for a, b in MD_LINK_RE.findall(text):
        target = nfc(unquote(a or b))
        if "://" not in target:
            out.append(posixpath.normpath(posixpath.join(posixpath.dirname(rel), target)))
    return out


def parse_note(text, rel, mtime):
    props, body = parse_frontmatter(text)
    tags = {t.lstrip("#").lower() for t in as_list(props.get("tags")) if t}
    tags |= {t.lower() for t in TAG_RE.findall(CODE_RE.sub("", body))}
    aliases = [nfc(a) for a in as_list(props.get("aliases") or props.get("alias")) if a]
    return {"text": text, "props": props, "tags": tags, "mtime": mtime, "aliases": aliases, "links": links_in(text, rel)}


class Notes(dict):
    """path -> note, plus lookup indexes so link resolution and backlinks are O(1) per link."""

    def __init__(self, entries):
        super().__init__(entries)
        self.by_path = {p[:-3].lower(): p for p in self}
        self.by_name = {}
        for p, n in self.items():
            for key in [posixpath.basename(p)[:-3], *n["aliases"]]:
                self.by_name.setdefault(key.lower(), []).append(p)
        self._backlinks = None

    def backlinks(self, path):
        if self._backlinks is None:
            self._backlinks = {}
            for p, n in self.items():
                for l in n["links"]:
                    target = resolve(l, self)
                    if target and target != p:
                        self._backlinks.setdefault(target, set()).add(p)
        return sorted(self._backlinks.get(path, ()))


def ignore_filters(vault):
    """Obsidian's Settings > Files and links > Excluded files: path prefixes, or /regex/."""
    try:
        with open(os.path.join(vault, ".obsidian", "app.json"), encoding="utf-8") as f:
            raw = json.load(f).get("userIgnoreFilters") or []
    except (OSError, ValueError, AttributeError):
        return []
    out = []
    for s in raw:
        if isinstance(s, str) and len(s) > 2 and s.startswith("/") and s.endswith("/"):
            try:
                out.append(re.compile(s[1:-1]).search)
            except re.error:
                pass
        elif isinstance(s, str) and s:
            out.append(lambda p, s=nfc(s): p.startswith(s))
    return out


_CACHE = {}  # vault -> {rel: (mtime, size, note)}; files are re-read only when they change


def load(vault):
    vault = os.path.abspath(vault)
    old, new = _CACHE.get(vault, {}), {}
    skip = ignore_filters(vault)
    for root, dirs, files in os.walk(vault):
        dirs[:] = sorted(d for d in dirs if not d.startswith("."))
        for f in sorted(files):
            if not f.endswith(".md"):
                continue
            full = os.path.join(root, f)
            rel = nfc(os.path.relpath(full, vault).replace(os.sep, "/"))
            if any(match(rel) for match in skip):
                continue
            try:
                st = os.stat(full)
                cached = old.get(rel)
                if cached and cached[:2] == (st.st_mtime, st.st_size):
                    new[rel] = cached
                    continue
                with open(full, encoding="utf-8", errors="replace") as fh:
                    new[rel] = (st.st_mtime, st.st_size, parse_note(fh.read(), rel, st.st_mtime))
            except OSError:
                continue  # deleted or unreadable mid-scan
    _CACHE[vault] = new
    return Notes({rel: entry[2] for rel, entry in new.items()})


def tok(text):
    """Rough token estimate shown to the agent so it can decide whether a fetch is worth it."""
    # shortcut: chars/2 measured on Polish notes (~1.8 chars/token, 2026-11-27); English runs ~4, so this overestimates there
    return len(text) // 2 + 1


def headings(text):
    """Yield (line_index, level, title) for Markdown headings outside code fences."""
    fence = False
    for i, line in enumerate(text.splitlines()):
        if line.lstrip().startswith("```"):
            fence = not fence
        m = None if fence else re.match(r"(#{1,6})\s+(.*)", line)
        if m:
            yield i, len(m.group(1)), m.group(2).strip()


def resolve(name, notes):
    """Obsidian-style lookup: exact path, else note name or alias; shortest path wins on ties."""
    t = nfc(name).strip()
    t = (t[:-3] if t.lower().endswith(".md") else t).lower()
    if t in notes.by_path:
        return notes.by_path[t]
    base = posixpath.basename(t)
    hits = [p for p in notes.by_name.get(base, ()) if base == t or p[:-3].lower().endswith("/" + t)]
    return min(hits, key=len) if hits else None


def search(notes, query=None, tag=None, property=None):
    rx = re.compile(query, re.I) if query else None
    if property:
        pkey, _, pval = property.partition("=")
        pkey, pval = pkey.strip().lower(), pval.strip().lower()
    hits = []
    for path, n in notes.items():
        if tag and tag.lstrip("#").lower() not in n["tags"]:
            continue
        if property:
            vals = [v for k, v in n["props"].items() if k.lower() == pkey]
            if not vals:
                continue
            if pval and not any(pval in v.lower() for v in as_list(vals[0])):
                continue
        lines = []
        if rx:
            lines = [l.strip()[:160] for l in n["text"].splitlines() if rx.search(l)]
            if not lines and not rx.search(path):
                continue
        hits.append((len(lines), n["mtime"], f"{path} (~{tok(n['text'])} tok)" + "".join(f"\n    {l}" for l in lines[:3])))
    hits.sort(key=lambda h: (-h[0], -h[1]))  # most matching lines first, then most recently changed
    out = [h[2] for h in hits[:MAX_RESULTS]]
    if len(hits) > MAX_RESULTS:
        out.append(f"... {len(hits) - MAX_RESULTS} more, narrow the query")
    return "\n".join(out) or "No results."


def remember(vault, write_dir, title, text, tags=None):
    """Create a new note in write_dir. Never edits or overwrites an existing note."""
    title = nfc(title or "").strip()
    if not title or len(title) > 120 or title.startswith(".") or re.search(r'[\\/:*?"<>|#^\[\]\n]', title):
        raise ValueError("Bad title: no / \\ : * ? \" < > | # ^ [ ], no leading dot, max 120 chars")
    vault = os.path.realpath(vault)
    target_dir = os.path.realpath(os.path.join(vault, write_dir))
    path = os.path.join(target_dir, title + ".md")
    if os.path.dirname(os.path.realpath(path)) != target_dir:
        raise ValueError("Path outside the write folder")
    tags = ["claude", *(t.lstrip("#") for t in as_list(tags) if t and t.lstrip("#") != "claude")]
    front = "---\ntags:\n" + "".join(f"  - {t}\n" for t in tags) + f"{DATE_KEY}: {time.strftime('%Y-%m-%d')}\n---\n"
    rel = os.path.relpath(path, vault).replace(os.sep, "/")
    try:
        with open(path, "x", encoding="utf-8") as f:
            f.write(front + str(text).strip() + "\n")
    except FileExistsError:
        raise ValueError(f"Note already exists: {rel}; pick another title")
    return f"Saved: {rel}"


def section(text, heading):
    """Lines from the matching heading up to the next heading of the same or higher level (code fences skipped)."""
    lines, start, level = text.splitlines(), None, 0
    for i, lvl, title in headings(text):
        if start is None and nfc(title).lower() == nfc(heading).strip().lower():
            start, level = i, lvl
        elif start is not None and lvl <= level:
            return "\n".join(lines[start:i])
    if start is None:
        raise ValueError(f"No such section: {heading}")
    return "\n".join(lines[start:])


def outline(text):
    """Headings with the size of each section's own text (up to the next heading), for picking one to read."""
    lines, hs = text.splitlines(), list(headings(text))
    out = [f"{'  ' * (lvl - 1)}{title} (~{tok(chr(10).join(lines[i:hs[k + 1][0] if k + 1 < len(hs) else None]))} tok)"
           for k, (i, lvl, title) in enumerate(hs)]
    return "\n".join(out) or "(no headings)"


def read(notes, name, outline_only=False, full=False):
    name, _, heading = name.partition("#")
    path = resolve(name, notes)
    if not path:
        raise ValueError(f"No such note: {name}")
    n = notes[path]
    text = section(n["text"], heading) if heading else n["text"]
    if outline_only or (not full and tok(text) > MAX_READ_TOKENS):
        note = "" if outline_only else (f"\n\n(~{tok(text)} tok is over the {MAX_READ_TOKENS} limit: "
                                        "read one section with 'Note#Heading', or pass full=true)")
        return f"# {path} (~{tok(n['text'])} tok)\n{outline(text)}{note}"
    outgoing = []
    for l in dict.fromkeys(links_in(text, path)):
        target = resolve(l, notes)
        outgoing.append(target or f"{l} (missing)")
    return (f"# {path}\n\n{text}\n\n---\nLinks: " + (", ".join(outgoing) or "none")
            + "\nBacklinks: " + (", ".join(notes.backlinks(path)) or "none"))


def context(notes, cwd):
    """Project brief for a repo: the note whose `repo` property names a folder in cwd, plus ADRs and open tasks beside it."""
    project = None
    for part in reversed(os.path.normpath(cwd).split(os.sep)):
        part, key = nfc(part), nfc(part).lower()
        project = next((p for p, n in notes.items() if key in (nfc(r).lower() for r in as_list(n["props"].get("repo")))), None)
        if project:
            repo = part
            break
    if not project:
        return ""
    # Index only, not content: the agent pulls details with `read` when needed (keeps every session start cheap).
    scope = posixpath.dirname(project)
    scope = scope + "/" if scope else ""
    in_scope = [p for p in notes if p.startswith(scope)]
    name = lambda p: posixpath.basename(p)[:-3]
    body = parse_frontmatter(notes[project]["text"])[1]
    about = next((l.strip() for l in body.splitlines() if l.strip() and not l.lstrip().startswith(("#", "|", "!", "```"))), "")
    adrs = {}
    for p in in_scope:
        if "adr" in notes[p]["tags"]:
            adrs.setdefault(notes[p]["props"].get("status", "?"), []).append(name(p))
    tasks = [(name(p), len(TASK_RE.findall(notes[p]["text"]))) for p in in_scope]
    out = [f"Obsidian vault: repo `{repo}` → [[{name(project)}]]. {about[:200]}"]
    out += [f"ADR {status}: " + "; ".join(sorted(ns)) for status, ns in sorted(adrs.items(), key=lambda kv: str(kv[0]))]
    if any(n for _, n in tasks):
        out.append("Open tasks: " + ", ".join(f"[[{t}]] {n}" for t, n in tasks if n))
    recent = sorted(in_scope, key=lambda p: notes[p]["mtime"], reverse=True)[:3]
    out.append("Recently changed: " + ", ".join(
        f"[[{name(p)}]] {time.strftime('%Y-%m-%d', time.localtime(notes[p]['mtime']))}" for p in recent))
    return "\n".join(out)


TOOLS = [
    {"name": "search",
     "description": "Search Obsidian vault notes; filters optional, ANDed.",
     "inputSchema": {"type": "object", "properties": {
         "query": {"type": "string", "description": "case-insensitive regex on text and path"},
         "tag": {"type": "string"},
         "property": {"type": "string", "description": "'key' or 'key=value substring'"}}},
     "annotations": {"readOnlyHint": True}},
    {"name": "read",
     "description": "Read a note by [[link]] name, alias or path; 'Note#Heading' reads one section. Includes links and "
                    "backlinks. For big notes call with outline=true first (headings + ~tokens), then read one section.",
     "inputSchema": {"type": "object", "properties": {
         "name": {"type": "string"}, "outline": {"type": "boolean"},
         "full": {"type": "boolean", "description": f"return text over {MAX_READ_TOKENS} tokens anyway"}},
         "required": ["name"]},
     "annotations": {"readOnlyHint": True}},
]
REMEMBER = {
    "name": "remember",
    "description": "Save a new note (decision, finding, lesson) to the vault's inbox folder for the user to review. "
                   "Cannot edit existing notes. Link related notes with [[name]] in text.",
    "inputSchema": {"type": "object", "properties": {
        "title": {"type": "string"}, "text": {"type": "string"},
        "tags": {"type": "array", "items": {"type": "string"}}}, "required": ["title", "text"]},
    "annotations": {"readOnlyHint": False, "destructiveHint": False}}


def call_tool(name, args, vault, write_dir):
    if name == "remember" and write_dir:
        return remember(vault, write_dir, args.get("title"), args.get("text", ""), args.get("tags"))
    if name == "search":
        return search(load(vault), args.get("query"), args.get("tag"), args.get("property"))
    if name == "read":
        return read(load(vault), str(args.get("name", "")), bool(args.get("outline")), bool(args.get("full")))
    raise ValueError(f"Unknown tool: {name}")


def handle(msg, vault, write_dir=None):
    method, params = msg.get("method"), msg.get("params") or {}
    if method == "initialize":
        return {"protocolVersion": params.get("protocolVersion", "2025-06-18"), "capabilities": {"tools": {}},
                "serverInfo": {"name": "obsidian-lite-mcp", "version": __version__}}
    if method == "ping":
        return {}
    if method == "tools/list":
        return {"tools": TOOLS + ([REMEMBER] if write_dir else [])}
    if method == "tools/call":
        args = params.get("arguments")
        try:
            text = call_tool(params.get("name"), args if isinstance(args, dict) else {}, vault, write_dir)
            return {"content": [{"type": "text", "text": text}]}
        except Exception as e:  # a bad call must not kill the server; the agent sees the reason and can retry
            return {"content": [{"type": "text", "text": f"{type(e).__name__}: {e}"}], "isError": True}
    raise LookupError(method)


def error(id_, code, message):
    return {"jsonrpc": "2.0", "id": id_, "error": {"code": code, "message": message}}


def respond(line, vault, write_dir):
    """One JSON-RPC line in, one reply (or None for notifications) out."""
    try:
        msg = json.loads(line)
    except ValueError:
        return error(None, -32700, "Parse error")
    if not isinstance(msg, dict) or not isinstance(msg.get("method"), str):
        return error(msg.get("id") if isinstance(msg, dict) else None, -32600, "Invalid request")
    if "id" not in msg:
        return None  # notification
    try:
        return {"jsonrpc": "2.0", "id": msg["id"], "result": handle(msg, vault, write_dir)}
    except LookupError as e:
        return error(msg["id"], -32601, f"Method not found: {e}")
    except Exception as e:
        return error(msg["id"], -32603, f"Internal error: {e}")


def main():
    for stream in (sys.stdin, sys.stdout):
        stream.reconfigure(encoding="utf-8", newline="\n")  # Windows consoles default to a legacy code page
    if sys.argv[1:2] == ["context"]:  # context VAULT CWD -> brief for a SessionStart hook
        if len(sys.argv) == 4 and os.path.isdir(sys.argv[2]) and os.environ.get("VAULT_CONTEXT") != "off":
            print(context(load(sys.argv[2]), sys.argv[3]))
        return
    if sys.argv[1:2] in (["--version"], ["-V"]):
        print(__version__)
        return
    args, write_dir = sys.argv[1:], None
    if "--write-dir" in args:
        i = args.index("--write-dir")
        if i + 1 >= len(args):
            sys.exit("--write-dir needs a folder name")
        write_dir, args = args[i + 1], args[:i] + args[i + 2:]
    if not args and "VAULT_PATH" not in os.environ:
        sys.exit(__doc__.strip())
    vault = os.path.abspath(args[0] if args else os.environ["VAULT_PATH"])
    if not os.path.isdir(vault):
        sys.exit(f"Vault folder not found: {vault}")
    if write_dir:
        target = os.path.realpath(os.path.join(vault, write_dir))
        if not target.startswith(os.path.realpath(vault) + os.sep) or not os.path.isdir(target):
            sys.exit(f"--write-dir {write_dir!r} must be an existing folder inside {vault}")
    for line in sys.stdin:
        if line.strip():
            reply = respond(line, vault, write_dir)
            if reply is not None:
                sys.stdout.write(json.dumps(reply, ensure_ascii=False) + "\n")
                sys.stdout.flush()


if __name__ == "__main__":
    main()
