import json
import subprocess
from datetime import datetime, timezone

import pytest

import trending


def repo(name, stars, created="2026-09-20T00:00:00Z"):
    return {"full_name": name, "html_url": f"https://github.com/{name}", "description": "",
            "stargazers_count": stars, "created_at": created, "pushed_at": created,
            "language": "Python", "topics": [], "open_issues_count": 0,
            "license": {"spdx_id": "MIT"}, "fork": False, "archived": False}


@pytest.mark.parametrize("given", [
    "owner/name", "https://github.com/owner/name", "https://github.com/owner/name/",
    "https://github.com/owner/name.git", "git@github.com:owner/name.git",
    "github.com/owner/name#readme",
])
def test_parse_repo(given):
    assert trending.parse_repo(given) == "owner/name"


def test_stars_per_day_uses_age_with_one_day_floor():
    now = datetime(2026, 9, 30, tzinfo=timezone.utc)
    assert trending.shape(repo("a/b", 100, "2026-09-20T00:00:00Z"), now)["stars_per_day"] == 10.0
    assert trending.shape(repo("a/b", 50, "2026-09-30T00:00:00Z"), now)["stars_per_day"] == 50.0


def test_search_dedups_ranks_and_survives_a_failed_query(monkeypatch):
    calls = []

    def fake_gh(args, retries=2):
        calls.append(args)
        q = next(a for a in args if a.startswith("q="))
        if "subagents" in q:
            raise trending.GhError("HTTP 422")
        return {"items": [repo("x/slow", 30, "2026-09-01T00:00:00Z"),
                          repo("y/fast", 300, "2026-09-25T00:00:00Z"),
                          {**repo("z/fork", 999), "fork": True}]}

    monkeypatch.setattr(trending, "gh", fake_gh)
    ranked, errors = trending.search(30, 20)
    assert [r["full_name"] for r in ranked] == ["y/fast", "x/slow"]
    assert len(errors) == 1 and "subagents" in errors[0]
    # Query terms travel as a -f parameter, not hand-built into the URL.
    assert all("search/repositories" in c and any(a.startswith("q=") for a in c) for c in calls)


def test_missing_gh_is_a_clear_error(monkeypatch, capsys):
    monkeypatch.setattr(trending.shutil, "which", lambda _: None)
    assert trending.main(["--days", "7"]) == 2
    assert "cli.github.com" in capsys.readouterr().err


def test_rate_limit_is_retried(monkeypatch):
    outputs = iter([
        subprocess.CompletedProcess([], 1, "", "API rate limit exceeded"),
        subprocess.CompletedProcess([], 0, json.dumps({"items": []}), ""),
    ])
    monkeypatch.setattr(trending.shutil, "which", lambda _: "/usr/bin/gh")
    monkeypatch.setattr(trending.subprocess, "run", lambda *a, **k: next(outputs))
    monkeypatch.setattr(trending.time, "sleep", lambda s: None)
    assert trending.gh(["x"]) == {"items": []}
