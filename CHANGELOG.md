# Changelog

## 0.1.0

First release.

- `plugin/signoff/signoff.py`: serves a decisions file, or a folder of `round-NN.json` rounds as tabs, on
  `127.0.0.1`; autosaves the answers beside each; on Done exits and prints one answers path per line. Resumes
  from existing answers files. Idle timeout (`--timeout`, default 120 minutes) with exit code 2; `--no-open`
  for SSH.
- Rounds are accepted one by one (**Accept this round**, Ctrl/Cmd+Enter), with a progress count, a ↺ mark when an
  accepted round changes, and a **Summary** tab; Done waits for every round, on the page and on the server. The
  answers file records `accepted`, `accepted_at` and `changed_after_accept`.
- Rounds: a round answered with Done is final and shows read only in later runs, so the user can look back. Done
  closes every open round at once. A broken round stops the run before anything is served, naming the file, the item
  and the field.
- The page: items with pictures and orange highlight boxes, costs, groups, single or multiple choice with the
  recommendation preselected and labelled, a comment per item, a free-text box at the end. Light and dark,
  keyboard accessible, phone width. Text and controls meet WCAG AA contrast in both themes, a Done button
  that is still waiting included.
- Security: a per-run token in the path, a single-use start link for the browser, exact `Host` check,
  JSON-only same-origin saves, size and content checks on every save, images served by index from an allowlist,
  no SVG, a nonce-based Content-Security-Policy.
- Per item, optional **Other** with a text field (`other: true`) and **Skip** (`skippable: true`), recorded as
  the choices `_other` and `_skip`.
- Keyboard shortcuts: `1` to `9` pick an option, `j` and `k` move between items, `?` lists them.
- The page's own labels in English and Hungarian, chosen by `lang`.
- Each answers file names its decisions file and that file's SHA-256.
- A second tab or window cannot overwrite newer answers: each save carries the round's save count, and an
  out-of-date save, acceptance or Done is refused with a note to reload; the reloaded page shows the newer
  answers. The server also checks the answers file is unchanged on disk, which catches a second run on the same
  folder on its next save (best effort across runs).
- Boards: an item's `images` entry may be `{"html": ...}`, the agent's own HTML shown live in a sandboxed frame
  (`sandbox allow-scripts`, under a token of its own, nothing loaded from outside) with light/dark and 1280/768/390 px switches.
- Marks: with `marks: true` the user draws rectangles on pictures, each with a comment, by pointer or keyboard;
  stored in the answers as `marks`.
- `--history <root>`: a read-only page of every past round in a project, with filters, outcomes
  (`<round>.outcome.json`), warnings when decisions changed after they were answered, `--folder`, `--since` and
  an image budget.
- A **Decisions folder** setting (default `.signoff`): where the skill puts topic folders and where the
  past-decisions page looks.
- `plugin/signoff/mcp_server.py`: an MCP server for other agents, with `signoff_open` and `signoff_status`; not
  registered by the plugin.
- A plugin icon.
- **Accept all recommendations** in the header, undoable; each answer records its `source` (`recommended`,
  `accept_all` or `user`).
- An optional `notice` in the decisions file, shown as a banner under the header.
- A Markdown copy of the answers, `<name>.answers.md`, written with every save, all text escaped.
- Writes a `.gitignore` for `*.answers.json` and `*.answers.md` into the topic folder when it has none.
- `plugin/signoff/schema.json`: the decisions file's JSON Schema.
- The `signoff` skill: when to use Signoff, where the decisions folder goes, how to batch rounds, one picture
  per option, how to run it in the background and act on the answers.
- `plugin/` is the whole plugin, with its own README and LICENSE; tests, examples and media stay outside it.
- A `signoff` command (`plugin/bin/signoff`, `signoff.cmd` on Windows) that runs the same program. The
  plugin's `bin/` folder keeps it to Claude Code on your own computer.
