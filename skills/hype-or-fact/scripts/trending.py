#!/usr/bin/env python3
"""Find new, fast-rising repos in the Claude Code ecosystem.

    trending.py                        created in the last 30 days, top 25
    trending.py --days 7 --top 15
    trending.py --repo owner/name      one repo (also accepts a github.com URL)

GitHub has no trending API. The honest substitute is stars per day since
creation, over recently created repos, which is what this ranks by. Raw star
counts favour old repos; velocity favours what is rising now.

Uses the GitHub CLI (`gh`), so authentication is whatever `gh auth login`
set up. Standard library only.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone

# Where Claude Code extensions are published. Deliberately broad: the overlap
# and relevance steps narrow it.
QUERIES = [
    "topic:claude-code", "topic:claude-skills", "topic:claude-code-skills",
    "topic:agent-skills", "topic:claude-code-plugin", "topic:claude-code-hooks",
    "topic:mcp-server", "topic:model-context-protocol",
    '"claude code" skill in:name,description',
    '"claude code" plugin in:name,description',
    '"SKILL.md" in:readme',
    '"claude code" subagents in:description',
]
FIELDS = ("full_name", "html_url", "description", "stargazers_count", "created_at",
          "pushed_at", "language", "topics", "open_issues_count")


class GhError(RuntimeError):
    pass


def gh(args: list[str], retries: int = 2) -> dict:
    if not shutil.which("gh"):
        raise GhError("GitHub CLI `gh` not found. Install it: https://cli.github.com")
    for attempt in range(retries + 1):
        r = subprocess.run(["gh", "api", "-H", "Accept: application/vnd.github+json", *args],
                           capture_output=True, text=True, timeout=60)
        if r.returncode == 0:
            return json.loads(r.stdout or "{}")
        err = (r.stderr or r.stdout).strip()
        if "rate limit" in err.lower() and attempt < retries:
            time.sleep(20 * (attempt + 1))   # search API: 30 requests/minute
            continue
        if "auth" in err.lower() and "login" in err.lower():
            raise GhError("`gh` is not logged in. Run: gh auth login")
        raise GhError(err[:300] or f"gh exited {r.returncode}")
    raise GhError("rate limited")


def shape(repo: dict, now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    d = {k: repo.get(k) for k in FIELDS}
    d["license"] = (repo.get("license") or {}).get("spdx_id")
    created = datetime.fromisoformat(repo["created_at"].replace("Z", "+00:00"))
    age = max((now - created).total_seconds() / 86400, 1.0)
    d["age_days"] = round(age, 1)
    d["stars_per_day"] = round((repo.get("stargazers_count") or 0) / age, 1)
    return d


def search(days: int, min_stars: int) -> tuple[list[dict], list[str]]:
    since = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d")
    found: dict[str, dict] = {}
    errors: list[str] = []
    for q in QUERIES:
        full = f"{q} created:>{since} stars:>={min_stars} fork:false archived:false"
        try:
            res = gh(["-X", "GET", "search/repositories", "-f", f"q={full}",
                      "-f", "sort=stars", "-f", "order=desc", "-f", "per_page=50"])
        except GhError as e:
            if "not found" in str(e) or "not logged in" in str(e):
                raise
            errors.append(f"{q}: {e}")   # one failed query must not sink the scan
            continue
        for repo in res.get("items", []):
            if repo.get("fork") or repo.get("archived"):
                continue
            found.setdefault(repo["full_name"], shape(repo))
    ranked = sorted(found.values(), key=lambda r: r["stars_per_day"], reverse=True)
    return ranked, errors


def parse_repo(s: str) -> str:
    m = re.search(r"(?:github\.com[/:])?([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+?)(?:\.git)?/?(?:[#?].*)?$",
                  s.strip())
    if not m:
        raise SystemExit(f"not a GitHub repo: {s!r} (expected owner/name or a github.com URL)")
    return f"{m.group(1)}/{m.group(2)}"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--days", type=int, default=30)
    ap.add_argument("--min-stars", type=int, default=20)
    ap.add_argument("--top", type=int, default=25)
    ap.add_argument("--repo", help="owner/name or a github.com URL")
    a = ap.parse_args(argv)
    try:
        if a.repo:
            print(json.dumps(shape(gh([f"repos/{parse_repo(a.repo)}"])), indent=2))
            return 0
        ranked, errors = search(a.days, a.min_stars)
    except GhError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    print(json.dumps(ranked[: a.top], indent=2))
    print(f"\n{len(ranked)} repos found, showing {min(a.top, len(ranked))}; "
          f"{len(errors)} of {len(QUERIES)} queries failed.", file=sys.stderr)
    for e in errors:
        print(f"  failed: {e}", file=sys.stderr)
    return 1 if errors and not ranked else 0


if __name__ == "__main__":
    sys.exit(main())
