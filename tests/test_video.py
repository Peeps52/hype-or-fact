import subprocess

import video


def test_vtt_to_text_strips_timing_tags_and_repeats():
    vtt = ("WEBVTT\nKind: captions\n\n00:00.000 --> 00:01.000\n<c>Hello</c> there\n\n"
           "00:01.000 --> 00:02.000\nHello there\n\n2\n00:02.000 --> 00:03.000\nnew line\n")
    assert video.vtt_to_text(vtt) == "Hello there new line"


def big(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "wb") as f:
        f.truncate(11_000_000)
    return path


def test_find_model_prefers_env(tmp_path, monkeypatch):
    m = big(tmp_path / "custom.bin")
    monkeypatch.setenv("WHISPER_MODEL", str(m))
    assert video.find_model() == str(m)


def test_find_model_prefers_multilingual_and_larger(tmp_path, monkeypatch):
    monkeypatch.delenv("WHISPER_MODEL", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    for name in ("ggml-base.en.bin", "ggml-small.bin", "ggml-tiny.bin"):
        big(tmp_path / ".cache" / "whisper" / name)
    assert video.find_model().endswith("ggml-small.bin")


def test_find_model_ignores_tiny_placeholder_files(tmp_path, monkeypatch):
    monkeypatch.delenv("WHISPER_MODEL", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    p = tmp_path / ".cache" / "whisper" / "ggml-base.bin"
    p.parent.mkdir(parents=True)
    p.write_text("not a model")
    monkeypatch.setattr(video, "MODEL_GLOBS", ["~/.cache/whisper/*.bin"])
    assert video.find_model() is None


def test_restricted_post_gives_actionable_error(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(video.shutil, "which", lambda b: f"/usr/bin/{b}")
    monkeypatch.setattr(video, "run", lambda cmd, timeout=600: subprocess.CompletedProcess(
        cmd, 1, "", "ERROR: [Instagram] X: This content isn't available to everyone"))
    assert video.main(["https://instagram.com/p/X/", "--out", str(tmp_path)]) == 1
    assert "login-only" in capsys.readouterr().out


def test_missing_tools_reported(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(video.shutil, "which", lambda b: None)
    assert video.main(["https://x", "--out", str(tmp_path)]) == 2
    assert "yt-dlp" in capsys.readouterr().out
