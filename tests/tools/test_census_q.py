"""scripts/tools/census_q.py: URL building, service-id masking, compact output, retry.

Pure: the HTTP getter is injected, so nothing here touches the network.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]


def _load(name: str):
    key = f"_tools_{name}"
    spec = importlib.util.spec_from_file_location(key, REPO / "scripts" / "tools" / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[key] = module
    spec.loader.exec_module(module)
    return module


cq = _load("census_q")

SECRET = "s3cr3tServiceId"


# ── request building ──────────────────────────────────────────────────────────


def test_parse_filters_splits_at_the_first_equals():
    assert cq.parse_filters(["name.first=Foo", "type.level=>50", "displayname=^Mark of", "c:case=false"]) == [
        ("name.first", "Foo"),
        ("type.level", ">50"),
        ("displayname", "^Mark of"),
        ("c:case", "false"),
    ]


@pytest.mark.parametrize("bad", ["name.first", "=value"])
def test_parse_filters_rejects_non_pairs(bad):
    with pytest.raises(cq.UsageError):
        cq.parse_filters([bad])


def test_build_url_matches_the_census_shape():
    url = cq.build_url(
        "example",
        "character",
        [("name.first", "Menludiir"), ("locationdata.world", "Wuoshi")],
        show="name.first, type.level,type.class",
        limit=5,
    )
    assert url == (
        "https://census.daybreakgames.com/s:example/json/get/eq2/character/"
        "?name.first=Menludiir&locationdata.world=Wuoshi&c:show=name.first,type.level,type.class&c:limit=5"
    )


def test_build_url_keeps_operators_and_encodes_the_rest():
    url = cq.build_url(
        "example",
        "guild",
        [("name", "^Dread Army"), ("level", ">50"), ("note", "a&b=c#d")],
        sort="level:-1",
        resolve="members(name,type)",
    )
    query = url.split("?", 1)[1]
    assert query == "name=^Dread%20Army&level=>50&note=a%26b%3Dc%23d&c:resolve=members(name,type)&c:sort=level:-1"


def test_build_url_count_verb_and_bare_collection():
    assert cq.build_url("example", "item", verb="count").endswith("/json/count/eq2/item/")


@pytest.mark.parametrize("bad", ["character/../x", "Character", "", "char?x=1"])
def test_build_url_rejects_a_bad_collection(bad):
    with pytest.raises(cq.UsageError):
        cq.build_url("example", bad)


def test_mask_hides_the_service_id_everywhere():
    url = cq.build_url(SECRET, "character", [("name.first", "X")], show="name")
    assert SECRET in url
    masked = cq.mask(f"GET {url} failed; retried {url}")
    assert SECRET not in masked
    assert masked.count("https://census.daybreakgames.com/s:***/json/get/eq2/character/") == 2


# ── service id ────────────────────────────────────────────────────────────────


def test_service_id_comes_from_env_then_each_dotenv_then_default(tmp_path):
    worktree_env = tmp_path / "wt" / ".env"
    main_env = tmp_path / "main" / ".env"
    main_env.parent.mkdir()
    main_env.write_text("DISCORD_TOKEN=x\nCENSUS_SERVICE_ID=from_main\n", encoding="utf-8")
    files = [worktree_env, main_env]  # the worktree has no .env at all

    assert cq.resolve_service_id({}, files) == ("from_main", str(main_env))
    assert cq.resolve_service_id({"CENSUS_SERVICE_ID": "from_env"}, files) == ("from_env", "environment")

    worktree_env.parent.mkdir()
    worktree_env.write_text('CENSUS_SERVICE_ID="from_worktree"\n', encoding="utf-8")
    assert cq.resolve_service_id({}, files)[0] == "from_worktree"
    assert cq.resolve_service_id({}, [tmp_path / "missing.env"]) == ("example", "default")


# ── fetching ──────────────────────────────────────────────────────────────────


class _Getter:
    """Scripted stand-in for the HTTP getter: each item is a (status, body) pair or an exception."""

    def __init__(self, *script):
        self.script = list(script)
        self.calls = 0

    def __call__(self, url: str, timeout: float):
        self.calls += 1
        step = self.script.pop(0)
        if isinstance(step, Exception):
            raise step
        return step


_OK = (200, json.dumps({"character_list": [{"id": 1}], "returned": 1}))


def test_fetch_retries_once_after_a_timeout():
    get, pauses = _Getter(TimeoutError("ReadTimeout"), _OK), []
    payload = cq.fetch_json("u", get=get, sleep=pauses.append)
    assert payload["returned"] == 1
    assert get.calls == 2 and pauses == [cq.RETRY_PAUSE_S]


def test_fetch_retries_once_after_a_5xx_then_gives_up():
    get, pauses = _Getter((503, "busy"), (502, "still busy")), []
    with pytest.raises(cq.CensusError, match=r"HTTP 502 \(after one retry\)"):
        cq.fetch_json("u", get=get, sleep=pauses.append)
    assert get.calls == 2 and len(pauses) == 1


def test_fetch_does_not_retry_client_errors_or_census_errors():
    get = _Getter((404, "nope"))
    with pytest.raises(cq.CensusError, match="HTTP 404"):
        cq.fetch_json("u", get=get, sleep=lambda s: None)
    assert get.calls == 1

    get = _Getter((200, json.dumps({"error": "No data found."})))
    with pytest.raises(cq.CensusError, match="Census error: No data found"):
        cq.fetch_json("u", get=get, sleep=lambda s: None)
    assert get.calls == 1

    with pytest.raises(cq.CensusError, match="not JSON"):
        cq.fetch_json("u", get=_Getter((200, "<html>")), sleep=lambda s: None)
    with pytest.raises(cq.CensusError, match="network error"):
        cq.fetch_json("u", get=_Getter(ConnectionError("ConnectError")), sleep=lambda s: None)


def test_census_error_text_never_carries_the_service_id():
    body = json.dumps({"error": f"Bad request: /s:{SECRET}/json/get/eq2/x"})
    with pytest.raises(cq.CensusError) as excinfo:
        cq.fetch_json("u", get=_Getter((200, body)), sleep=lambda s: None)
    assert SECRET not in str(excinfo.value)


# ── compact output ────────────────────────────────────────────────────────────


def test_compact_cuts_long_lists_and_strings_at_every_depth():
    doc = {
        "name": "Halcyon",
        "member_list": [{"dbid": i, "tags": list(range(8))} for i in range(7)],
        "motd": "x" * 250,
        "short": [1, 2, 3, 4, 5],
    }
    out = cq.compact(doc)

    assert out["name"] == "Halcyon"
    assert len(out["member_list"]) == 6 and out["member_list"][-1] == "... (+2 more)"
    assert out["member_list"][0]["tags"] == [0, 1, 2, 3, 4, "... (+3 more)"]
    assert out["motd"] == "x" * 200 + "... (+50 chars)"
    assert out["short"] == [1, 2, 3, 4, 5]


def test_render_puts_returned_first_and_one_compact_line_per_row():
    url = cq.build_url(SECRET, "character", [("name.first", "X")], show="name.first", limit=5)
    payload = {"character_list": [{"name": {"first": "X"}, "spell_list": list(range(9))}, {"id": 2}], "returned": 2}
    lines, rows = cq.render(payload, "character", url)

    assert rows == 2
    assert lines[0] == (
        "returned=2  GET https://census.daybreakgames.com/s:***/json/get/eq2/character/"
        "?name.first=X&c:show=name.first&c:limit=5"
    )
    assert lines[1] == '{"name":{"first":"X"},"spell_list":[0,1,2,3,4,"... (+4 more)"]}'
    assert lines[2] == '{"id":2}'

    raw_lines, _ = cq.render(payload, "character", url, raw=True)
    assert raw_lines[1] == '{"name":{"first":"X"},"spell_list":[0,1,2,3,4,5,6,7,8]}'


def test_render_empty_result_and_count():
    lines, rows = cq.render({"character_list": [], "returned": 0, "limit": 5, "min_ts": 0.0}, "character", "u")
    assert (lines, rows) == (["returned=0  GET u"], 0)
    assert cq.render({"count": 12355}, "character", "u") == (["count=12355  GET u"], 12355)


def test_cap_text_truncates_and_says_so():
    assert cq.cap_text("abc", 10) == "abc"
    assert cq.cap_text("abc", 0) == "abc"
    capped = cq.cap_text("x" * 5000, 4000)
    assert capped.startswith("x" * 4000 + "\n... truncated at 4000 chars (+1000 more)")


# ── main ──────────────────────────────────────────────────────────────────────


@pytest.fixture
def env(monkeypatch):
    monkeypatch.setenv("CENSUS_SERVICE_ID", SECRET)
    monkeypatch.setattr(cq, "main_worktree", lambda root: None)  # no git subprocess in tests


def test_main_prints_compact_rows_and_never_the_service_id(env, tmp_path, capsys):
    seen = []

    def get(url: str, timeout: float):
        seen.append(url)
        return 200, json.dumps({"character_list": [{"type": {"level": 80, "class": "Templar"}}], "returned": 1})

    argv = ["character", "name.first=Menludiir", "locationdata.world=Wuoshi", "--show", "type.level,type.class"]
    assert cq.main(argv, root=tmp_path, get=get) == 0
    captured = capsys.readouterr()

    assert seen == [
        f"https://census.daybreakgames.com/s:{SECRET}/json/get/eq2/character/"
        "?name.first=Menludiir&locationdata.world=Wuoshi&c:show=type.level,type.class&c:limit=5"
    ]
    assert SECRET not in captured.out + captured.err
    assert captured.out.splitlines() == [
        "returned=1  GET https://census.daybreakgames.com/s:***/json/get/eq2/character/"
        "?name.first=Menludiir&locationdata.world=Wuoshi&c:show=type.level,type.class&c:limit=5",
        '{"type":{"level":80,"class":"Templar"}}',
    ]


def test_main_requires_show_unless_raw_or_count(env, tmp_path, capsys):
    def never(url: str, timeout: float):
        raise AssertionError("must not query without a projection")

    assert cq.main(["character", "name.first=X"], root=tmp_path, get=never) == 2
    assert "--show a,b,c is required" in capsys.readouterr().err

    ok = _Getter((200, json.dumps({"character_list": [{"id": 1}], "returned": 1})), (200, json.dumps({"count": 3})))
    assert cq.main(["character", "name.first=X", "--raw"], root=tmp_path, get=ok) == 0
    assert cq.main(["character", "name.first=X", "--count"], root=tmp_path, get=ok) == 0
    assert capsys.readouterr().out.splitlines()[-1].startswith("count=3  GET ")


def test_main_caps_total_output(env, tmp_path, capsys):
    rows = [{"description": "y" * 150, "id": i} for i in range(5)]
    get = _Getter((200, json.dumps({"item_list": rows, "returned": 5})))
    assert (
        cq.main(["item", "displayname=^Mark", "--show", "description", "--max-chars", "300"], root=tmp_path, get=get)
        == 0
    )
    out = capsys.readouterr().out
    assert "... truncated at 300 chars" in out
    assert len(out) < 420


def test_main_no_rows_exits_1(env, tmp_path, capsys):
    get = _Getter((200, json.dumps({"character_list": [], "returned": 0})))
    assert cq.main(["character", "name.first=Nobody", "--show", "name"], root=tmp_path, get=get) == 1
    assert capsys.readouterr().out.startswith("returned=0  GET ")


def test_main_error_is_one_masked_line(env, tmp_path, capsys, monkeypatch):
    monkeypatch.setattr(cq.time, "sleep", lambda s: None)
    get = _Getter(TimeoutError("ReadTimeout"), TimeoutError("ReadTimeout"))
    assert cq.main(["character", "name.first=X", "--show", "name"], root=tmp_path, get=get) == 1
    captured = capsys.readouterr()

    assert captured.out == ""
    err = captured.err.strip().splitlines()
    assert len(err) == 1
    assert err[0].startswith(
        "census_q: timed out after 15s (after one retry)  [GET https://census.daybreakgames.com/s:***/"
    )
    assert SECRET not in err[0]


def test_main_usage_errors_exit_2(env, tmp_path, capsys):
    assert cq.main(["character", "notapair", "--show", "name"], root=tmp_path) == 2
    assert cq.main(["Bad/Collection", "--show", "name"], root=tmp_path) == 2
    assert cq.main(["character", "--show", "name", "--limit", "0"], root=tmp_path) == 2
    assert capsys.readouterr().err.count("census_q:") == 3


def test_main_warns_when_falling_back_to_the_public_default(tmp_path, capsys, monkeypatch):
    monkeypatch.delenv("CENSUS_SERVICE_ID", raising=False)
    monkeypatch.setattr(cq, "main_worktree", lambda root: None)
    get = _Getter(_OK)
    assert cq.main(["character", "name.first=X", "--show", "id"], root=tmp_path, get=get) == 0
    assert "using the rate-limited public default" in capsys.readouterr().err
