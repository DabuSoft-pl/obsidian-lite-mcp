# Changelog

## 1.0.1

- Tool descriptions: what `search` returns, and a description for every parameter (`tag`, `name`, `outline`, `remember` fields).

## 1.0.0

First public release.

- `search`: regex, tag and frontmatter property filters, ranked by matches then recency, `~N tok` per hit.
- `search` `links: broken | orphans`: unresolved links (attachments excluded) and notes without backlinks.
- `read`: wikilinks, aliases and Markdown links resolved like Obsidian; outgoing links and backlinks; `Note#Heading`
  sections; `outline` mode; notes over 8000 tokens return their outline unless `full: true`.
- `remember` (opt-in with `--write-dir`): creates new notes only, never overwrites.
- `context`: a short project index for a `SessionStart` hook, driven by a `repo` frontmatter property.
- Honours Obsidian's excluded files, matches NFD file names (macOS) against NFC links, works on Windows paths.
- Re-reads only changed files between calls; backlinks stay fast on large vaults (5000 notes: ~0.05 s).
- Survives malformed JSON-RPC input instead of exiting.
