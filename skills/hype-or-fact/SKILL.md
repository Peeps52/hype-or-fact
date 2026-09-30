---
name: hype-or-fact
description: Decide whether a new Claude Code skill, plugin, MCP server or agent tool is worth installing. Checks it against what the user already has installed, audits the code, then measures it with a sandboxed, blind with/without A/B test on the user's own tasks. Use whenever the user shares a GitHub repo, reel, TikTok, tweet or video about a Claude or agent tool and asks whether it is any good, worth it, legit, hype, or should be installed; when they ask what is trending or new for Claude Code; or when they say "hype or fact", "is this worth it", "should I add this skill". Use it even when they paste a tool link with no question; a pasted tool link means "judge this".
---

# Hype or Fact

Viral Claude tools get reviewed by showing one impressive output. That can't
tell you what matters: **what the tool adds to the setup this user already
has.** This skill answers that in order of increasing cost, and stops as soon
as the answer is clear:

1. Is it relevant? (free)
2. Is it already covered by something installed? (free)
3. Is the code real and safe? (free)
4. Does it beat the current setup on the user's own tasks? (costs money, needs a yes)

Most candidates are settled by step 2. People with large setups rarely need
another skill that does what one of theirs already does. Every installed
skill's description also costs context tokens in every session, used or not.

The scripts are in `scripts/`, in the same directory as this SKILL.md. Use
the skill's base directory that Claude Code reports when the skill loads.
They need Python 3.9+, `git`, the GitHub CLI `gh` (logged in) and the
`claude` CLI. Work in a temporary directory, never inside the user's Claude
config directory.

## 0. Get the candidates

**A repo link.** Run `python3 scripts/trending.py --repo <url-or-owner/name>`.

**A video (reel, TikTok, YouTube, X).** If a video-watching skill is
installed, use it. Otherwise:
`yt-dlp -o video.mp4 <url>` and `yt-dlp --skip-download --write-info-json <url>`
(the caption is in the `.info.json`). For speech, use native captions if
there are any. If there aren't and `whisper-cli` or `whisper` is installed,
run `ffmpeg -i video.mp4 -ac 1 -ar 16000 a.wav` and transcribe that. For
on-screen text, extract a few frames with ffmpeg and look at them.

Creators often gate the link ("comment X for the link"), so get the tool's
*name* from the speech, on-screen text and caption. Then search
`gh search repos "<name>" --sort stars --limit 5`, plus a web search if
needed. Confirm the match (description, author handle, creation date) before
going on; same-named repos are common.

**No link: "what's trending".** Run
`python3 scripts/trending.py --days 30 --top 25`. It ranks recently created
repos by stars per day, the closest thing GitHub has to trending. Triage to
the 3 to 5 most relevant to how this user actually works, using what you know
of their projects. Mention how many unrelated repos you dropped, and no more.

Read `${CLAUDE_CONFIG_DIR:-~/.claude}/hype-or-fact/ledger.md` if it exists,
and skip anything already judged unless it has had a major release since.

## 1. Overlap with what is installed

`python3 scripts/inventory.py` lists installed skills, plugins, MCP servers
and CLAUDE.md sections. Then run
`python3 scripts/inventory.py --match term1,term2,...` with the candidate's
core nouns and verbs (e.g. `diagram,excalidraw,svg`) to get full
descriptions of the likely overlaps.

Judge overlap by *capability*, not by name. A "launch video" plugin overlaps
an installed video-generation skill even if no words are shared. There are
three outcomes:

- **Covered.** An installed tool does the same job. The verdict is ALREADY HAVE,
  unless the candidate's claim is specifically "better than X" and the user
  relies on X. Then the A/B can pit them against each other.
- **Partial.** It adds something specific. Name exactly what.
- **New.** Nothing installed does this.

## 2. Read the code and audit it

```bash
git clone --depth 1 https://github.com/<owner>/<name> <tmp>/<name>
python3 scripts/audit.py <tmp>/<name>
```

The audit reports the kind of candidate, any parts that **run outside Claude
Code's sandbox** (plugin hooks, MCP and LSP servers, hooks in skill
frontmatter), red-flag code patterns, and the always-on context cost.

Then read the README and the SKILL.md, plugin manifest or server entry point
yourself, looking for the gap between the claims and the code:

- Does the code do what the README promises, or is it a prompt behind a
  marketing page? A 40-line SKILL.md can be excellent. Just call it what it is.
- Hype signals: star velocity far above the commit count; a days-old repo
  with one commit; benchmarks claimed with no method; "works with any agent"
  meaning a markdown file; a hard dependency on a paid API that the README
  buries.
- Fact signals: tests, runnable examples, issues that get answered, the
  author using it in their own public work.

Check *where* each flag sits. Repos often keep dev-only tooling (a `.claude/`
folder inside the repo, `tests/`, benchmark scripts) that an install never
loads. Say whether a flagged file is reachable from what gets installed.

- **A reachable HIGH flag** stops everything. Report the file and line, and
  run nothing from the repo. The verdict is UNSAFE.
- **Unsandboxed parts** (hooks, servers) run with the user's full
  permissions in every session. Say so plainly: hooks are the highest-trust
  thing a plugin can add. The A/B script refuses these unless given
  `--allow-unsandboxed`. Only suggest that after you have read the hook or
  server code and found nothing wrong, and the user agrees.

## 3. Ask before spending

The A/B step runs third-party code and costs real money. Each `claude -p`
run pays to load the user's whole setup, often $0.20 to $0.40 before any
work, and a substantial task costs $1 to $6. Only take candidates that
survived steps 1 and 2, and at most three per session.

Choose the tasks, then print the exact plan without spending anything:

```bash
python3 scripts/ab_test.py --candidate <tmp>/<name> --kind skill|plugin|mcp \
  --project <a real project of theirs> --task "..." --task "..." \
  --out <tmp>/<name>-ab --budget 2 --dry-run
```

Show the user the candidates, tasks, and worst-case cost from the dry run,
and wait for a yes. One yes covers that plan. A new candidate or a bigger
budget needs another.

**Choosing tasks** matters more than anything else in the test. Use the
user's *real* work: a copy of an actual project (`--project`; `.git`,
secrets, databases and symlinks are left out of the copy) and a task they
would plausibly ask for that the candidate claims to help with. At least one
task should be something they do often, not the candidate's showcase demo.
A tool that shines on its own demo and ties on the user's work is hype for
this user.

## 4. Run the A/B

Run the same command without `--dry-run`. Useful extra flags:
- `--mcp-config <file>`: required for `--kind mcp`, a JSON file with `mcpServers`.
- `--skill-dir <path>`: pick one skill out of a multi-skill repo.
- `--repeat 2`: noise control when the first result is close.
- `--allow-web`: when the task genuinely needs the web.

What the script does:
- **Runs each task twice**, at the same time, in two fresh copies. The
  *baseline* is the user's normal setup and the *candidate* run adds only the
  candidate. Nothing is installed into their config.
- **Probes the sandbox first.** A cheap probe run proves Claude Code's OS
  sandbox is working, i.e. that a write outside the working copy is blocked,
  before any candidate code runs. If the probe fails, nothing runs. Tell the
  user; don't work around it.
- **Restricts tools.** Web tools are off unless `--allow-web`, and the user's
  own MCP servers are not loaded, so the candidate cannot drive them.
- **Blocks outbound network** from sandboxed Bash, so a tool that downloads
  things at runtime may fail. Report that as UNTESTED, not HYPE.

It prints cost, time, turns and output tokens per run, plus **loaded/used**
for the candidate:

- **NOT LOADED** is a harness problem. Read `<pair>/candidate/stream.jsonl`
  and fix it before judging anything.
- **Loaded but not used** is a real finding: the tool doesn't trigger on a
  realistic request. With many skills installed, this is common. Rerun
  once with the task naming the tool ("use the X skill to ...") to separate
  "doesn't trigger" from "doesn't help".
- A warning that the candidate was **visible in the baseline** means it is
  already installed and the comparison is contaminated.

## 5. Judge blind, then reveal

For each pair, open `blind/<pair>/task.md`, then the `files/` in `A/` and
`B/`. Judge the files **before** reading either `answer.md`. The script masks
the candidate's name in the answers, but they can still give the game away
("I couldn't find a skill for this, so..."). Render or run the outputs when
that is the honest way to judge them: open the image, run the code, check
the numbers.

Write down your judgement for each pair before opening `key.json`: which is
better, by how much (clearly, slightly, or a tie), and why, in one line.
Only then read the key. Knowing which output came from the new tool biases
the read toward it, and this skill exists to correct exactly that bias. If
you saw something that unblinded a pair, say so in the verdict.

With one run per side, a slight win is noise. Say so rather than calling it
a result.

Weigh quality against the cost and time multiples. In this skill's first
real test, a logo skill produced a clearly better logo, but at 1.6x the cost
and 5x the time, and only when asked for by name. It never triggered on a
plain "design a logo" request among 200+ installed skills. That is NICHE
("invoke it by name when a logo matters"), not FACT.

## 6. Verdict

Lead with the verdict. Write one short paragraph per candidate with the
numbers inline, and match the user's usual preference for prose or tables.

- **FACT**: clearly better on the user's own tasks, at an acceptable cost.
- **NICHE**: works, but only for something the user rarely does, or only
  when invoked by name.
- **ALREADY HAVE**: covered by an installed tool. Name which.
- **HYPE**: no measurable difference, doesn't work, or the claims outrun the
  code.
- **UNSAFE**: a reachable HIGH flag or an unacceptable unsandboxed part.
  Give file:line.
- **UNTESTED**: couldn't be tested in isolation. Give the reason.

Give the evidence behind the verdict:
- the blind result per pair;
- the cost and time multiples;
- loaded/used;
- the always-on context cost from the audit;
- what you couldn't check.

The user should come away with something checkable instead of a creator's
word.

Only a FACT verdict comes with an install offer. Show the exact command,
e.g. `/plugin install ...` or copying the skill folder into the skills
directory, and **install only on an explicit yes**. For NICHE, say when it
would be worth having.

Finally, append one line per candidate to
`${CLAUDE_CONFIG_DIR:-~/.claude}/hype-or-fact/ledger.md` (create it if
needed): `YYYY-MM-DD | owner/name | VERDICT | one-line reason | $spent`.
Then delete the clones and A/B directories unless the user wants to inspect
them.
