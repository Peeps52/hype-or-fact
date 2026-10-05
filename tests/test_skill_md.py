import re
from pathlib import Path

SKILL = Path(__file__).resolve().parent.parent / "skills" / "hype-or-fact" / "SKILL.md"


def test_no_argument_placeholders_in_skill_text():
    # Claude Code replaces $0, $1, ... and $ARGUMENTS in skill content with
    # the arguments the skill was invoked with. "$0.20" rendered as a pasted
    # Instagram URL in the first real user test.
    text = SKILL.read_text()
    assert not re.search(r"\$[0-9]", text)
    assert "$ARGUMENTS" not in text


def test_frontmatter_has_name_and_description():
    fm = SKILL.read_text().split("---")[1]
    assert re.search(r"^name: hype-or-fact$", fm, re.M)
    assert re.search(r"^description: .{100,}", fm, re.M)


def test_verdict_requires_plain_description_and_claims_check():
    text = SKILL.read_text()
    assert "**What it actually does**" in text
    assert "**The claims check**" in text
    assert "Write down the creator's claims" in text
