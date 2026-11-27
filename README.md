# obsidian-lite-mcp

<!-- mcp-name: io.github.DabuSoft-pl/obsidian-lite-mcp -->

A tiny MCP server that lets AI agents read an Obsidian vault straight from disk.

- **No Obsidian app running**, no plugins, no REST API: the vault is just a folder of `.md` files.
- **No shell needed**: works in MCP clients that can't run `grep` at all, such as Claude Desktop.
- **Links the way Obsidian sees them**: backlinks that match case-insensitively, through `[[Folder/Note]]` paths,
  aliases and Markdown links, and skip links inside code. Plain `grep "[[Name"` gets these wrong.
- **One file, zero dependencies**: Python standard library only.
- **Read-only by default.** Writing is opt-in and limited to creating new notes in one folder.
- **Pull only what's needed**: every result carries its size (`~N tok`), and a big note can be read one section
  at a time. See [Measured](#measured) for what that buys and where it doesn't.

## Tools

| Tool | What it does |
|---|---|
| `search` | Regex over text and paths, plus `tag` and frontmatter `property` filters (`key` or `key=value`). Ranked by matches, then recency. |
| `read` | A note by `[[link]]` name, alias or path, with outgoing links and backlinks. `Note#Heading` returns one section; `outline: true` returns headings with the size of each section. Notes over 8000 tokens return their outline unless `full: true`. |
| `remember` | Only with `--write-dir`: creates a new note in that folder, tagged `claude`. Never edits or overwrites. |

Links resolve the way Obsidian does: exact path first, then note name or `aliases`, shortest path on ties.
Markdown links (`[text](Folder/Note.md)`) count too. Tags come from frontmatter and `#inline` tags outside code.
Folders listed in Obsidian's *Excluded files* setting are skipped, as are hidden folders like `.obsidian` and `.trash`.

Fast on big vaults: files are re-read only when they change, and on a 5000-note vault a `read` with backlinks
takes about 0.05 s.

## Install

```sh
uvx obsidian-lite-mcp /path/to/vault
```

Or skip packaging entirely: download `obsidian_lite_mcp.py` and run `python3 obsidian_lite_mcp.py /path/to/vault`.

### Claude Code

```sh
claude mcp add -s user vault -- uvx obsidian-lite-mcp /path/to/vault
```

To let the agent save new notes into `Inbox/` (date key in their frontmatter: `VAULT_DATE_KEY`, default `created`):

```sh
claude mcp add -s user vault -- uvx obsidian-lite-mcp /path/to/vault --write-dir Inbox
```

### Other clients

Any MCP client that runs stdio servers works:

```json
{
  "mcpServers": {
    "vault": { "command": "uvx", "args": ["obsidian-lite-mcp", "/path/to/vault"] }
  }
}
```

## Project context at session start

Give a project note a `repo` property listing the repository folder names:

```yaml
---
repo:
  - my-api
  - my-frontend
---
```

`obsidian-lite-mcp context /path/to/vault "$PWD"` then prints a short index for whichever repo you are in:
the note's first line, ADRs (notes tagged `adr`) grouped by `status`, open `- [ ]` task counts and the three most
recently changed notes, all from the project note's folder. Measured in a real Claude Code session: about 450 tokens.
Details come from `read`.

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
            "command": "uvx obsidian-lite-mcp context /path/to/vault \"$CLAUDE_PROJECT_DIR\"",
            "timeout": 10
          }
        ]
      }
    ]
  }
}
```

Outside a known repo it prints nothing. `VAULT_CONTEXT=off` silences it for one run (handy for A/B token measurements).

## Measured

Same questions, Claude Code headless (`claude -p`, Haiku), two runs per setup. *grep* had only Read, Grep and Glob;
*obsidian-lite-mcp* had only this server's tools. Tokens are total input tokens for the whole task.

**Obsidian's own help vault** ([obsidianmd/obsidian-help](https://github.com/obsidianmd/obsidian-help) `en/`,
181 notes): two backlink counts, two answers from sections of a 16k-token note, one alias lookup.

| Setup | Correct | Turns | Input tokens | Output tokens | Cost |
|---|---|---|---|---|---|
| grep | 5/5, 5/5 | 20, 15 | 821k, 510k | 12.6k, 11.6k | $0.084, $0.026 |
| obsidian-lite-mcp | 5/5, 5/5 | 11, 12 | 225k, 277k | 3.7k, 4.3k | $0.014, $0.013 |

Both got everything right, but only because the prompt spelled out Obsidian's link rules; grep needed about
twice the turns and 2-4x the tokens to apply them. Naive `grep -rlF "[[Command palette"` reports 62 backlinks
instead of 64 (it misses `[[command palette]]`), and 32 instead of 31 for Properties (it counts `[[Properties view]]`).

**A small personal vault** (90 notes, simple links): five lookups (ADR status, backlink count, a section, open
tasks, find by topic). Both setups 5/5; input tokens grep 184k and 123k, obsidian-lite-mcp 147k and 247k.
**No measurable difference**: on a small vault with plain links, grep is just as good.

The `context` hook costs about 450 input tokens per session (A/B with `VAULT_CONTEXT=off`).

## Limits

- Every call checks file timestamps (about 0.04 s for 5000 notes); the first call reads everything (about 1 s).
- Frontmatter parsing covers scalars, `[a, b]` and `- item` lists; nested YAML is skipped.
- Token counts are estimates (characters / 2, measured on Polish notes; English text is cheaper).
- `.base` and `.canvas` files are not indexed.

## Development

```sh
python3 -m unittest
```

## License

MIT
