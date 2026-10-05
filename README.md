# Hype or Fact

**Is that viral Claude Code tool actually worth installing?**

Every week a new skill, plugin or MCP server goes viral with a demo that
looks incredible. A demo can't tell you what matters: *what does it add to
the setup you already have?*

Hype or Fact is a Claude Code skill that answers that with evidence. It:

1. **Finds** candidates. Paste a GitHub link or a reel/TikTok/YouTube link,
   or ask what's trending. Trending means new repos ranked by stars per day.
2. **Checks overlap** with what you already have installed (skills, plugins,
   MCP servers). This settles most candidates for free.
3. **Audits the code** for red flags, and for anything that would run
   *outside* Claude Code's sandbox, such as hooks and MCP servers.
4. **Measures it** after asking you. It runs your own tasks twice, with and
   without the candidate, in throwaway sandboxed copies of your project, and
   compares the outputs **blind**.
5. **Gives a verdict** (FACT, NICHE, ALREADY HAVE, HYPE, UNSAFE or UNTESTED)
   with the numbers behind it: blind result, cost and time multiples, whether
   the tool even triggered, and how much context it costs every session.

The blind part matters. Knowing which output came from the shiny new tool
biases you towards it. So the outputs are shuffled into `A` and `B`, the
candidate's name is masked, and the judgement is written down before the key
is opened.

## A real result

In the first test, a trending logo-design plugin with 900+ stars in 4 days:

- **Plain request** ("design a logo for …"): the plugin was **loaded but
  never invoked**. With 200+ skills installed, it lost the trigger every
  time, so the output was no better than without it.
- **Named explicitly** ("use the logo-design skill …"): the logo was clearly
  better, at **1.6× the cost and 5× the time**.

Verdict: **NICHE**. Invoke it by name when a logo matters; it won't help on
its own. No demo video would tell you that.

## Install

As a plugin:

```
/plugin marketplace add Peeps52/hype-or-fact
/plugin install hype-or-fact@hype-or-fact
```

Or as a plain skill:

```bash
git clone https://github.com/Peeps52/hype-or-fact
cp -r hype-or-fact/skills/hype-or-fact ~/.claude/skills/
```

Then ask Claude things like "is this worth installing?" with a link, "hype
or fact: <link>", or "what's trending for Claude Code?".

**Requirements:** Claude Code (a recent version), Python 3.9+, `git`, and
the GitHub CLI (`gh auth login`). A/B tests need **macOS or Linux**. On
Linux, Claude Code's sandbox also needs `bubblewrap` and `socat`. Video
links additionally need `yt-dlp` and `ffmpeg`. Without platform captions,
speech needs a local Whisper (`whisper-cli` from whisper.cpp with a model,
or `whisper`); set `WHISPER_MODEL` if the model isn't found. Without
one, the skill works from the caption and on-screen text. Everything else is the Python
standard library.

## Safety model

The A/B step runs someone else's code, so the defaults are strict, and they
are enforced by the script, not left to instructions:

- **Nothing runs without your yes.** The skill shows the plan and worst-case
  cost from a `--dry-run` and waits for you to approve it.
- **The sandbox is proven, not assumed.** Before any candidate code runs, a
  probe checks that Claude Code's OS sandbox blocks a write outside the
  working copy *and* blocks reading a denied canary file. If either check
  fails, or can't be verified, nothing runs.
- **Writes** are confined to a throwaway copy of your project.
- **Reads** of common secret locations (`~/.ssh`, `~/.aws`, `~/.gnupg`, `gh`
  and Docker/Kube credentials, `~/.claude.json`, `.env` files and more) are
  denied.
- **Your project copy** leaves out `.git`, `.env` and key files,
  credentials, databases and symlinks, and has a size cap.
- **No web tools, no MCP servers.** WebFetch/WebSearch are off unless you
  pass `--allow-web`. Your own MCP servers are not loaded, so a candidate
  can't drive your Gmail, Drive and so on.
- **Nothing is installed** into your Claude config. Candidates load only for
  their own run.
- **Spend is capped** per run and in total. A plan usage-limit message is
  detected and stops the run, rather than being scored as an empty "tie".

**What it cannot contain:** plugin **hooks** and **MCP/LSP servers**. Claude
Code runs these as separate processes outside the Bash sandbox. Candidates
that have them are refused unless you pass `--allow-unsandboxed`, which you
should only do after reading that code. A candidate can also read files your
user can read, apart from the denied locations above. Those contents go to
the model, as with any Claude Code session, but it can't write them outside
the copy or send them over the network.

The static audit is a tripwire, not a guarantee: a clean audit does not mean
a repo is safe.

## Scripts

The skill drives these. You can also run them yourself:

| script | what it does |
|---|---|
| `trending.py` | new repos in the Claude Code ecosystem, ranked by stars/day, or one repo with `--repo` |
| `inventory.py` | what you have installed; `--match a,b` for likely overlaps |
| `audit.py` | kind, unsandboxed parts, red flags, always-on context cost |
| `ab_test.py` | the sandboxed blind A/B test (`--dry-run` to see the plan and cost) |

Each run uses your Claude usage. With an API key it is billed in dollars.
On a Claude subscription it comes out of your plan's usage limits instead,
and the dollar figures are API-equivalent estimates, not charges. A
`claude -p` run pays to load your whole setup, often $0.20 to $0.40 worth
before any work, and a substantial task $1 to $6 worth per side. `--budget` caps each run and
`--max-total` caps the whole test.

## Development

```bash
pip install pytest
pytest -q
```

The tests are offline. `tests/fakes/claude` is a stand-in for the Claude CLI
that emits the real event format and can simulate a sandbox escape, a read
leak, a usage limit or a hang, so the safety gates are tested without an API
key.

## License

MIT
