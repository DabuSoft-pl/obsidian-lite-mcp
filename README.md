# vault-mcp

A tiny MCP server that lets AI agents read an Obsidian vault straight from disk.

- **No Obsidian app running**, no plugins, no REST API: the vault is just a folder of `.md` files.
- **One file, zero dependencies**: Python standard library only.
- **Read-only by default.** Writing is opt-in and limited to creating new notes in one folder.
- **Token-lean**: the agent sees an index first and pulls only the part it needs.

## Tools

| Tool | What it does |
|---|---|
| `search` | Regex over text and paths, plus `tag` and frontmatter `property` filters (`key` or `key=value`). Ranked by matches, then recency. Every hit shows its size (`~N tok`). |
| `read` | A note by `[[link]]` name or path, with outgoing links and backlinks. `Note#Heading` returns one section; `outline: true` returns headings with the size of each section. |
| `remember` | Only with `--write-dir`: creates a new note in that folder, tagged `claude`. Never edits or overwrites. |

Wikilinks resolve the way Obsidian does: exact path first, then note name, shortest path on ties.
Tags come from frontmatter and `#inline` tags outside code.

## Install

```sh
uvx --from git+https://github.com/<owner>/vault-mcp vault-mcp /path/to/vault
# or simply: python3 vault_mcp.py /path/to/vault
```

### Claude Code

```sh
claude mcp add -s user vault -- uvx --from git+https://github.com/<owner>/vault-mcp vault-mcp /path/to/vault
# allow new notes in Inbox/ (date key in their frontmatter: VAULT_DATE_KEY, default "created"):
claude mcp add -s user vault -e VAULT_DATE_KEY=created -- python3 /path/to/vault_mcp.py /path/to/vault --write-dir Inbox
```

Any other MCP client works the same way: stdio, one command.

## Project context at session start

Give a project note a `repo` property listing the repository folder names:

```yaml
---
repo:
  - my-api
  - my-frontend
---
```

`vault-mcp context /path/to/vault "$PWD"` then prints a short index for whichever repo you are in:
the note's first line, ADRs (notes tagged `adr`) grouped by `status`, open `- [ ]` task counts and the three most
recently changed notes, all from the project note's folder. In a real session it measured about 450 tokens; details come from `read`.

In Claude Code, add it as a `SessionStart` hook in `~/.claude/settings.json`:

```json
{
  "hooks": {
    "SessionStart": [
      {
        "matcher": "startup|clear|compact",
        "hooks": [
          {
            "type": "command",
            "command": "python3 /path/to/vault_mcp.py context /path/to/vault \"$CLAUDE_PROJECT_DIR\"",
            "timeout": 5
          }
        ]
      }
    ]
  }
}
```

Outside a known repo it prints nothing. `VAULT_CONTEXT=off` silences it for one run (handy for A/B token measurements).

## Limits

- The vault is rescanned on every call: instant for hundreds of notes, slow past roughly ten thousand.
- Frontmatter parsing covers scalars, `[a, b]` and `- item` lists; nested YAML is skipped.
- Token counts are estimates (characters / 2, measured on Polish notes; English text is cheaper).
- Markdown links `[text](note.md)` and `.base` files are not indexed.

## Tests

```sh
python3 -m unittest
```

## License

MIT
