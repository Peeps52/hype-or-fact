import json
import os
import platform

import pytest

import ab_test
from conftest import make_plugin, make_skill, write

needs_sandbox_os = pytest.mark.skipif(platform.system() not in ("Darwin", "Linux"),
                                      reason="A/B runs need macOS or Linux")


def args(cand, out, claude, *extra, kind="skill"):
    return ["--candidate", str(cand), "--kind", kind, "--task", "Do the thing",
            "--out", str(out), "--budget", "1", "--claude", claude, *extra]


@pytest.fixture(autouse=True)
def linux_sandbox_deps(monkeypatch):
    # CI may lack bubblewrap/socat; the preflight check for them is tested
    # separately. Here, pretend they exist so the flow itself is exercised.
    real = ab_test.shutil.which
    monkeypatch.setattr(ab_test.shutil, "which",
                        lambda b: f"/usr/bin/{b}" if b in ("bwrap", "socat") else real(b))


@needs_sandbox_os
def test_full_run_detects_loaded_and_used_and_blinds(home, tmp_path, fake_claude, monkeypatch):
    monkeypatch.setenv("FAKE_USE", "1")
    cand = make_skill(tmp_path / "cand")
    out = tmp_path / "out"
    assert ab_test.main(args(cand, out, fake_claude)) == 0

    [r] = json.loads((out / "results.json").read_text())
    assert r["candidate"]["candidate_loaded"] and r["candidate"]["candidate_used"]
    assert not r["baseline"]["candidate_loaded"] and not r["baseline"]["candidate_used"]
    assert r["candidate"]["files_changed"] == ["output.txt"]
    assert r["candidate"]["cost_usd"] == 0.25 and r["candidate"]["input_tokens"] == 1100

    key = json.loads((out / "key.json").read_text())["task-1"]
    cand_answer = (out / "blind" / "task-1" / key["candidate"] / "answer.md").read_text()
    assert "fancy" not in cand_answer.lower() and "[tool]" in cand_answer
    assert (out / "blind" / "task-1" / key["baseline"] / "files" / "output.txt").exists()
    assert json.loads((out / "probe.json").read_text())["inside_write"] is True


@needs_sandbox_os
def test_loaded_but_unused_is_reported_as_such(home, tmp_path, fake_claude, capsys):
    cand = make_skill(tmp_path / "cand")
    assert ab_test.main(args(cand, tmp_path / "out", fake_claude)) == 0
    [r] = json.loads((tmp_path / "out" / "results.json").read_text())
    assert r["candidate"]["candidate_loaded"] and not r["candidate"]["candidate_used"]
    assert "yes/no" in capsys.readouterr().out


@needs_sandbox_os
def test_plugin_is_loaded_via_plugin_dir(home, tmp_path, fake_claude, monkeypatch):
    monkeypatch.setenv("FAKE_USE", "1")
    cand = make_plugin(tmp_path / "cand")
    assert ab_test.main(args(cand, tmp_path / "out", fake_claude, kind="plugin")) == 0
    [r] = json.loads((tmp_path / "out" / "results.json").read_text())
    assert r["candidate"]["candidate_used"]
    assert r["candidate"]["skills_invoked"] == ["fancy-plugin:fancy"]


@needs_sandbox_os
def test_sandbox_escape_aborts_before_any_task(home, tmp_path, fake_claude, monkeypatch):
    monkeypatch.setenv("FAKE_ESCAPE", "1")
    cand = make_skill(tmp_path / "cand")
    with pytest.raises(SystemExit, match="SANDBOX PROBE FAILED"):
        ab_test.main(args(cand, tmp_path / "out", fake_claude))
    assert not (tmp_path / "out" / "task-1").exists()
    assert not list(home.glob(".hype-or-fact-probe-*"))   # probe file cleaned up


@needs_sandbox_os
def test_inconclusive_probe_aborts(home, tmp_path, fake_claude, monkeypatch):
    monkeypatch.setenv("FAKE_NO_BASH", "1")
    cand = make_skill(tmp_path / "cand")
    with pytest.raises(SystemExit, match="inconclusive"):
        ab_test.main(args(cand, tmp_path / "out", fake_claude))


def test_unsandboxed_plugin_refused_without_flag(home, tmp_path, fake_claude):
    cand = make_plugin(tmp_path / "cand", hooks=True)
    with pytest.raises(SystemExit, match="OUTSIDE the sandbox"):
        ab_test.main(args(cand, tmp_path / "out", fake_claude, kind="plugin"))
    assert not (tmp_path / "out").exists()


def test_skill_with_frontmatter_hooks_refused(home, tmp_path, fake_claude):
    cand = make_skill(tmp_path / "cand", extra_fm="hooks:\n  Stop: []\n")
    with pytest.raises(SystemExit, match="OUTSIDE the sandbox"):
        ab_test.main(args(cand, tmp_path / "out", fake_claude))


def test_budget_guard(home, tmp_path, fake_claude):
    cand = make_skill(tmp_path / "cand")
    with pytest.raises(SystemExit, match="exceeds --max-total"):
        ab_test.main(args(cand, tmp_path / "out", fake_claude, "--budget", "3",
                          "--repeat", "3", "--max-total", "10"))


def test_dry_run_calls_nothing(home, tmp_path, capsys):
    cand = make_skill(tmp_path / "cand")
    assert ab_test.main(args(cand, tmp_path / "out", "/nonexistent/claude", "--dry-run")) == 0
    assert "plan: 1 task(s)" in capsys.readouterr().out
    assert not (tmp_path / "out").exists()


def test_project_copy_excludes_secrets_and_symlinks(tmp_path):
    proj = tmp_path / "proj"
    write(proj / "src" / "app.py", "print(1)")
    write(proj / ".env", "KEY=secret")
    write(proj / "config" / "credentials.json", "{}")
    write(proj / "data.db", "x")
    write(proj / ".git" / "config", "[remote] url=https://token@github.com")
    write(tmp_path / "private.txt", "outside")
    os.symlink(tmp_path / "private.txt", proj / "link.txt")
    work = tmp_path / "work"
    ab_test.prepare(work, proj)
    got = sorted(str(p.relative_to(work)) for p in work.rglob("*") if p.is_file())
    assert got == ["src/app.py"]


def test_oversized_project_refused(home, tmp_path):
    proj = tmp_path / "proj"
    write(proj / "big.bin", "x" * (2 * 2**20))
    cand = make_skill(tmp_path / "cand")
    with pytest.raises(SystemExit, match="over 1 MB"):
        ab_test.main(args(cand, tmp_path / "out", "claude", "--project", str(proj),
                          "--max-project-mb", "1", "--dry-run"))


def test_skill_dir_cannot_escape_candidate(tmp_path):
    cand = make_skill(tmp_path / "cand")
    with pytest.raises(SystemExit, match="outside the candidate"):
        ab_test.install_skills(tmp_path / "work", cand, ["../../"])


def test_old_claude_is_refused(tmp_path):
    old = write(tmp_path / "claude", "#!/bin/sh\necho 'Usage: claude -p'\n")
    old.chmod(0o755)
    if platform.system() in ("Darwin", "Linux"):
        with pytest.raises(SystemExit, match="too old"):
            ab_test.preflight(str(old))


def test_linux_needs_bubblewrap(monkeypatch, fake_claude):
    monkeypatch.setattr(ab_test.platform, "system", lambda: "Linux")
    monkeypatch.setattr(ab_test.shutil, "which", lambda b: None if b == "bwrap" else "/x")
    with pytest.raises(SystemExit, match="bwrap"):
        ab_test.preflight(fake_claude)


def test_windows_refused(monkeypatch):
    monkeypatch.setattr(ab_test.platform, "system", lambda: "Windows")
    with pytest.raises(SystemExit, match="not available on Windows"):
        ab_test.preflight("claude")


@needs_sandbox_os
def test_timeout_is_recorded_not_crashed(tmp_path, fake_claude, monkeypatch):
    monkeypatch.setenv("FAKE_HANG", "1")
    work = tmp_path / "w"
    work.mkdir()
    res = ab_test.run([fake_claude, "-p", "x"], work, timeout=2)
    assert res["status"] == "timeout" and not res["ok"]


def test_parse_stream_ignores_garbage():
    s = ab_test.parse_stream('not json\n[1,2]\n{"type":"result","subtype":"success"}\n')
    assert s["final"]["subtype"] == "success" and s["tools_used"] == {}


def test_redact_is_case_insensitive_and_longest_first():
    assert ab_test.redact("Used Logo-Design and logo-design-pro", {"logo-design", "logo-design-pro"}) \
        == "Used [tool] and [tool]"


@needs_sandbox_os
def test_read_leak_aborts_and_canary_is_removed(home, tmp_path, fake_claude, monkeypatch):
    monkeypatch.setenv("FAKE_READ_LEAK", "1")
    cand = make_skill(tmp_path / "cand")
    with pytest.raises(SystemExit, match="denied file was readable"):
        ab_test.main(args(cand, tmp_path / "out", fake_claude))
    assert not list(home.glob(".hype-or-fact-*"))


@needs_sandbox_os
def test_usage_limit_in_probe_aborts_clearly(home, tmp_path, fake_claude, monkeypatch):
    monkeypatch.setenv("FAKE_LIMIT", "1")
    cand = make_skill(tmp_path / "cand")
    with pytest.raises(SystemExit, match="usage limit"):
        ab_test.main(args(cand, tmp_path / "out", fake_claude))


@needs_sandbox_os
def test_usage_limit_is_not_a_success(tmp_path, fake_claude, monkeypatch):
    # The real CLI returns the limit message as subtype "success"; treating
    # it as a completed run would score two empty runs as a tie.
    monkeypatch.setenv("FAKE_LIMIT", "1")
    work = tmp_path / "w"
    work.mkdir()
    res = ab_test.run([fake_claude, "-p", "x"], work, timeout=30)
    assert res["status"] == "usage_limit" and not res["ok"]


@needs_sandbox_os
def test_usage_limit_mid_run_stops_spending(home, tmp_path, fake_claude, monkeypatch, capsys):
    real_run = ab_test.run
    calls = {"n": 0}

    def run_then_limit(cmd, work, timeout):
        calls["n"] += 1
        if calls["n"] > 1:   # probe passes, then the plan runs out
            monkeypatch.setenv("FAKE_LIMIT", "1")
        return real_run(cmd, work, timeout)

    monkeypatch.setattr(ab_test, "run", run_then_limit)
    cand = make_skill(tmp_path / "cand")
    ab_test.main(args(cand, tmp_path / "out", fake_claude, "--task", "second task"))
    out = capsys.readouterr().out
    assert "stopping" in out and "usage LIMIT" in out
    assert len(json.loads((tmp_path / "out" / "results.json").read_text())) == 1


def test_sandbox_settings_deny_secret_reads():
    deny = ab_test.SANDBOX["permissions"]["deny"]
    for path in ("Read(~/.ssh/**)", "Read(~/.aws/**)", "Read(~/.claude.json)", "Read(**/.env)"):
        assert path in deny
    assert ab_test.SANDBOX["sandbox"]["allowUnsandboxedCommands"] is False
