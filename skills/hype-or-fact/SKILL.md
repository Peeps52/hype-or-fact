---
name: hype-or-fact
description: Decide whether a new Claude Code skill, plugin, MCP server or agent tool is worth installing. Checks it against what the user already has installed, audits the code, then measures it with a sandboxed, blind with/without A/B test on the user's own tasks. Use whenever the user shares a GitHub repo, reel, TikTok, tweet or video about a Claude or agent tool and asks whether it is any good, worth it, legit, hype, or should be installed; when they ask what is trending or new for Claude Code; or when they say "hype or fact", "is this worth it", "should I add this skill". Use it even when they paste a tool link with no question; a pasted tool link means "judge this". For links about a Claude or agent tool, prefer this skill over general web-reading or link-fetching skills.
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

**A video (reel, TikTok, YouTube, X).** Run
`python3 scripts/video.py <url> --out <tmp>/video`. It prints JSON with the
title, uploader, post caption, a transcript and frame paths. The transcript
comes from platform captions, or from a local Whisper when there are none;
the script finds the model itself, or uses `$WHISPER_MODEL`. Read the
frames for on-screen text such as burned-in captions, cost or time
readouts, and repo names. If `transcript` is empty, `transcript_source`
says why. Work from the caption and frames then, and say so in the verdict.

If the script reports a login-only or restricted post, say so plainly and
ask the user for the tool's name, a screenshot, or permission to view the
post in their logged-in browser. Don't load their browser cookies into a
downloader on your own initiative. Carry on with any other links meanwhile.

Creators often gate the link ("comment X for the link"), so get the tool's
*name* from the speech, on-screen text and caption. Then search
`gh search repos "<name>" --sort stars --limit 5`, plus a web search if
needed. Confirm the match (description, author handle, creation date) before
going on; same-named repos are common.

**Write down the creator's claims** while you have the video: each
concrete, checkable promise, close to their words. For example, "turns any
project into a launch video in seconds", "30% cheaper", "scores your CV the
way the ATS will". Note any numbers shown on screen, including ones that
contradict the pitch, such as a run time or a cost. These claims are what
made the user curious, so they are what the verdict has to answer. If the
candidate came from a repo link, take the claims from the README instead.

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
yourself. Work out **what it actually does**, from the code rather than the
marketing: the mechanism (a prompt, a CLI, a server, an installer for other
tools), what it installs or changes on the machine, and what it needs (API
keys, logins, network). Then check each claim you wrote down against the
README and the code. Creators routinely promise more than the author does.
Look for the gap between claims and code:

- Does the code do what the README promises, or is it a prompt behind a
  marketing page? A 40-line SKILL.md can be excellent. Just call it what it is.
- Hype signals: star velocity far above the commit count; a days-old repo
  with one commit; benchmarks claimed with no method; "works with any agent"
  meaning a markdown file; a hard dependency on a paid API that the README
  buries.
- Fact signals: tests, runnable examples, issues that get answered, the
  author using it in their own public work.

Three audit findings change the verdict more than their MED label suggests:

- **Trigger-greedy description** ("MUST USE", "any URL"). Once installed, it
  takes requests away from skills the user already relies on, including
  link-driven ones like this skill. Name which installed skills it would
  compete with.
- **Uses your browser logins or cookies.** The tool reaches sites through
  the user's real accounts. That is an account-ban risk under most platforms'
  terms, and session cookies sit in files on disk. Say both plainly.
- **Installs from a moving branch.** What gets installed later may not be
  what you audited. Any install should be pinned to the audited commit.

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

The A/B step runs third-party code and costs usage. On an API key that is
billed money. On a Claude subscription it comes out of the plan's usage
limits, and the dollar figures the script prints are API-equivalent
estimates, not charges. Say which applies. Each `claude -p` run pays to load
the user's whole setup, often 0.20 to 0.40 USD-equivalent before any work,
and a substantial task costs 1 to 6 USD-equivalent. Only take candidates
that survived steps 1 and 2, and at most three per session.

Choose the tasks, then print the exact plan without spending anything:

```bash
python3 scripts/ab_test.py --candidate <tmp>/<name> --kind skill|plugin|mcp \
  --project <a real project of theirs> --task "..." --task "..." \
  --out <tmp>/<name>-ab --budget 2 --dry-run
```

Show the user the candidates, tasks, and worst-case cost from the dry run,
and wait for a yes. One yes covers that plan. A new candidate or a bigger
budget needs another.

**Choosing tasks** matters more than anything else in the test. Where the
creator's headline claim can be tested, make it the first task, because
that is the claim the user wants settled. Use the
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

Some candidates can't be A/B tested here by design:
- **Command-line tools** (kind `cli`) are installed globally, outside any sandbox.
- **Tools whose whole point is network access** (web or social-media
  readers, API clients) are crippled by the network block.

Don't spend on a run that can only return UNTESTED. Judge from the code,
the docs and the overlap, and say that this was the basis.

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

Every verdict has the same three parts, in this order. Keep them short,
and match the user's usual preference for prose or tables:

1. **The verdict**, in one line.
2. **What it actually does**, in plain words, from the code: the mechanism,
   what it installs or changes, and what it needs from the user. Someone who
   has never seen the repo should understand what they would be installing.
   "Three prompts, not a tool" or "an installer for a dozen scraping CLIs"
   is the level of plainness to aim for.
3. **The claims check**: each claim from the video or README, with what
   happened to it: *held* (the code and test support it), *overstated*
   (true in a weaker form; say which), *unsupported* (nothing in the code or
   tests backs it), or *untested* (say why). Say whether an overstatement
   came from the creator or is in the README itself.

The verdict categories:

- **FACT**: clearly better on the user's own tasks, at an acceptable cost.
- **NICHE**: works, but only for something the user rarely does, or only
  when invoked by name.
- **ALREADY HAVE**: covered by an installed tool. Name which.
- **HYPE**: no measurable difference, doesn't work, or the claims outrun the
  code. Also when it **works, but the negatives outweigh the positives**
  for this user, e.g.:
  - cost or time multiples out of proportion to the gain;
  - always-on context cost for something rarely used;
  - a trigger-greedy description that takes requests from skills they rely on;
  - account-ban or credential exposure from using their logins;
  - unsandboxed hooks or servers for a small benefit.
- **UNSAFE**: a reachable HIGH flag or an unacceptable unsandboxed part.
  Give file:line.
- **UNTESTED**: couldn't be tested in isolation. Give the reason.

Weigh positives against negatives explicitly before choosing between FACT,
NICHE and HYPE. A tool that genuinely works can still be HYPE for this user.
When that decides the verdict, list both sides briefly: what it really does
well, what it costs, and why the costs win. The user can then disagree with
your weighting, which they can't do with a bare label. The distinction from
the neighbours: NICHE means the benefit is real but narrow, and the costs
are small enough to be worth paying for that narrow use. UNSAFE means a
specific security problem, not a weighing.

Then give the evidence behind it:
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

**If the user installs against the verdict**, that's their call. Make the
install as safe as the tool allows:
- **Pin it** to the commit you audited, e.g.
  `uv tool install "git+https://github.com/<owner>/<repo>@<sha>"`, never the
  moving branch or a raw-URL install doc.
- **Use the tool's most conservative mode first**, such as a read-only
  check or doctor command.
- **Don't connect accounts, cookies or API keys yourself.** List them as
  steps for the user.
- **Afterwards, report every side effect**: commands added, directories
  written (including ones for other agents), a trigger-greedy skill now
  active, and the uninstall command.

Finally, append one line per candidate to
`${CLAUDE_CONFIG_DIR:-~/.claude}/hype-or-fact/ledger.md` (create it if
needed): `YYYY-MM-DD | owner/name | VERDICT | one-line reason | spent`. Add
`INSTALLED @<sha>` when it was installed.
Then delete the clones and A/B directories unless the user wants to inspect
them.
