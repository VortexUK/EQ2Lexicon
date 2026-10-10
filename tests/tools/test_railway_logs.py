"""scripts/tools/railway_logs.py: CLI command building, log parsing, collapsing, redaction.

Pure: ``subprocess.run`` is faked, so neither git nor the Railway CLI ever runs.
"""

from __future__ import annotations

import importlib.util
import json
import re
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]


def _load(name: str):
    key = f"_tools_{name}"
    spec = importlib.util.spec_from_file_location(key, REPO / "scripts" / "tools" / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[key] = module  # dataclasses resolve string annotations through sys.modules
    spec.loader.exec_module(module)
    return module


rl = _load("railway_logs")

NOW = datetime(2026, 10, 9, 23, 59, 0, tzinfo=UTC)


def _app(clock: str, level: str, message: str, world: str = "Wuoshi") -> str:
    """One `railway logs --json` line as production emits it (text format, stderr => level=error)."""
    text = f"2026-10-09 {clock}.123  {level:<8}  [req=a40a3fa54e454c36 user=- world={world}]  {message}"
    return json.dumps({"message": text, "timestamp": f"2026-10-09T{clock}.670250839Z", "level": "error"})


def _plain(clock: str, message: str, level: str = "error") -> str:
    return json.dumps({"message": message, "timestamp": f"2026-10-09T{clock}.000000001Z", "level": level})


# ── CLI location + command ────────────────────────────────────────────────────


def test_find_cli_prefers_the_env_var_then_path(tmp_path):
    exe = tmp_path / "railway.exe"
    exe.write_text("", encoding="utf-8")

    assert rl.find_cli({"RAILWAY_CLI": str(exe)}, which=lambda name: "/usr/bin/railway") == str(exe)
    assert rl.find_cli({}, which=lambda name: "/usr/bin/railway") == "/usr/bin/railway"


def test_find_cli_missing_is_an_environment_error_with_a_hint(tmp_path):
    with pytest.raises(rl.EnvError, match="RAILWAY_CLI"):
        rl.find_cli({}, which=lambda name: None)
    with pytest.raises(rl.EnvError, match="does not exist"):
        rl.find_cli({"RAILWAY_CLI": str(tmp_path / "nope.exe")}, which=lambda name: None)


def test_main_worktree_is_the_first_porcelain_entry():
    porcelain = (
        "worktree E:/git/EQ2Lexicon\nHEAD 0d0ea1d\nbranch refs/heads/main\n\n"
        "worktree E:/git/EQ2Lexicon-agent-tooling\nHEAD 0d0ea1d\nbranch refs/heads/chore/agent-tooling\n"
    )
    assert rl.parse_worktree_list(porcelain) == "E:/git/EQ2Lexicon"
    assert rl.parse_worktree_list("") is None


def test_build_command_uses_only_real_logs_flags():
    assert rl.build_command("railway", since="30m") == [
        "railway",
        "logs",
        "--json",
        "--lines",
        "5000",
        "--since",
        "30m",
    ]
    cmd = rl.build_command("railway", since="2026-10-09T10:00:00Z", until="10m", fetch=200, build=True, latest=True)
    assert cmd == [
        "railway",
        "logs",
        "--json",
        "--lines",
        "200",
        "--since",
        "2026-10-09T10:00:00Z",
        "--until",
        "10m",
        "--build",
        "--latest",
    ]
    assert "--since" not in rl.build_command("railway", build=True)


@pytest.mark.parametrize("bad", ["yesterday", "5m; rm -rf", "--help", "30"])
def test_build_command_rejects_a_malformed_window(bad):
    with pytest.raises(rl.EnvError, match="relative time"):
        rl.build_command("railway", since=bad)


# ── parsing ───────────────────────────────────────────────────────────────────


def test_parse_ts_handles_nanoseconds_and_bad_input():
    assert rl.parse_ts("2026-10-09T22:14:08.670250839Z") == datetime(2026, 10, 9, 22, 14, 8, 670250, tzinfo=UTC)
    assert rl.parse_ts("2026-10-09 22:14:08") == datetime(2026, 10, 9, 22, 14, 8, tzinfo=UTC)
    assert rl.parse_ts("nope") is None
    assert rl.parse_ts(None) is None


def test_app_text_line_takes_its_own_level_not_railways():
    record, structured = rl.parse_entry(json.loads(_app("22:14:06", "WARNING", "[client-throttle] over budget")))
    assert structured
    assert (record.level, record.logger, record.message) == ("WARNING", "client-throttle", "over budget")
    assert record.ts == datetime(2026, 10, 9, 22, 14, 6, 670250, tzinfo=UTC)


def test_untagged_and_audit_messages_get_a_logger_column():
    assert rl.split_tag("audit: claim_approved actor=1") == ("audit", "claim_approved actor=1")
    assert rl.split_tag("PyNaCl is not installed") == ("-", "PyNaCl is not installed")
    assert rl.split_tag("[Not-A-Tag] x") == ("-", "[Not-A-Tag] x")


def test_json_formatted_app_records_are_understood():
    inner = {"ts": "2026-10-09 22:00:00", "level": "ERROR", "logger": "backend.server.app", "message": "boom"}
    inner["exc"] = "Traceback (most recent call last):\n  File x\nValueError: bad"
    record, structured = rl.parse_entry({"message": json.dumps(inner), "timestamp": "2026-10-09T22:00:01Z"})
    assert structured
    assert (record.level, record.logger, record.message) == ("ERROR", "backend.server.app", "boom")
    assert record.extra[-1] == "ValueError: bad"

    lifted = {"message": "slow", "level": "warn", "logger": "backend.x", "timestamp": "2026-10-09T22:00:01Z"}
    record, structured = rl.parse_entry(lifted)
    assert structured and (record.level, record.logger, record.message) == ("WARNING", "backend.x", "slow")


def test_uvicorn_style_and_unstructured_lines():
    record, structured = rl.parse_entry({"message": "INFO:     Started server process [1]"})
    assert structured and (record.level, record.message) == ("INFO", "Started server process [1]")

    record, structured = rl.parse_entry({"message": "Starting Container", "level": "error"})
    assert not structured
    assert record.level == "INFO"  # railway's stderr-driven "error" is not believed

    record, _ = rl.parse_entry({"message": "ValueError: nope", "level": "info"})
    assert record.level == "ERROR"


def test_traceback_lines_fold_into_the_record_before_them():
    lines = [
        _plain("22:00:00", "Starting Container"),
        _app("22:00:05", "ERROR", "[unhandled] GET /api/x ValueError"),
        _plain("22:00:05", "Traceback (most recent call last):"),
        _plain("22:00:05", '  File "/app/backend/x.py", line 3, in f'),
        _plain("22:00:05", "ValueError: bad"),
        _app("22:00:09", "INFO", "[startup] ready"),
        _plain("22:05:00", "stray line long after"),
    ]
    records = rl.parse_lines(lines)

    assert [r.message for r in records] == [
        "Starting Container",
        "GET /api/x ValueError",
        "ready",
        "stray line long after",
    ]
    assert records[1].extra == [
        "Traceback (most recent call last):",
        '  File "/app/backend/x.py", line 3, in f',
        "ValueError: bad",
    ]
    assert records[3].extra == [] and records[2].extra == []


def test_non_json_cli_output_is_kept_as_text():
    records = rl.parse_lines(["plain text from the cli", ""])
    assert [(r.level, r.message, r.ts) for r in records] == [("INFO", "plain text from the cli", None)]


def test_build_logs_trust_the_cli_level_and_lose_ansi_and_rules():
    lines = [
        _plain("22:28:41", "UndefinedVar: Usage of undefined variable '$X' (line 18)", level="warn"),
        _plain(
            "22:29:40",
            "\x1b[35m====================\nStarting Healthcheck\n====================\n\x1b[0m",
            level="info",
        ),
        _plain("22:29:40", "", level="info"),
    ]
    records = rl.parse_lines(lines, build=True)
    assert [(r.level, r.logger, r.message) for r in records] == [
        ("WARNING", "build", "UndefinedVar: Usage of undefined variable '$X' (line 18)"),
        ("INFO", "build", "Starting Healthcheck"),
    ]


# ── normalising + collapsing ──────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ("GET /api/stats/character/Jethro took 15.5s (status 200)", "GET /api/stats/character/* took #s (status 200)"),
        (
            "GET /api/guild/Dread Army/progression took 10.7s (status 503)",
            "GET /api/guild/*/progression took #s (status 503)",
        ),
        ("GET /api/characters/search took 18.9s (status 200)", "GET /api/characters/search took #s (status 200)"),
        ("API error fetching guild for 'Bonecruncher': TimeoutError", "API error fetching guild for '*': TimeoutError"),
        (
            'cut-parse excluded: encounter=94853 title="Thera\'ta of the Jarsath"',
            'cut-parse excluded: encounter=# title="*"',
        ),
        ("couldn't get a connection after 5.00 sec, won't retry", "couldn't get a connection after # sec, won't retry"),
        ("exceeded 60 per 1 minute for tok:22cd2767ca0c4e5f", "exceeded # per # minute for tok:<hex>"),
        ("token_hash=a63b64a2 remote_ip=2001:569:7587:1800:140b:5520:8e87:d7b0", "token_hash=<hex> remote_ip=<ip>"),
        ("remote_ip=212.142.68.129 at 12:34:56", "remote_ip=<ip> at #:#:#"),
        ("request 7c9e6679-7425-40de-944b-e07fc1f90ae7 failed", "request <uuid> failed"),
        ("raid_x4 zone int4 v2 kept", "raid_x4 zone int4 v2 kept"),
    ],
)
def test_normalise_replaces_only_the_variable_parts(message, expected):
    assert rl.normalise(message) == expected


def _records(*specs: tuple[str, str, str]) -> list:
    return rl.parse_lines([_app(clock, level, message) for clock, level, message in specs])


def test_collapse_counts_repeats_and_keeps_first_and_last_time():
    records = _records(
        ("10:00:00", "WARNING", "[slow-request] GET /api/stats/character/Aa took 11.0s (status 200)"),
        ("10:05:00", "WARNING", "[slow-request] GET /api/stats/character/Bb took 30.1s (status 200)"),
        ("10:06:00", "WARNING", "[slow-request] GET /api/stats/character/Cc took 30.1s (status 404)"),
        ("10:07:00", "ERROR", "[slow-request] GET /api/stats/character/Dd took 30.1s (status 200)"),
    )
    groups = rl.collapse(records)

    assert [(g.level, g.count) for g in groups] == [("WARNING", 2), ("WARNING", 1), ("ERROR", 1)]
    assert rl.format_group(groups[0]) == (
        "10:00:00 WARNING slow-request  GET /api/stats/character/* took #s (status 200)  (x2, last 10:05:00)"
    )
    # a single occurrence keeps its real values
    assert rl.format_group(groups[1]) == (
        "10:06:00 WARNING slow-request  GET /api/stats/character/Cc took 30.1s (status 404)"
    )


def test_single_record_shows_its_traceback_tail():
    lines = [_app("22:00:05", "ERROR", "[unhandled] boom"), _plain("22:00:05", "Traceback (most recent call last):")]
    lines.append(_plain("22:00:05", "KeyError: 'x'"))
    (group,) = rl.collapse(rl.parse_lines(lines))
    assert rl.format_group(group) == "22:00:05 ERROR   unhandled  boom [+2 lines: KeyError: 'x']"


def test_merge_similar_folds_free_text_variants_past_the_threshold():
    names = ["Fizzle/1/Melee", "Bobby/1/Melee, Trust/2/Grim Wave", "Kahn/2/Graven Frenzy", "Iret/1/Melee, Error/2/Grim"]
    records = _records(
        *[
            (f"10:0{i}:00", "WARNING", f"[parses-ingest] rejected malformed payload (rows for: {n})")
            for i, n in enumerate(names)
        ],
        ("10:08:00", "WARNING", "[parses-ingest] rejected malformed payload (rows for: Fizzle/1/Melee)"),
        ("10:09:00", "WARNING", "[stats] leader query failed for kills on Wuoshi"),
        ("10:09:10", "WARNING", "[stats] leader query failed for deaths on Wuoshi"),
    )
    groups = rl.collapse(records)
    assert len(groups) == 6

    merged = rl.merge_similar(groups)
    assert [(g.logger, g.count, g.variants) for g in merged] == [
        ("parses-ingest", 5, 4),
        ("stats", 1, 1),
        ("stats", 1, 1),
    ]
    assert rl.format_group(merged[0]) == (
        "10:00:00 WARNING parses-ingest  rejected malformed payload (rows for: ...  (x5, 4 variants, last 10:08:00)"
    )
    assert rl.merge_similar(groups, min_variants=99) == groups


def test_select_applies_the_level_floor_and_grep():
    records = _records(
        ("10:00:00", "INFO", "[startup] ready"),
        ("10:00:00", "WARNING", "[census] API error on guild/"),
        ("10:00:02", "ERROR", "[unhandled] boom"),
    )
    assert [r.logger for r in rl.select(records, "WARNING")] == ["census", "unhandled"]
    assert [r.logger for r in rl.select(records, "DEBUG")] == ["startup", "census", "unhandled"]
    assert [r.logger for r in rl.select(records, "INFO", re.compile("CENSUS|ready", re.I))] == ["startup", "census"]


def test_render_caps_groups_keeping_the_most_recent():
    records = _records(
        *[(f"10:{i:02d}:00", "WARNING", f"[t{i}] distinct message number {chr(97 + i)}") for i in range(6)]
    )
    body, total = rl.render(records, max_lines=2, now=NOW)

    assert total == 6
    assert body[0] == "... 4 older groups omitted (raise --max or narrow)"
    assert [line.split()[2] for line in body[1:]] == ["t4", "t5"]


def test_render_sort_count_puts_the_biggest_group_first():
    records = _records(
        ("10:00:00", "WARNING", "[a] once"),
        ("10:01:00", "WARNING", "[b] many 1"),
        ("10:02:00", "WARNING", "[b] many 2"),
        ("10:03:00", "WARNING", "[c] once too"),
    )
    body, _ = rl.render(records, sort="count", max_lines=1, now=NOW)
    assert body == [
        "10:01:00 WARNING b  many #  (x2, last 10:02:00)",
        "... 2 smaller groups omitted (raise --max or narrow)",
    ]


def test_render_raw_prints_every_record_with_its_traceback():
    lines = [
        _app("10:00:00", "ERROR", "[x] boom 1"),
        _plain("10:00:00", "ValueError: a"),
        _app("10:00:05", "ERROR", "[x] boom 2"),
    ]
    body, total = rl.render(rl.parse_lines(lines), raw=True, now=NOW)
    assert total == 2
    assert body == ["10:00:00 ERROR   x  boom 1", "    ValueError: a", "10:00:05 ERROR   x  boom 2"]


def test_dates_are_shown_once_the_window_leaves_today():
    records = _records(("10:00:00", "WARNING", "[a] x"))
    assert rl.render(records, now=NOW)[0] == ["10:00:00 WARNING a  x"]
    tomorrow = datetime(2026, 10, 10, 0, 5, tzinfo=UTC)
    assert rl.render(records, now=tomorrow)[0] == ["10-09 10:00:00 WARNING a  x"]


def test_long_messages_are_cut():
    records = _records(("10:00:00", "WARNING", "[a] " + "word " * 200))
    (line,) = rl.render(records, width=40, now=NOW)[0]
    assert line.endswith(" ...") and len(line) < 70


@pytest.mark.parametrize(
    ("text", "leaked"),
    [
        ("Authorization: Bearer abcdef0123456789abcdef", "abcdef0123456789abcdef"),
        ("retry with password=hunter2&x=1", "hunter2"),
        ("dsn postgresql://postgres.ref:S3cretPw@aws-0.pooler.supabase.com:5432/postgres", "S3cretPw"),
        ("GET https://census.daybreakgames.com/s:myserviceid/json/get/eq2/character/", "myserviceid"),
        ("client_secret='abc123' rejected", "abc123"),
    ],
)
def test_redact_masks_credential_shaped_values(text, leaked):
    assert leaked not in rl.redact(text)


def test_redact_leaves_ordinary_fields_alone():
    text = "Invalid token presented: token_hash=a63b64a2 token_id=159 user_id=241315780858347521"
    assert rl.redact(text) == text


# ── main ──────────────────────────────────────────────────────────────────────

_PORCELAIN = "worktree /repos/main\nHEAD abc\nbranch refs/heads/main\n\nworktree /repos/wt\nHEAD abc\n"


def _fake_cli(monkeypatch, tmp_path, stdout: str, returncode: int = 0, stderr: str = ""):
    exe = tmp_path / "railway.exe"
    exe.write_text("", encoding="utf-8")
    main_dir = tmp_path / "main"
    main_dir.mkdir()
    calls: list[tuple[list[str], str]] = []

    def fake_run(cmd, **kwargs):
        calls.append((list(cmd), str(kwargs.get("cwd"))))
        if cmd[0] == "git":
            return subprocess.CompletedProcess(
                cmd, 0, stdout=_PORCELAIN.replace("/repos/main", str(main_dir)), stderr=""
            )
        assert kwargs["encoding"] == "utf-8" and kwargs["errors"] == "replace"
        return subprocess.CompletedProcess(cmd, returncode, stdout=stdout, stderr=stderr)

    monkeypatch.setenv("RAILWAY_CLI", str(exe))
    monkeypatch.setattr(rl.subprocess, "run", fake_run)
    return exe, main_dir, calls


def test_main_runs_only_railway_logs_from_the_main_worktree(monkeypatch, tmp_path, capsys):
    stdout = "\n".join(
        [
            _app("23:50:00", "INFO", "[startup] ready"),
            _app("23:51:00", "WARNING", "[census] API error on guild/: TimeoutError"),
            _app("23:52:00", "WARNING", "[census] API error on guild/: TimeoutError"),
        ]
    )
    exe, main_dir, calls = _fake_cli(monkeypatch, tmp_path, stdout)

    assert rl.main(["--since", "1h", "--width", "0"], root=tmp_path) == 0
    out = capsys.readouterr().out.splitlines()

    railway_calls = [c for c in calls if c[0][0] != "git"]
    assert railway_calls == [([str(exe), "logs", "--json", "--lines", "5000", "--since", "1h"], str(main_dir))]
    assert out[0] == "# deploy logs since 1h: 3 lines, 2 at >=WARNING, 1 groups (UTC)"
    assert re.fullmatch(
        r"(10-09 )?23:51:00 WARNING census  API error on guild/: TimeoutError  \(x2, last (10-09 )?23:52:00\)", out[1]
    )
    assert len(out) == 2


def test_main_level_grep_and_link_dir(monkeypatch, tmp_path, capsys):
    stdout = "\n".join([_app("23:50:00", "INFO", "[startup] ready"), _app("23:51:00", "INFO", "[leader] acquired")])
    _exe, _main_dir, calls = _fake_cli(monkeypatch, tmp_path, stdout)
    other = tmp_path / "other"
    other.mkdir()

    assert rl.main(["--level", "info", "--grep", "LEADER", "--link-dir", str(other)], root=tmp_path) == 0
    out = capsys.readouterr().out.splitlines()

    assert calls == [(calls[0][0], str(other))]  # no git lookup when --link-dir is given
    assert out[0] == "# deploy logs since 30m: 2 lines, 1 at >=INFO matching /LEADER/, 1 groups (UTC)"
    assert out[1].endswith("INFO    leader  acquired")


def test_main_reports_a_cli_failure_on_one_line(monkeypatch, tmp_path, capsys):
    _fake_cli(monkeypatch, tmp_path, "", returncode=1, stderr="\nNo linked project found. Run railway link\nmore\n")
    assert rl.main([], root=tmp_path) == 2
    err = capsys.readouterr().err.strip().splitlines()
    assert len(err) == 1
    assert "railway logs failed (exit 1" in err[0] and "No linked project found" in err[0]


def test_main_without_a_cli_exits_2(monkeypatch, tmp_path, capsys):
    monkeypatch.delenv("RAILWAY_CLI", raising=False)
    monkeypatch.setattr(rl.shutil, "which", lambda name: None)
    assert rl.main([], root=tmp_path) == 2
    assert capsys.readouterr().err.startswith("railway_logs: Railway CLI not found")


def test_main_rejects_a_bad_regex(monkeypatch, tmp_path, capsys):
    _fake_cli(monkeypatch, tmp_path, "")
    assert rl.main(["--grep", "("], root=tmp_path) == 2
    assert "bad --grep regex" in capsys.readouterr().err
