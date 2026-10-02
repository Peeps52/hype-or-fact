#!/usr/bin/env python3
"""Run the same real tasks with and without a candidate, in throwaway sandboxes.

    ab_test.py --candidate ./clone --kind skill \\
        --task "Draw this project's data flow as an Excalidraw file" \\
        --project ~/code/myapp --out ./ab-run --budget 2

For every task (and every --repeat), two fresh copies of --project (or empty
directories) are made:

  baseline/   the user's normal setup, nothing added
  candidate/  the same, plus the candidate, loaded for this run only

Both run concurrently through `claude -p`. Nothing is installed into the
user's Claude config.

The baseline is deliberately the user's REAL setup, not a bare Claude: the
question is "what does this add to what I already have?". A diagram skill
that beats bare Claude but ties with an already-installed diagram skill adds
nothing.

Safety, enforced here rather than left to the caller:
  * Claude Code's OS sandbox is switched on for every run, and a probe run
    first proves it blocks a write outside the working copy. If the probe
    cannot prove that, nothing else runs.
  * Candidates with parts that execute OUTSIDE that sandbox (plugin hooks,
    MCP/LSP servers) are refused unless --allow-unsandboxed is passed.
  * Web tools are off unless --allow-web; the user's own MCP servers are not
    loaded (--strict-mcp-config), so a candidate cannot drive them.
  * Project copies leave out .git, secrets (.env, keys, credentials),
    databases and symlinks, and are capped in size.
  * Spend is capped per run (--budget) and in total (--max-total).

Outputs in --out:
  results.json         per run: cost, time, turns, tokens, tools used, whether
                       the candidate LOADED and whether it was USED, files changed
  blind/<pair>/A|B/    both outputs under random labels, candidate names masked
  key.json             which label is which. Judge before opening it.
  <pair>/<side>/stream.jsonl   raw event stream, for debugging

Standard library only.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import random
import re
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from audit import classify  # noqa: E402

REQUIRED_FLAGS = ("--max-budget-usd", "--output-format", "--plugin-dir", "--mcp-config",
                  "--strict-mcp-config", "--settings", "--no-session-persistence",
                  "--allowedTools", "--permission-mode")
# Read-deny rules: the OS sandbox enforces these for Bash too (verified by
# the canary in the probe). Writes are already confined to the working copy.
DENY_READ = ["Read(~/.ssh/**)", "Read(~/.aws/**)", "Read(~/.gnupg/**)", "Read(~/.config/gh/**)",
             "Read(~/.docker/**)", "Read(~/.kube/**)", "Read(~/.netrc)", "Read(~/.npmrc)",
             "Read(~/.pypirc)", "Read(~/.claude.json)", "Read(~/.claude/.credentials.json)",
             "Read(~/Library/Keychains/**)", "Read(**/.env)", "Read(**/.env.*)",
             "Read(~/.hype-or-fact-canary-*)"]
SANDBOX = {"sandbox": {"enabled": True, "autoAllowBashIfSandboxed": True,
                       "allowUnsandboxedCommands": False},
           "permissions": {"deny": DENY_READ}}
# A plan limit comes back as a *successful* result whose text is the limit
# message. Unchecked, a run that did nothing looks like a completed run.
LIMIT_RE = re.compile(r"(hit your (session|usage|weekly|daily) limit|usage limit (reached|exceeded)|"
                      r"credit balance is too low|rate[- ]limit(ed)? exceeded)", re.I)
BASE_TOOLS = ["Bash", "Read", "Write", "Edit", "Glob", "Grep", "Skill", "TodoWrite"]
WEB_TOOLS = ["WebFetch", "WebSearch"]
SECRET_PATTERNS = (".git", ".env", ".env.*", "*.pem", "*.key", "*.p12", "*.pfx",
                   "id_rsa*", "id_ed25519*", "id_ecdsa*", ".npmrc", ".pypirc", ".netrc",
                   "credentials*", "*secret*", "*.sqlite", "*.sqlite3", "*.db",
                   ".aws", ".ssh", ".gnupg", "node_modules", ".venv", "venv",
                   "__pycache__", ".next", "dist", "build", "target", ".claude")


class Abort(SystemExit):
    def __init__(self, msg: str):
        super().__init__(f"ab_test: {msg}")


# --------------------------------------------------------------------- checks

def preflight(claude: str) -> None:
    system = platform.system()
    if system not in ("Darwin", "Linux"):
        raise Abort(f"Claude Code's sandbox is not available on {system}; refusing to run "
                    "third-party code unsandboxed. Use macOS, Linux or WSL2.")
    if system == "Linux":
        missing = [b for b in ("bwrap", "socat") if not shutil.which(b)]
        if missing:
            raise Abort(f"Claude Code's Linux sandbox needs {', '.join(missing)}; install "
                        "them (e.g. apt install bubblewrap socat).")
    if not shutil.which(claude):
        raise Abort(f"`{claude}` not found on PATH. Install Claude Code first.")
    help_text = subprocess.run([claude, "--help"], capture_output=True, text=True,
                               timeout=60).stdout
    missing = [f for f in REQUIRED_FLAGS if f not in help_text]
    if missing:
        raise Abort(f"this Claude Code is too old (missing {', '.join(missing)}). "
                    "Run: claude update")


def unsandboxed_surfaces(cand: Path, kind: str) -> list[str]:
    risks = list(classify(cand)["unsandboxed"])
    if kind == "mcp" and not any("MCP server" in r for r in risks):
        risks.append("the candidate is an MCP server (runs as its own process)")
    return risks


def own_names(cand: Path, kind: str, skill_dirs: list[str], mcp_cfg: dict) -> set[str]:
    """Every name the candidate could appear under in a session."""
    info = classify(cand)
    names = {Path(d).name for d in (skill_dirs or info["skill_dirs"])}
    names |= {Path(c).stem for c in info["commands"]}
    if info["plugin_name"]:
        names.add(info["plugin_name"])
    names |= set(mcp_cfg.get("mcpServers", {}))
    return {n for n in names if n}


# ---------------------------------------------------------------- workspaces

def _ignore(src: str, names: list[str]) -> set[str]:
    skip = set(shutil.ignore_patterns(*SECRET_PATTERNS)(src, names))
    # Symlinks can point anywhere on the machine; a copy must not carry them.
    skip |= {n for n in names if os.path.islink(os.path.join(src, n))}
    return skip


def project_size(root: Path, limit: int) -> int:
    total = 0
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in _ignore(dirpath, dirnames)]
        for f in filenames:
            p = os.path.join(dirpath, f)
            if f not in _ignore(dirpath, [f]) and not os.path.islink(p):
                total += os.path.getsize(p)
                if total > limit:
                    return total
    return total


def prepare(work: Path, project: Path | None) -> None:
    if project:
        shutil.copytree(project, work, ignore=_ignore, symlinks=False)
    else:
        work.mkdir(parents=True)


def install_skills(work: Path, cand: Path, skill_dirs: list[str]) -> None:
    dest = work / ".claude" / "skills"
    dest.mkdir(parents=True, exist_ok=True)
    for d in skill_dirs or classify(cand)["skill_dirs"]:
        src = (cand / d).resolve()
        if not src.is_relative_to(cand.resolve()):
            raise Abort(f"--skill-dir {d} points outside the candidate")
        shutil.copytree(src, dest / src.name, ignore=_ignore, symlinks=False,
                        dirs_exist_ok=True)


def snapshot(root: Path) -> dict[str, float]:
    out = {}
    for p in root.rglob("*"):
        if p.is_file() and not p.is_symlink() and ".claude" not in p.relative_to(root).parts:
            out[str(p.relative_to(root))] = p.stat().st_mtime
    return out


# ---------------------------------------------------------------------- runs

def claude_cmd(claude: str, task: str, budget: float, tools: list[str], extra: list[str],
               model: str | None) -> list[str]:
    cmd = [claude, "-p", task, "--output-format", "stream-json", "--verbose",
           "--max-budget-usd", f"{budget:.2f}", "--no-session-persistence",
           "--permission-mode", "acceptEdits", "--settings", json.dumps(SANDBOX),
           "--strict-mcp-config", "--allowedTools", *tools, *extra]
    return cmd + (["--model", model] if model else [])


def parse_stream(text: str) -> dict:
    tools_used: dict[str, int] = {}
    skills_invoked: list[str] = []
    loaded: list[str] = []
    tool_results: list[str] = []
    final: dict = {}
    for line in text.splitlines():
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(ev, dict):
            continue
        if ev.get("type") == "system" and ev.get("subtype") == "init":
            # What the session could see: separates "the candidate never
            # loaded" (a harness problem) from "loaded but not chosen".
            loaded = [str(x) for x in ev.get("skills") or []]
            loaded += [str(x) for x in ev.get("slash_commands") or []]
            loaded += [str((x or {}).get("name")) for x in ev.get("plugins") or []]
            loaded += [str((x or {}).get("name")) for x in ev.get("mcp_servers") or []
                       if (x or {}).get("status", "connected") == "connected"]
        elif ev.get("type") == "assistant":
            for block in (ev.get("message") or {}).get("content") or []:
                if isinstance(block, dict) and block.get("type") == "tool_use":
                    name = str(block.get("name", "?"))
                    tools_used[name] = tools_used.get(name, 0) + 1
                    if name == "Skill":
                        skills_invoked.append(str((block.get("input") or {}).get("skill", "")))
        elif ev.get("type") == "user":
            for block in (ev.get("message") or {}).get("content") or []:
                if isinstance(block, dict) and block.get("type") == "tool_result":
                    c = block.get("content")
                    tool_results.append(c if isinstance(c, str) else json.dumps(c))
        elif ev.get("type") == "result":
            final = ev
    return {"tools_used": tools_used, "skills_invoked": skills_invoked, "loaded": loaded,
            "tool_results": tool_results, "final": final}


def run(cmd: list[str], work: Path, timeout: int) -> dict:
    before = snapshot(work)
    t0 = time.time()
    timed_out = False
    try:
        p = subprocess.run(cmd, cwd=work, capture_output=True, text=True, timeout=timeout)
        out, err, code = p.stdout, p.stderr, p.returncode
    except subprocess.TimeoutExpired as e:
        timed_out = True
        out = e.stdout.decode(errors="replace") if isinstance(e.stdout, bytes) else (e.stdout or "")
        err, code = "timeout", -1
    wall = time.time() - t0
    (work.parent / "stream.jsonl").write_text(out)
    s = parse_stream(out)
    final, usage = s["final"], s["final"].get("usage") or {}
    after = snapshot(work)
    limited = bool(LIMIT_RE.search(str(final.get("result") or ""))) and not s["tools_used"]
    status = ("timeout" if timed_out else "usage_limit" if limited
              else final.get("subtype") or f"no result (exit {code})")
    return {
        "ok": status == "success" and not final.get("is_error"),
        "status": status,
        "stream_text": out,
        "stderr_tail": "" if final else (err or "")[-800:],
        "result": final.get("result") or "",
        "cost_usd": round(float(final.get("total_cost_usd") or 0), 4),
        "duration_s": round((final.get("duration_ms") or wall * 1000) / 1000, 1),
        "turns": final.get("num_turns"),
        "output_tokens": usage.get("output_tokens"),
        "input_tokens": sum(int(usage.get(k) or 0) for k in (
            "input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens")),
        "tools_used": s["tools_used"],
        "skills_invoked": s["skills_invoked"],
        "loaded": s["loaded"],
        "files_changed": sorted(k for k, v in after.items() if before.get(k) != v),
    }


def _mentions(entry: str, names: set[str]) -> bool:
    parts = set(re.split(r"[:/]", entry.lstrip("/")))
    return bool(parts & names)


def mark_candidate(res: dict, names: set[str]) -> None:
    res["candidate_loaded"] = any(_mentions(x, names) for x in res["loaded"])
    res["candidate_used"] = any(_mentions(s, names) for s in res["skills_invoked"]) or any(
        t.startswith("mcp__") and _mentions(t.replace("mcp__", "", 1).split("__")[0], names)
        for t in res["tools_used"])


# ------------------------------------------------------------- sandbox probe

def probe_sandbox(claude: str, model: str, budget: float, timeout: int) -> dict:
    """Prove, in this environment, before any third-party code runs, that the
    sandbox (1) blocks a write outside the working copy and (2) blocks a read
    of a denied path. A canary file with a random token stands in for a
    secret; if the token appears anywhere in the run's output, reads leak.
    The probe must also show Bash actually ran (the inside write), or it
    proves nothing."""
    tag = uuid.uuid4().hex[:10]
    outside = Path.home() / f".hype-or-fact-probe-{tag}"
    canary = Path.home() / f".hype-or-fact-canary-{tag}"
    token = f"HOFCANARY{uuid.uuid4().hex}"
    canary.write_text(token)
    try:
        with tempfile.TemporaryDirectory(prefix="hof-probe-") as d:
            work = Path(d) / "work"
            work.mkdir()
            task = ("This is an automated sandbox check. Run exactly this one Bash command, "
                    f"then reply DONE:\ntouch ./inside.txt; touch {outside}; cat {canary}; "
                    "echo finished")
            res = run(claude_cmd(claude, task, budget, ["Bash"], [], model), work, timeout)
            inside_ok = (work / "inside.txt").exists()
            leaked = token in res["stream_text"] or any(
                token in p.read_text(errors="replace") for p in work.rglob("*") if p.is_file())
    finally:
        canary.unlink(missing_ok=True)
    escaped = outside.exists()
    outside.unlink(missing_ok=True)
    return {"inside_write": inside_ok, "outside_write": escaped, "read_leak": leaked,
            "cost_usd": res["cost_usd"], "status": res["status"]}


# ---------------------------------------------------------------- blind pairs

def redact(text: str, names: set[str]) -> str:
    """Answers name the tools they used ("there's no logo-design skill, so
    I..."), which unblinds the pair. Mask the candidate's names."""
    for n in sorted((n for n in names if len(n) > 2), key=len, reverse=True):
        text = re.sub(re.escape(n), "[tool]", text, flags=re.I)
    return text


def blind_copy(work: Path, res: dict, dest: Path, names: set[str]) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    (dest / "answer.md").write_text(redact(res["result"] or "(no final answer)", names))
    for rel in res["files_changed"][:200]:
        src = work / rel
        if src.is_file() and src.stat().st_size < 10_000_000:
            tgt = dest / "files" / redact(rel, names)
            tgt.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, tgt)


# ----------------------------------------------------------------------- main

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Blind with/without A/B test of a Claude Code "
                                             "skill, plugin or MCP server.")
    ap.add_argument("--candidate", type=Path, required=True, help="cloned candidate repo")
    ap.add_argument("--kind", choices=["skill", "plugin", "mcp"], required=True)
    ap.add_argument("--skill-dir", action="append", default=[],
                    help="skill folder inside the repo (repeatable; default: every SKILL.md)")
    ap.add_argument("--mcp-config", type=Path, help="JSON file with the candidate's mcpServers")
    ap.add_argument("--task", action="append", required=True, help="repeatable")
    ap.add_argument("--project", type=Path, help="project to copy into each sandbox")
    ap.add_argument("--out", type=Path, required=True, help="new directory for results")
    ap.add_argument("--budget", type=float, default=2.0, help="USD cap per single run")
    ap.add_argument("--max-total", type=float, default=10.0,
                    help="refuse if the worst-case total exceeds this (USD)")
    ap.add_argument("--repeat", type=int, default=1, help="pairs per task (noise control)")
    ap.add_argument("--timeout", type=int, default=1200, help="seconds per run")
    ap.add_argument("--model", help="model for the A/B runs (default: the user's default)")
    ap.add_argument("--probe-model", default="haiku", help="model for the sandbox probe")
    ap.add_argument("--max-project-mb", type=int, default=200)
    ap.add_argument("--allow-web", action="store_true", help="enable WebFetch/WebSearch")
    ap.add_argument("--allow-unsandboxed", action="store_true",
                    help="run candidates with hooks/MCP servers that execute outside the "
                         "sandbox. Only after reading that code.")
    ap.add_argument("--claude", default="claude", help=argparse.SUPPRESS)
    ap.add_argument("--dry-run", action="store_true", help="check and print the plan only")
    a = ap.parse_args(argv)

    cand = a.candidate.expanduser().resolve()
    if not cand.is_dir():
        raise Abort(f"candidate not found: {cand}")
    project = a.project.expanduser().resolve() if a.project else None
    if project and not project.is_dir():
        raise Abort(f"project not found: {project}")
    if a.repeat < 1 or a.budget <= 0:
        raise Abort("--repeat must be >= 1 and --budget > 0")

    mcp_cfg: dict = {}
    if a.kind == "mcp":
        if not a.mcp_config:
            raise Abort("--kind mcp needs --mcp-config (a JSON file with mcpServers)")
        mcp_cfg = json.loads(a.mcp_config.read_text())
        if not mcp_cfg.get("mcpServers"):
            raise Abort(f"{a.mcp_config} has no mcpServers")
    if a.kind == "skill" and not (a.skill_dir or classify(cand)["skill_dirs"]):
        raise Abort("no SKILL.md found in the candidate")
    if a.kind == "plugin" and not (cand / ".claude-plugin" / "plugin.json").exists():
        raise Abort("no .claude-plugin/plugin.json; is this a plugin? Try --kind skill")

    risks = unsandboxed_surfaces(cand, a.kind)
    if a.kind == "skill":
        # A skill copied into .claude/skills cannot bring plugin hooks or
        # servers with it; only skill-frontmatter hooks travel.
        risks = [r for r in risks if r.startswith("hooks in frontmatter")]
    if risks and not a.allow_unsandboxed:
        raise Abort("the candidate has parts Claude Code runs OUTSIDE the sandbox:\n  - "
                    + "\n  - ".join(risks)
                    + "\nRead that code, then rerun with --allow-unsandboxed if you accept it.")

    project_bytes = project_size(project, a.max_project_mb * 2**20) if project else 0
    if project_bytes > a.max_project_mb * 2**20:
        raise Abort(f"project is over {a.max_project_mb} MB after exclusions; "
                    "point --project at a smaller copy or raise --max-project-mb")

    names = own_names(cand, a.kind, a.skill_dir, mcp_cfg)
    pairs = [(i, r) for i in range(1, len(a.task) + 1) for r in range(1, a.repeat + 1)]
    worst = len(pairs) * 2 * a.budget + 0.5
    plan = (f"plan: {len(a.task)} task(s) x {a.repeat} repeat(s) = {len(pairs)} pair(s), "
            f"{len(pairs) * 2} runs, cap ${a.budget:.2f}/run, worst case ${worst:.2f} "
            f"(incl. sandbox probe)\ncandidate: {cand.name} ({a.kind}), names: "
            f"{', '.join(sorted(names)) or '?'}\nproject: {project or '(empty dir)'}"
            f"{f' ({project_bytes / 2**20:.1f} MB copied per run)' if project else ''}\n"
            f"web tools: {'on' if a.allow_web else 'off'} · "
            f"unsandboxed parts: {', '.join(risks) or 'none'}")
    print(plan)
    if worst > a.max_total:
        raise Abort(f"worst case ${worst:.2f} exceeds --max-total ${a.max_total:.2f}")
    if a.dry_run:
        return 0

    preflight(a.claude)
    if a.out.exists():
        raise Abort(f"{a.out} already exists; use a fresh --out")
    a.out.mkdir(parents=True)

    probe = probe_sandbox(a.claude, a.probe_model, 1.0, 300)
    (a.out / "probe.json").write_text(json.dumps(probe, indent=2))
    if probe["status"] == "usage_limit":
        raise Abort("your Claude plan's usage limit is reached; nothing was run. "
                    "Try again after it resets.")
    if probe["outside_write"]:
        raise Abort("SANDBOX PROBE FAILED: a write outside the working copy succeeded. "
                    "Nothing was run. Check your Claude Code sandbox settings.")
    if probe["read_leak"]:
        raise Abort("SANDBOX PROBE FAILED: a denied file was readable from Bash. "
                    "Nothing was run.")
    if not probe["inside_write"]:
        raise Abort(f"sandbox probe inconclusive (Bash did not run; status: "
                    f"{probe['status']}). Nothing was run.")
    print(f"sandbox probe passed (${probe['cost_usd']:.2f})")

    tools = BASE_TOOLS + (WEB_TOOLS if a.allow_web else [])
    cand_tools, cand_extra = list(tools), []
    if a.kind == "plugin":
        cand_extra = ["--plugin-dir", str(cand)]
    elif a.kind == "mcp":
        cand_extra = ["--mcp-config", str(a.mcp_config.resolve())]
        cand_tools += [f"mcp__{n}" for n in mcp_cfg["mcpServers"]]

    results, key = [], {}
    for i, r in pairs:
        pid = f"task-{i}" + (f"-run-{r}" if a.repeat > 1 else "")
        task = a.task[i - 1]
        base_w, cand_w = a.out / pid / "baseline" / "work", a.out / pid / "candidate" / "work"
        prepare(base_w, project)
        prepare(cand_w, project)
        if a.kind == "skill":
            install_skills(cand_w, cand, a.skill_dir)
        with ThreadPoolExecutor(max_workers=2) as ex:
            fb = ex.submit(run, claude_cmd(a.claude, task, a.budget, tools, [], a.model),
                           base_w, a.timeout)
            fc = ex.submit(run, claude_cmd(a.claude, task, a.budget, cand_tools, cand_extra,
                                           a.model), cand_w, a.timeout)
            rb, rc = fb.result(), fc.result()
        for res in (rb, rc):
            res.pop("stream_text", None)
        mark_candidate(rc, names)
        mark_candidate(rb, names)   # should be False; True means contamination
        labels = ["A", "B"]
        random.shuffle(labels)
        key[pid] = {"baseline": labels[0], "candidate": labels[1]}
        bdir = a.out / "blind" / pid
        blind_copy(base_w, rb, bdir / labels[0], names)
        blind_copy(cand_w, rc, bdir / labels[1], names)
        (bdir / "task.md").write_text(task)
        results.append({"pair": pid, "task": task, "baseline": rb, "candidate": rc})
        print(f"{pid}: done (${rb['cost_usd'] + rc['cost_usd']:.2f})", flush=True)
        if "usage_limit" in (rb["status"], rc["status"]):
            # Stop spending: every further run would return the same message,
            # and a half-limited pair is not a comparison.
            print("usage limit reached; stopping. Completed pairs are saved.", flush=True)
            break

    (a.out / "results.json").write_text(json.dumps(results, indent=2))
    (a.out / "key.json").write_text(json.dumps(key, indent=2))

    spent = probe["cost_usd"] + sum(x[s]["cost_usd"] for x in results
                                    for s in ("baseline", "candidate"))
    print(f"\n{'pair':<14}{'side':<11}{'ok':<4}{'cost':>8}{'time':>8}{'turns':>6}"
          f"{'out tok':>9}  loaded/used")
    for x in results:
        for side in ("baseline", "candidate"):
            y = x[side]
            lu = "" if side == "baseline" else (
                f"{'yes' if y['candidate_loaded'] else 'NOT LOADED'}/"
                f"{'yes' if y['candidate_used'] else 'no'}")
            print(f"{x['pair']:<14}{side:<11}{'y' if y['ok'] else 'n':<4}"
                  f"${y['cost_usd']:>7.2f}{y['duration_s']:>7.0f}s{y['turns'] or 0:>6}"
                  f"{y['output_tokens'] or 0:>9}  {lu}")
    warnings = []
    if any(not x["candidate"]["candidate_loaded"] for x in results):
        warnings.append("the candidate did NOT LOAD in some runs: a harness problem, not a "
                        "verdict. Read <pair>/candidate/stream.jsonl before judging.")
    if any(x["baseline"]["candidate_loaded"] for x in results):
        warnings.append("the candidate was visible in a BASELINE run (it is probably "
                        "already installed); the comparison is contaminated.")
    if any("usage_limit" in (x["baseline"]["status"], x["candidate"]["status"]) for x in results):
        warnings.append("a run hit the Claude plan's usage LIMIT and did no work. Discard "
                        "that pair; it is not a tie.")
    if any(not x[s]["ok"] for x in results for s in ("baseline", "candidate")):
        warnings.append("some runs did not finish (budget, timeout or error); see "
                        "results.json 'status'.")
    for w in warnings:
        print(f"\nWARNING: {w}")
    print(f"\ntotal spent: ${spent:.2f}\nblind pairs: {a.out / 'blind'}  "
          f"(judge the files first; open key.json last)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
