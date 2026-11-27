#!/usr/bin/env python3
"""Read-only MCP server (stdio) for an Obsidian vault. No Obsidian app, no dependencies.

Usage: obsidian-lite-mcp /path/to/vault [--write-dir Inbox]  MCP server on stdio; --write-dir enables `remember`
       obsidian-lite-mcp context /path/to/vault CWD           project brief for the repo in CWD (notes with `repo:` property)
"""
import json
import os
import re
import sys
import time

LINK_RE = re.compile(r"!?\[\[([^\]|#]*)[^\]]*\]\]")
TAG_RE = re.compile(r"(?<![\w/#&])#([^\W\d][\w/-]*)")
CODE_RE = re.compile(r"```.*?```|`[^`\n]*`", re.S)
MAX_RESULTS = 20
DATE_KEY = os.environ.get("VAULT_DATE_KEY", "created")  # frontmatter key for the date on notes `remember` creates


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


def load(vault):
    """Index every note. shortcut: rescans the vault on each call; fine for hundreds of notes, add caching past ~10k."""
    notes = {}
    for root, dirs, files in os.walk(vault):
        dirs[:] = sorted(d for d in dirs if not d.startswith("."))
        for f in sorted(files):
            if f.endswith(".md"):
                full = os.path.join(root, f)
                with open(full, encoding="utf-8", errors="replace") as fh:
                    text = fh.read()
                props, body = parse_frontmatter(text)
                tags = props.get("tags", [])
                tags = [tags] if isinstance(tags, str) else tags
                tags = {t.lstrip("#").lower() for t in tags if t}
                tags |= {t.lower() for t in TAG_RE.findall(CODE_RE.sub("", body))}
                rel = os.path.relpath(full, vault)
                notes[rel] = {"text": text, "props": props, "tags": tags, "mtime": os.path.getmtime(full),
                              "links": [l.strip() for l in LINK_RE.findall(text) if l.strip()]}
    return notes


def tok(text):
    """Rough token estimate shown to the agent so it can decide whether a fetch is worth it."""
    # shortcut: chars/2 measured on Polish notes (~1.8 chars/token, 2026-11-27); English runs ~4, so this overestimates there
    return f"~{len(text) // 2 + 1} tok"


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
    """Obsidian-style lookup: exact path, else note name; shortest path wins on ties."""
    t = name.strip().removesuffix(".md").lower()
    hits = [p for p in notes if p[:-3].lower() == t]
    if not hits:
        hits = [p for p in notes if p[:-3].lower().endswith("/" + t) or os.path.basename(p)[:-3].lower() == t]
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
            vals = vals[0] if isinstance(vals[0], list) else [vals[0]]
            if pval and not any(pval in v.lower() for v in vals):
                continue
        lines = []
        if rx:
            lines = [l.strip()[:160] for l in n["text"].splitlines() if rx.search(l)]
            if not lines and not rx.search(path):
                continue
        hits.append((len(lines), n["mtime"], f"{path} ({tok(n['text'])})" + "".join(f"\n    {l}" for l in lines[:3])))
    hits.sort(key=lambda h: (-h[0], -h[1]))  # most matching lines first, then most recently changed
    out = [h[2] for h in hits[:MAX_RESULTS]]
    if len(hits) > MAX_RESULTS:
        out.append(f"... {len(hits) - MAX_RESULTS} more, narrow the query")
    return "\n".join(out) or "No results."


def remember(vault, write_dir, title, text, tags=None):
    """Create a new note in write_dir. Never edits or overwrites an existing note."""
    title = (title or "").strip()
    if not title or len(title) > 120 or title.startswith(".") or re.search(r'[\\/:*?"<>|#^\[\]\n]', title):
        raise ValueError("Bad title: no / \\ : * ? \" < > | # ^ [ ], no leading dot, max 120 chars")
    vault = os.path.realpath(vault)
    target_dir = os.path.realpath(os.path.join(vault, write_dir))
    path = os.path.join(target_dir, title + ".md")
    if os.path.dirname(os.path.realpath(path)) != target_dir:
        raise ValueError("Path outside the write folder")
    tags = ["claude", *(t.lstrip("#") for t in (tags or []) if t and t.lstrip("#") != "claude")]
    front = "---\ntags:\n" + "".join(f"  - {t}\n" for t in tags) + f"{DATE_KEY}: {time.strftime('%Y-%m-%d')}\n---\n"
    try:
        with open(path, "x", encoding="utf-8") as f:
            f.write(front + text.strip() + "\n")
    except FileExistsError:
        raise ValueError(f"Note already exists: {os.path.relpath(path, vault)}; pick another title")
    return f"Saved: {os.path.relpath(path, vault)}"


def section(text, heading):
    """Lines from the matching heading up to the next heading of the same or higher level (code fences skipped)."""
    lines, start, level = text.splitlines(), None, 0
    for i, lvl, title in headings(text):
        if start is None and title.lower() == heading.strip().lower():
            start, level = i, lvl
        elif start is not None and lvl <= level:
            return "\n".join(lines[start:i])
    if start is None:
        raise ValueError(f"No such section: {heading}")
    return "\n".join(lines[start:])


def outline(text):
    """Headings with the size of each section's own text (up to the next heading), for picking one to read."""
    lines, hs = text.splitlines(), list(headings(text))
    out = [f"{'  ' * (lvl - 1)}{title} ({tok(chr(10).join(lines[i:hs[k + 1][0] if k + 1 < len(hs) else None]))})"
           for k, (i, lvl, title) in enumerate(hs)]
    return "\n".join(out) or "(no headings)"


def read(notes, name, outline_only=False):
    name, _, heading = name.partition("#")
    path = resolve(name, notes)
    if not path:
        raise ValueError(f"No such note: {name}")
    n = notes[path]
    if outline_only:
        return f"# {path} ({tok(n['text'])})\n{outline(n['text'])}"
    text = section(n["text"], heading) if heading else n["text"]
    outgoing = []
    for l in dict.fromkeys(l.strip() for l in LINK_RE.findall(text) if l.strip()):
        target = resolve(l, notes)
        outgoing.append(target or f"{l} (missing)")
    backlinks = [p for p, o in notes.items() if p != path and any(resolve(l, notes) == path for l in o["links"])]
    return (f"# {path}\n\n{text}\n\n---\nLinks: " + (", ".join(outgoing) or "none")
            + "\nBacklinks: " + (", ".join(backlinks) or "none"))


def as_list(value):
    return [value] if isinstance(value, str) else value or []


def context(notes, cwd):
    """Project brief for a repo: the note whose `repo` property names a folder in cwd, plus ADRs and open tasks beside it."""
    project = None
    for part in reversed(os.path.normpath(cwd).split(os.sep)):
        project = next((p for p, n in notes.items()
                        if part.lower() in (r.lower() for r in as_list(n["props"].get("repo")))), None)
        if project:
            repo = part
            break
    if not project:
        return ""
    # Index only, not content: the agent pulls details with `read` when needed (keeps every session start cheap).
    scope = os.path.dirname(project) + "/"
    in_scope = [p for p in notes if p.startswith(scope)]
    name = lambda p: os.path.basename(p)[:-3]
    body = parse_frontmatter(notes[project]["text"])[1]
    about = next((l.strip() for l in body.splitlines() if l.strip() and not l.lstrip().startswith(("#", "|", "!", "```"))), "")
    adrs = {}
    for p in in_scope:
        if "adr" in notes[p]["tags"]:
            adrs.setdefault(notes[p]["props"].get("status", "?"), []).append(name(p))
    tasks = [(name(p), len(re.findall(r"^\s*- \[ \] ", notes[p]["text"], re.M))) for p in in_scope]
    out = [f"Obsidian vault: repo `{repo}` → [[{name(project)}]]. {about[:200]}"]
    out += [f"ADR {status}: " + "; ".join(sorted(ns)) for status, ns in sorted(adrs.items())]
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
     "description": "Read a note by [[link]] name or path; 'Note#Heading' reads one section. Includes links and backlinks. "
                    "For big notes call with outline=true first (headings + ~tokens), then read one section.",
     "inputSchema": {"type": "object", "properties": {"name": {"type": "string"}, "outline": {"type": "boolean"}},
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


def handle(msg, vault, write_dir=None):
    method, params = msg.get("method"), msg.get("params") or {}
    if method == "initialize":
        return {"protocolVersion": params.get("protocolVersion", "2025-06-18"),
                "capabilities": {"tools": {}}, "serverInfo": {"name": "obsidian-lite-mcp", "version": "0.1.0"}}
    if method == "ping":
        return {}
    if method == "tools/list":
        return {"tools": TOOLS + ([REMEMBER] if write_dir else [])}
    if method == "tools/call":
        args = params.get("arguments") or {}
        try:
            if params.get("name") == "remember" and write_dir:
                text = remember(vault, write_dir, args.get("title"), args.get("text", ""), args.get("tags"))
                return {"content": [{"type": "text", "text": text}]}
            notes = load(vault)
            if params.get("name") == "search":
                text = search(notes, args.get("query"), args.get("tag"), args.get("property"))
            elif params.get("name") == "read":
                text = read(notes, args.get("name", ""), bool(args.get("outline")))
            else:
                raise ValueError(f"Unknown tool: {params.get('name')}")
            return {"content": [{"type": "text", "text": text}]}
        except (ValueError, re.error, OSError) as e:
            return {"content": [{"type": "text", "text": str(e)}], "isError": True}
    raise LookupError(method)


def main():
    if sys.argv[1:2] == ["context"]:  # server.py context VAULT CWD -> brief for a SessionStart hook
        if len(sys.argv) == 4 and os.path.isdir(sys.argv[2]) and os.environ.get("VAULT_CONTEXT") != "off":
            print(context(load(sys.argv[2]), sys.argv[3]))
        return
    args, write_dir = sys.argv[1:], None
    if "--write-dir" in args:
        i = args.index("--write-dir")
        write_dir, args = args[i + 1], args[:i] + args[i + 2:]
    vault = os.path.abspath(args[0] if args else os.environ["VAULT_PATH"])
    if write_dir and not os.path.isdir(os.path.join(vault, write_dir)):
        sys.exit(f"--write-dir {write_dir!r} is not a folder inside {vault}")
    if write_dir and not os.path.realpath(os.path.join(vault, write_dir)).startswith(os.path.realpath(vault) + os.sep):
        sys.exit("--write-dir must be inside the vault")
    for line in sys.stdin:
        if not line.strip():
            continue
        msg = json.loads(line)
        if "id" not in msg:
            continue  # notification
        try:
            reply = {"jsonrpc": "2.0", "id": msg["id"], "result": handle(msg, vault, write_dir)}
        except LookupError as e:
            reply = {"jsonrpc": "2.0", "id": msg["id"], "error": {"code": -32601, "message": f"Method not found: {e}"}}
        sys.stdout.write(json.dumps(reply, ensure_ascii=False) + "\n")
        sys.stdout.flush()


if __name__ == "__main__":
    main()
