"""Grades for `scripts/wiki_diagnose.py`, the wiki-daily job's Claude-step diagnosis (M12-14).

Every input is `constructed`: an execution file in the shape the pinned
`anthropics/claude-code-action` writes (a JSON array of Agent SDK messages:
`assistant` messages with `tool_use` blocks, `user` messages with
`tool_result` blocks, one final `result`), built in `tmp_path`. It is
constructed because the real one holds the model's whole run and is never
uploaded. The personal data in it (`jane.doe@gmail.com`, `/Users/jane`,
`Arthas-Stormrage`, `Jane#12345`) is invented; the hostile parts (workflow
commands, escape sequences, fence breaks, oversized text) are boundary tests.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
from test_check_wiki import SCRIPTS, _load, cw

PARSER = Path(__file__).resolve().parents[2] / "lab" / "core" / "tests" / "parser"
if str(PARSER) not in sys.path:
    sys.path.insert(0, str(PARSER))

from _cpu_clock import cpu_clock  # noqa: E402

wd = _load("wiki_diagnose")
SCRIPT = SCRIPTS / "wiki_diagnose.py"

WORKSPACE = "/home/runner/work/wowlab/wowlab"
TEMP = "/home/runner/work/_temp"
HOME = "/home/runner"
PERSONAL = ("jane", "Jane", "gmail", "Arthas", "Stormrage", "12345")


def _tool(n: int, name: str, content: Any) -> list[dict[str, Any]]:
    use = {"type": "tool_use", "id": f"t{n}", "name": name, "input": {"x": "secret-input"}}
    result = {"type": "tool_result", "tool_use_id": f"t{n}", "is_error": True, "content": content}
    return [
        {"type": "assistant", "message": {"role": "assistant", "content": [use]}},
        {"type": "user", "message": {"role": "user", "content": [result]}},
    ]


def _result(**fields: Any) -> dict[str, Any]:
    base = {"type": "result", "subtype": "success", "is_error": True, "num_turns": 16}
    return base | fields


def _run(tmp_path: Path, messages: Any) -> tuple[str, str]:
    file = tmp_path / "claude-execution-output.json"
    file.write_text(json.dumps(messages), encoding="utf-8")
    summary = tmp_path / "summary.md"
    argv = [str(file), "--summary", str(summary), "--workspace", WORKSPACE]
    argv += ["--temp", TEMP, "--home", HOME]
    return _capture(argv), summary.read_text(encoding="utf-8")


def _capture(argv: list[str]) -> str:
    import contextlib
    import io

    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        assert wd.main(argv) == 0
    return buf.getvalue()


def test_the_privacy_filter_is_check_wikis_own() -> None:
    assert wd.personal_kinds is cw.personal_kinds
    assert wd.normalise is cw.normalise


def test_constructed_the_result_and_counts_are_printed(tmp_path: Path) -> None:
    messages = [
        {
            "type": "system",
            "subtype": "init",
            "apiKeySource": "ANTHROPIC_API_KEY",
            "cwd": WORKSPACE,
        },
        _result(result="API Error: 400 prompt is too long"),
    ]
    log, summary = _run(tmp_path, messages)
    assert "subtype: success  is_error: true  num_turns: 16" in log
    assert "API Error: 400 prompt is too long" in log
    assert "API Error: 400 prompt is too long" in summary
    assert "subtype `success`, is_error `true`, num_turns `16`" in summary
    # Only the extract: nothing from the init message.
    assert "apiKeySource" not in log + summary and "ANTHROPIC" not in log + summary


def test_constructed_an_error_subtype_prints_its_errors(tmp_path: Path) -> None:
    messages = [_result(subtype="error_during_execution", is_error=True, errors=["boom", "bang"])]
    log, _ = _run(tmp_path, messages)
    assert "subtype: error_during_execution" in log
    assert "boom" in log and "bang" in log


def test_constructed_tool_errors_name_the_tool_and_are_bounded(tmp_path: Path) -> None:
    messages: list[Any] = []
    for n in range(15):
        messages += _tool(n, f"Tool{n}", "x" * 1000)
    messages += _tool(99, "Read", [{"type": "text", "text": "listed block"}, {"type": "image"}])
    messages.append(_result(result="r" * 5000))
    log, summary = _run(tmp_path, messages)
    assert "tool errors: 16 (first 10 shown)" in log
    assert "1. Tool0" in log and "10. Tool9" in log and "Tool10" not in log
    assert "x" * 300 + "… (700 more characters)" in log
    assert "x" * 301 not in log
    assert "r" * 1500 + "… (3500 more characters)" in log
    assert "r" * 1501 not in log
    assert "secret-input" not in log + summary  # tool inputs are never printed
    assert "tool errors: 16 (first 10 shown)" in summary


def test_constructed_a_list_content_and_a_successful_result_are_skipped_or_joined(
    tmp_path: Path,
) -> None:
    messages = _tool(1, "Read", [{"type": "text", "text": "listed block"}, {"type": "image"}])
    ok = {"type": "tool_result", "tool_use_id": "t1", "is_error": False, "content": "fine"}
    messages.append({"type": "user", "message": {"content": [ok]}})
    messages.append(_result())
    log, _ = _run(tmp_path, messages)
    assert "tool errors: 1 (first 1 shown)" in log
    assert "listed block" in log and "[image block]" in log
    assert "fine" not in log


def test_constructed_runner_paths_are_shortened_and_personal_data_withheld(
    tmp_path: Path,
) -> None:
    text = (
        f"Permission denied at {WORKSPACE}/wiki/Home.md and {TEMP}/x and {HOME}/.claude/y\n"
        "copied from /Users/jane/wow by jane.doe@gmail.com\n"
        "Arthas-Stormrage asked Jane#12345"
    )
    messages = [*_tool(1, "Bash", text), _result(result=text)]
    log, summary = _run(tmp_path, messages)
    for out in (log, summary):
        assert "<workspace>/wiki/Home.md" in out
        assert "<runner-temp>/x" in out and "~/.claude/y" in out
        assert "/home/runner" not in out
        assert "[withheld: an absolute home path]" in out
        assert "[withheld: an email address]" in out
        assert "[withheld: a Name-Realm pair]" in out
        assert "[withheld: a BattleTag]" in out
        assert "Permission denied" in out and "copied from" in out
        for secret in PERSONAL:
            assert secret not in out, secret


def test_constructed_a_line_the_word_pass_cannot_clean_is_withheld_whole(
    tmp_path: Path,
) -> None:
    # The install path spans words; no single word matches.
    text = "kept\nsee C:/Games/Somewhere/World of Warcraft/_retail_ now"
    log, _ = _run(tmp_path, [_result(result=text)])
    assert "kept" in log
    assert (
        f"[line withheld: an install path other than a documented default location {cw.PERSONAL_SUFFIX}]"
        in log
    )
    assert "Somewhere" not in log


def test_constructed_no_text_can_start_a_workflow_command(tmp_path: Path) -> None:
    text = "::add-mask::x\n::error::fake\r::warning::cr\u2028::notice::ls\u0085::debug::nel\n\x1b[31mred\u202e"
    log, _ = _run(tmp_path, [*_tool(1, "::group::tool", text), _result(result=text)])
    lines = log.splitlines()
    assert lines[0].startswith("::stop-commands::")
    token = lines[0].removeprefix("::stop-commands::")
    assert len(token) == 32
    assert lines[-1] == f"::{token}::"
    assert all(line.startswith("  ") for line in lines[1:-1])
    assert "\x1b" not in log and "\r" not in log and "\u202e" not in log
    assert "\u2028" not in log and "\u0085" not in log
    assert "[31mred" in log  # the escape character itself is dropped


def test_constructed_the_summary_fence_cannot_be_broken(tmp_path: Path) -> None:
    text = "before\n~~~~~\n# heading\n```\nafter"
    _, summary = _run(tmp_path, [_result(result=text)])
    fence = "~" * 6
    assert f"{fence}\nbefore\n~~~~~\n# heading\n```\nafter\n{fence}\n" in summary


@pytest.mark.parametrize(
    ("content", "note"),
    [
        (None, "no execution file (the Claude step stopped before writing one)"),
        ("{not json", "the execution file is not valid JSON"),
        ('{"type": "result"}', "the execution file is not a JSON array"),
        ("[]", "no result message in the execution file"),
    ],
)
def test_constructed_a_missing_or_malformed_file_is_a_note_not_a_failure(
    tmp_path: Path, content: str | None, note: str
) -> None:
    file = tmp_path / "claude-execution-output.json"
    if content is not None:
        file.write_text(content, encoding="utf-8")
    summary = tmp_path / "summary.md"
    log = _capture([str(file), "--summary", str(summary)])
    assert f"note: {note}" in log
    assert f"- note: {note}" in summary.read_text(encoding="utf-8")


def test_constructed_unexpected_result_fields_are_not_echoed(tmp_path: Path) -> None:
    messages = [_result(subtype="x::set-env name=jane", is_error="yes", num_turns=True)]
    log, _ = _run(tmp_path, messages)
    assert "subtype: (unexpected)  is_error: (unexpected)  num_turns: (unexpected)" in log
    assert "jane" not in log


# ─── round 2: surrogates, a closed block, nesting, bounded time ───────────────


def _subprocess(tmp_path: Path, messages: Any) -> tuple[subprocess.CompletedProcess[str], str]:
    """The script as the job runs it: its own process, stdout a real UTF-8 stream."""
    file = tmp_path / "claude-execution-output.json"
    file.write_text(json.dumps(messages), encoding="utf-8")
    summary = tmp_path / "summary.md"
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), str(file), "--summary", str(summary)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=env,
        check=False,
    )
    return proc, summary.read_text(encoding="utf-8") if summary.exists() else ""


@pytest.mark.parametrize(
    "half",
    ["\ud83d", "\udc00"],
    ids=["constructed-lone-high-surrogate", "constructed-lone-low-surrogate"],
)
def test_constructed_a_lone_surrogate_is_dropped_and_the_block_closes(
    tmp_path: Path, half: str
) -> None:
    # Claude Code cuts tool output by UTF-16 position: half an emoji can arrive.
    text = f"before{half}after"
    proc, summary = _subprocess(tmp_path, [*_tool(1, "Bash", text), _result(result=text)])
    assert proc.returncode == 0, proc.stderr
    lines = proc.stdout.splitlines()
    token = lines[0].removeprefix("::stop-commands::")
    assert len(token) == 32
    assert lines[-1] == f"::{token}::"
    assert proc.stdout.count("beforeafter") == 2
    assert summary.count("beforeafter") == 2


def test_constructed_a_deeply_nested_file_is_a_note_and_the_block_closes(tmp_path: Path) -> None:
    file = tmp_path / "claude-execution-output.json"
    file.write_text("[" * 200_000 + "]" * 200_000, encoding="utf-8")
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), str(file)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    lines = proc.stdout.splitlines()
    assert lines[0].startswith("::stop-commands::")
    assert lines[-1] == f"::{lines[0].removeprefix('::stop-commands::')}::"
    assert "note: the execution file is nested too deeply to read" in proc.stdout


def test_constructed_the_block_closes_even_when_printing_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    file = tmp_path / "claude-execution-output.json"
    file.write_text(json.dumps([_result(result="text")]), encoding="utf-8")
    printed: list[str] = []

    def failing_print(*args: object, **_: object) -> None:
        line = " ".join(str(a) for a in args)
        if line.startswith("  ") and len(printed) > 2:
            raise UnicodeEncodeError("utf-8", "x", 0, 1, "constructed")
        printed.append(line)

    monkeypatch.setattr("builtins.print", failing_print)
    with pytest.raises(UnicodeEncodeError):
        wd.main([str(file)])
    token = printed[0].removeprefix("::stop-commands::")
    assert printed[-1] == f"::{token}::"


# Constructed, hostile: 21,141 characters of model text shaped for the gate's
# default-root search, which ran over the whole text before every install-folder
# match. Through this script before the window: 2.1 s and 1.2 s of CPU on the
# owner's M1; after it, under 0.1 s each.
TIMED = {
    "constructed-many-starts-one-match": (" <a>/x" * 21_141)[: 21_141 - 19] + "'/World of Warcraft",
    "constructed-many-matches": ("`\\/World of Warcraft<" * 21_141)[:21_141],
}


@pytest.mark.parametrize("text", list(TIMED.values()), ids=list(TIMED))
def test_constructed_a_crafted_result_is_redacted_in_bounded_time(
    tmp_path: Path, text: str
) -> None:
    assert len(text) == 21_141
    file = tmp_path / "claude-execution-output.json"
    file.write_text(json.dumps([*_tool(1, "Bash", text), _result(result=text)]), encoding="utf-8")
    clock, name = cpu_clock()
    start = clock()
    log = _capture([str(file)])
    elapsed = clock() - start
    assert "install path other than a documented default location" in log
    assert elapsed < 1.0, f"{elapsed:.2f} s {name} time for 21,141 characters"
