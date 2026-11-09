"""The transcript sanitizer runs BEFORE the privacy scan (rulings R18 and R19).

- It rewrites CLI metadata only, through rules of the form (tool, event-type selector, EXACT
  scalar leaf path). Unknown event types, unlisted leaves and every container stay verbatim,
  so a privacy problem in authored content stays visible to the scanner.
- The one exception is a short list of literals the harness itself created (the run's
  workspace path and the temp root): those are replaced everywhere, authored content included.

Long numbers are built from pieces so this file stays scannable by the repository's own
privacy scanner.
"""

import json
import re
from pathlib import Path

import pytest

import sanitize
from privacy_check import scan_text
from sanitize import (
    CLAUDE_METADATA,
    OPENCODE_METADATA,
    iter_metadata,
    path_variants,
    sanitize_transcript,
)

HOME = "/Users/fakeuser"
TMPROOT = "/var/folders/ab/cdef/T"
WS = f"{TMPROOT}/taxeval-abc"
HARNESS = [(WS, "<WORKSPACE>"), (TMPROOT, "<TMP>")]
MS_A, MS_B, MS_C = 1_793_151_036_532, 1_793_151_035_865, 1_793_150_943_809
SEC_B = 1_793_159_400
THIRTEEN = "".join(["12345", "67890", "123"])
REAL_UUID = "-".join(["12345678", "1234", "1234", "1234", "".join(["12345", "6789012"])])
LONG_COST = float("0." + "33392440" + "0000" + "00007")
LONG_FLOAT = float("0." + "12345" + "6789123")
JANE_ID = "req_jane12345678"


def run(events, tool="claude", harness=()):
    items = events if isinstance(events, list) else [events]
    text = "\n".join(json.dumps(e) for e in items)
    return sanitize_transcript(text, tool=tool, home_dirs=[HOME], harness_paths=list(harness))


def clean(events, tool="claude", harness=()):
    return [json.loads(line) for line in run(events, tool, harness).splitlines()]


def assistant_with(block, **top):
    return {"type": "assistant", "message": {"id": "msg_01AbCdEfGhIj", "role": "assistant", "content": [block]}, **top}


def tool_result_event(content, **top):
    return {"type": "user", "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "toolu_01AbCdEfGh", "content": content}]}, **top}


def oc_tool(state, **part):
    return {"type": "tool_use", "timestamp": MS_A, "sessionID": "ses_e7fe011ddffe5fM4Y3O",
            "part": {"type": "tool", "id": "call_8a7c83d750c349a0", "tool": "read", "state": state, **part}}


def oc_text(text):
    return {"type": "text", "timestamp": MS_A, "sessionID": "ses_e7fe011ddffe5fM4Y3O",
            "part": {"id": "prt_18020bff8001bYUNJ", "type": "text", "text": text, "time": {"start": MS_B, "end": MS_A}}}


def init_event(**extra):
    return {"type": "system", "subtype": "init", "session_id": REAL_UUID, "uuid": REAL_UUID, "model": "m", **extra}


def kinds(text, deny):
    return sorted(f.kind for f in scan_text(text, deny))


# ================================================================ R19(a): event-type gating


def test_unknown_claude_event_type_with_metadata_named_keys_is_verbatim():
    event = {"type": "custom_tool_output", "timestamp": MS_A, "session_id": JANE_ID, "cwd": f"{HOME}/private"}
    text = json.dumps(event)
    assert kinds(text, ["jane", HOME]) == ["denylist", "denylist", "digits"]
    assert run(event) == text
    assert kinds(run(event), ["jane", HOME]) == ["denylist", "denylist", "digits"]


def test_unknown_opencode_event_type_with_metadata_named_keys_is_verbatim():
    event = {"type": "custom_tool_output", "timestamp": MS_A, "sessionID": JANE_ID, "part": {"id": JANE_ID, "type": "custom", "time": {"start": MS_A}}}
    assert run(event, "opencode") == json.dumps(event)
    assert kinds(run(event, "opencode"), ["jane"]) == ["denylist"] * 2 + ["digits"] * 2


def test_known_type_with_a_leaf_that_belongs_to_another_type_is_verbatim():
    cases = [
        {"type": "assistant", "cwd": f"{HOME}/x"},  # cwd is an init field
        {"type": "user", "total_cost_usd": LONG_COST},  # cost is a result field
        {"type": "result", "subtype": "success", "request_id": JANE_ID},  # request_id is an assistant field
        {"type": "system", "subtype": "thinking_tokens", "cwd": f"{HOME}/x"},  # only init has cwd
        {"type": "system", "subtype": "init", "request_id": JANE_ID, "message": {"id": JANE_ID}},
        {"type": "rate_limit_event", "timestamp": MS_A, "message": {"id": JANE_ID}},
        {"type": "assistant", "message": {"usage": {"c": LONG_COST}}},  # usage is only a result field
    ]
    for event in cases:
        assert run(event) == json.dumps(event), event


def test_opencode_type_and_part_type_must_both_match():
    cases = [
        {"type": "text", "timestamp": MS_A, "part": {"type": "tool", "state": {"time": {"start": MS_A}}}},
        {"type": "tool_use", "timestamp": MS_A, "part": {"type": "text", "time": {"start": MS_B, "end": MS_A}}},
        {"type": "step_start", "timestamp": MS_A, "part": {"type": "step-start", "time": {"start": MS_B}, "cost": LONG_COST}},
        {"type": "step_finish", "part": {"type": "step-start", "cost": LONG_COST}},
        {"type": "text", "part": {"type": "text", "partID": JANE_ID}},  # partID is a tool part field
    ]
    for event in cases:
        # `part` leaves that belong to another (type, part.type) pair never change
        assert json.loads(run(event, "opencode")).get("part") == event["part"], event
    assert run(cases[0], "opencode") == json.dumps(cases[0])  # no rule for (text, tool) at all
    assert run(cases[3], "opencode") == json.dumps(cases[3])  # none for (step_finish, step-start) either


def test_a_claude_assistant_event_with_session_id_inside_tool_input_keeps_it():
    nested = {"session_id": JANE_ID, "uuid": REAL_UUID, "timestamp": MS_A, "cwd": f"{HOME}/x", "request_id": JANE_ID}
    block = {"type": "tool_use", "id": "toolu_01AbCdEfGh", "name": "Write", "input": nested}
    event = assistant_with(block, session_id=REAL_UUID, uuid=REAL_UUID, request_id="req_011CgTn3FpiYwA4q")
    out = clean(event)[0]
    assert out["message"]["content"][0]["input"] == nested
    assert re.fullmatch(r"00000000-0000-0000-0000-a\d{11}", out["session_id"])  # the real metadata still normalized
    assert re.fullmatch(r"req_ID_\d+", out["request_id"])
    assert kinds(json.dumps(out), ["jane", HOME]).count("denylist") == 3  # two ids and the home path, all in tool input


@pytest.mark.parametrize("container", ["skills", "slash_commands", "tools", "mcp_servers", "agents"])
def test_names_inside_init_containers_are_never_rewritten(container):
    as_list = init_event(**{container: [JANE_ID, {"name": JANE_ID}]})
    as_dict = init_event(**{container: {JANE_ID: 1, "ok": JANE_ID}})
    for event in (as_list, as_dict):
        out = run(event)
        assert JANE_ID in out and kinds(out, ["jane"]) != []
        assert json.loads(out)[container] == event[container]


def test_container_at_a_scalar_rule_path_is_left_alone():
    event = init_event(cwd={"p": f"{HOME}/x", "i": JANE_ID}, original_cwd=[f"{HOME}/y"])
    event["session_id"] = {"x": JANE_ID}
    event["uuid"] = [JANE_ID]
    assert run(event) == json.dumps(event)


def test_plugin_paths_get_home_replacement_only_never_id_replacement():
    event = init_event(plugins=[
        {"name": JANE_ID, "path": f"{HOME}/.claude/plugins/p", "source": JANE_ID},
        {"name": "q", "path": f"{HOME}/.claude/{JANE_ID}", "source": "q@x"},
    ])
    plugins = clean(event)[0]["plugins"]
    assert plugins[0] == {"name": JANE_ID, "path": "<HOME>/.claude/plugins/p", "source": JANE_ID}
    assert plugins[1]["path"] == f"<HOME>/.claude/{JANE_ID}"


def test_result_usage_rounds_numeric_leaves_only():
    event = {"type": "result", "subtype": "success", "result": "x", "total_cost_usd": LONG_COST,
             "usage": {"cost": LONG_COST, "note": JANE_ID, "iterations": [{"c": LONG_COST}], "nested": {"c": LONG_COST}},
             "modelUsage": {"claude-sonnet-5-5": {"costUSD": LONG_COST, "label": JANE_ID, "other": LONG_COST}},
             "model_usage_since_last_result": {"m": {"costUSD": LONG_COST}}}
    out = clean(event)[0]
    assert out["total_cost_usd"] == 0.333924 and out["usage"]["cost"] == 0.333924
    assert out["usage"]["note"] == JANE_ID
    assert out["usage"]["iterations"] == [{"c": LONG_COST}] and out["usage"]["nested"] == {"c": LONG_COST}
    assert out["modelUsage"]["claude-sonnet-5-5"] == {"costUSD": 0.333924, "label": JANE_ID, "other": LONG_COST}
    assert out["model_usage_since_last_result"]["m"]["costUSD"] == 0.333924


# ================================================================ authored content is never rewritten (R18, kept)


def test_epoch_looking_values_in_tool_inputs_stay_flagged():
    block = {"type": "tool_use", "id": "toolu_01AbCdEfGh", "name": "Write", "input": {"start": MS_A, "path": "a.md"}}
    out = clean(assistant_with(block))[0]
    assert out["message"]["content"][0]["input"] == {"start": MS_A, "path": "a.md"}
    assert kinds(json.dumps(out), []) == ["digits"]
    state = {"status": "completed", "input": {"start": MS_A, "timestamp": MS_A}, "time": {"start": MS_B, "end": MS_A}}
    out = clean(oc_tool(state), "opencode")[0]
    assert out["part"]["state"]["input"] == {"start": MS_A, "timestamp": MS_A}
    assert out["part"]["state"]["time"] == {"start": 0, "end": 0}


def test_timestamp_strings_in_tool_results_stay_flagged():
    for content in ({"timestamp": str(MS_A)}, json.dumps({"timestamp": str(MS_A)}), [{"type": "text", "text": json.dumps({"timestamp": str(MS_A)})}]):
        out = clean(tool_result_event(content))[0]
        assert out["message"]["content"][0]["content"] == content
        assert kinds(json.dumps(out), []) == ["digits"]


def test_long_floats_in_tool_payloads_are_not_rounded():
    event = {"type": "user", "message": {"role": "user", "content": []}, "tool_use_result": {"account": LONG_FLOAT}}
    assert clean(event)[0]["tool_use_result"] == {"account": LONG_FLOAT}
    state = {"status": "completed", "input": {"amount": LONG_FLOAT}, "metadata": {"account": LONG_FLOAT}, "output": json.dumps({"a": LONG_FLOAT})}
    out = clean(oc_tool(state), "opencode")[0]
    assert out["part"]["state"]["input"] == {"amount": LONG_FLOAT} and out["part"]["state"]["metadata"] == {"account": LONG_FLOAT}
    assert kinds(json.dumps(out), []) != []


@pytest.mark.parametrize("where", ["assistant-text", "tool-result", "tool-input", "result-text"])
def test_id_shaped_values_in_authored_claude_content_stay(where):
    text = f"Client {JANE_ID}"
    events = {
        "assistant-text": assistant_with({"type": "text", "text": text}),
        "tool-result": tool_result_event(text),
        "tool-input": assistant_with({"type": "tool_use", "id": "toolu_01AbCdEfGh", "name": "Write", "input": {"content": text}}),
        "result-text": {"type": "result", "subtype": "success", "is_error": False, "result": text},
    }
    out = run(events[where])
    assert JANE_ID in out and kinds(out, ["jane"]) != []


@pytest.mark.parametrize("where", ["part-text", "state-output", "state-input", "state-error"])
def test_id_shaped_values_in_authored_opencode_content_stay(where):
    text = f"Client {JANE_ID}"
    events = {
        "part-text": oc_text(text),
        "state-output": oc_tool({"status": "completed", "input": {}, "output": text}),
        "state-input": oc_tool({"status": "completed", "input": {"content": text}}),
        "state-error": oc_tool({"status": "error", "input": {}, "error": text}),
    }
    out = run(events[where], "opencode")
    assert JANE_ID in out and kinds(out, ["jane"]) != []


ENCODED_HOMES = [
    "%2FUsers%2Ffakeuser%2Fnotes",
    r"\/Users\/fakeuser\/notes",
    r"\u002fUsers\u002ffakeuser\u002fnotes",
    json.dumps({"path": "/Users/fakeuser/notes"}),
    "/Users/fakeuser/notes",
]


@pytest.mark.parametrize("home_form", ENCODED_HOMES)
def test_a_home_path_in_authored_content_is_left_alone_and_flagged(home_form):
    events = [
        assistant_with({"type": "text", "text": f"see {home_form}"}),
        tool_result_event(f"cat {home_form}"),
        assistant_with({"type": "tool_use", "id": "toolu_01AbCdEfGh", "name": "Read", "input": {"file_path": home_form}}),
    ]
    out = run(events)
    assert out.count(json.dumps(home_form)[1:-1]) == 3
    assert kinds(out, [HOME]) == ["denylist"] * 3
    oc = run([oc_text(f"see {home_form}"), oc_tool({"status": "completed", "input": {"path": home_form}, "output": home_form})], "opencode")
    assert oc.count(json.dumps(home_form)[1:-1]) == 3 and kinds(oc, [HOME]) == ["denylist"] * 3


def test_ordinary_numbers_and_dates_in_agent_text_are_untouched():
    text = "Basis is 15,000; gain 1,000; deadline 2026-10-13; filed 2026-10-12T09:00:00Z"
    assert clean(assistant_with({"type": "text", "text": text}))[0]["message"]["content"][0]["text"] == text
    assert clean(oc_text(text), "opencode")[0]["part"]["text"] == text


# ================================================================ metadata is normalized (exact leaves)


def test_claude_init_leaves():
    event = init_event(
        cwd=f"{HOME}/proj", original_cwd=f"{HOME}/proj", memory_paths={"auto": f"{HOME}/.claude/projects/p/memory/"},
        plugins=[{"name": "p", "path": f"{HOME}/.claude/plugins/p", "source": "p@x"}],
        timestamp="2026-10-28T01:34:52.289Z", tools=["Read"], skills=["a"], apiKeySource="none",
    )
    out = clean(event)[0]
    assert re.fullmatch(r"00000000-0000-0000-0000-a\d{11}", out["session_id"]) and out["uuid"] == out["session_id"]
    assert out["cwd"] == out["original_cwd"] == "<HOME>/proj"
    assert out["memory_paths"] == {"auto": "<HOME>/.claude/projects/p/memory/"}
    assert out["plugins"][0]["path"] == "<HOME>/.claude/plugins/p" and out["timestamp"] == "1970-01-01T00:00:00Z"
    assert out["tools"] == ["Read"] and out["skills"] == ["a"] and out["model"] == "m"
    assert HOME not in json.dumps(out)


def test_claude_other_event_leaves():
    a, u, rl, th = clean([
        assistant_with({"type": "text", "text": "hi"}, session_id=REAL_UUID, uuid=REAL_UUID, request_id="req_011CgTn3FpiYwA4q", timestamp="2026-10-28T01:34:52.289Z"),
        tool_result_event("ok", session_id=REAL_UUID, uuid=REAL_UUID, timestamp="2026-10-28T01:34:53.000Z"),
        {"type": "rate_limit_event", "rate_limit_info": {"status": "allowed", "resetsAt": SEC_B, "unifiedWindows": {"five_hour": {"utilization": 0.2, "resetsAt": SEC_B}}},
         "uuid": REAL_UUID, "session_id": REAL_UUID},
        {"type": "system", "subtype": "thinking_tokens", "estimated_tokens": 50, "session_id": REAL_UUID, "uuid": REAL_UUID},
    ])
    assert re.fullmatch(r"msg_ID_\d+", a["message"]["id"]) and re.fullmatch(r"req_ID_\d+", a["request_id"])
    assert a["timestamp"] == u["timestamp"] == "1970-01-01T00:00:00Z"
    assert a["session_id"] == a["uuid"] == u["session_id"] == rl["uuid"] == th["session_id"]
    assert rl["rate_limit_info"] == {"status": "allowed", "resetsAt": 0, "unifiedWindows": {"five_hour": {"utilization": 0.2, "resetsAt": 0}}}
    assert th["estimated_tokens"] == 50
    assert u["message"]["content"][0]["tool_use_id"] == "toolu_01AbCdEfGh"  # tool-call ids are never rewritten


def test_opencode_leaves():
    events = [
        {"type": "step_start", "timestamp": MS_A, "sessionID": "ses_e7fe011ddffe5fM4Y3O",
         "part": {"id": "prt_18020bff8001bYUNJ", "sessionID": "ses_e7fe011ddffe5fM4Y3O", "messageID": "msg_18020ad9d001tOJ8", "type": "step-start"}},
        oc_tool({"status": "completed", "input": {"path": "a.md"}, "output": "ok", "time": {"start": MS_B, "end": MS_A}}, partID="prt_18020c484001LL4P", messageID="msg_18020ad9d001tOJ8"),
        oc_text("Basis 15,000."),
        {"type": "step_finish", "timestamp": MS_A, "sessionID": "ses_e7fe011ddffe5fM4Y3O",
         "part": {"id": "prt_1abcdefgh2", "type": "step-finish", "reason": "tool-calls", "cost": LONG_COST, "tokens": {"input": 5626, "output": 62, "cache": {"read": 0, "write": 0}}}},
        {"type": "error", "timestamp": MS_C, "sessionID": "ses_e7fe011ddffe5fM4Y3O", "error": {"type": "provider.no-route", "message": "Model unavailable"}},
    ]
    start, tool, text, finish, error = clean(events, "opencode")
    assert re.fullmatch(r"ses_ID_\d+", start["sessionID"]) and start["part"]["sessionID"] == start["sessionID"] == tool["sessionID"]
    assert re.fullmatch(r"prt_ID_\d+", start["part"]["id"]) and re.fullmatch(r"msg_ID_\d+", start["part"]["messageID"])
    assert re.fullmatch(r"call_ID_\d+", tool["part"]["id"]) and re.fullmatch(r"prt_ID_\d+", tool["part"]["partID"])
    assert start["timestamp"] == tool["timestamp"] == text["timestamp"] == finish["timestamp"] == error["timestamp"] == 0
    assert tool["part"]["state"]["time"] == {"start": 0, "end": 0} and text["part"]["time"] == {"start": 0, "end": 0}
    assert tool["part"]["state"]["input"] == {"path": "a.md"} and text["part"]["text"] == "Basis 15,000."
    assert finish["part"]["cost"] == 0.333924 and finish["part"]["tokens"] == events[3]["part"]["tokens"]
    assert error["error"] == events[4]["error"]
    assert kinds(json.dumps([start, tool, text, finish, error]), []) == []


def test_time_normalization_only_touches_epoch_shaped_values():
    event = {"type": "step_start", "timestamp": 7, "sessionID": "ses_x", "part": {"type": "step-start", "id": "prt_x"}}
    assert clean(event, "opencode")[0] == event
    event = {"type": "text", "timestamp": "soon", "part": {"type": "text", "time": {"start": 3, "end": "later"}}}
    assert clean(event, "opencode")[0] == event


def test_small_floats_are_untouched():
    res = {"type": "result", "subtype": "success", "result": "x", "total_cost_usd": 0.058, "usage": {"r": 0.2, "n": 3.14159}}
    assert clean(res)[0] == res


# ================================================================ R19(b): harness-created literals


FORMS = {
    "plain": WS,
    "private-prefixed": f"/private{WS}",
    "json-escaped slashes": WS.replace("/", "\\/"),
    "unicode-escaped": WS.replace("/", "\\u002f"),
    "unicode-escaped upper": WS.replace("/", "\\u002F"),
    "percent-encoded": WS.replace("/", "%2F"),
    "percent-encoded lowercase": WS.replace("/", "%2f"),
    "escaped twice": WS.replace("/", "\\\\/"),
}


@pytest.mark.parametrize("name", sorted(FORMS))
def test_the_workspace_literal_is_replaced_everywhere_in_every_form(name):
    path = FORMS[name]
    events = [
        tool_result_event(f"{path}/03_TAX_YEARS/f.md"),
        assistant_with({"type": "tool_use", "id": "toolu_01AbCdEfGh", "name": "Read", "input": {"file_path": f"{path}/a.md"}}),
        assistant_with({"type": "text", "text": f"I read {path}/b.md"}),
    ]
    out = run(events, harness=HARNESS)
    assert "<WORKSPACE>" in out and "taxeval-abc" not in out
    parsed = clean(events, harness=HARNESS)
    assert parsed[0]["message"]["content"][0]["content"].startswith("<WORKSPACE>")


def test_the_reviewed_example_path_becomes_workspace_relative():
    real_like = "/private/var/folders/ab/cdef/T/taxeval-abc/03_TAX_YEARS/TY_2024/Filing/x.md"
    out = clean(tool_result_event(real_like), harness=HARNESS)[0]
    assert out["message"]["content"][0]["content"] == "<WORKSPACE>/03_TAX_YEARS/TY_2024/Filing/x.md"


def test_temp_root_outside_the_workspace_becomes_tmp():
    out = clean(tool_result_event(f"{TMPROOT}/taxeval-oc-9/home/.config"), harness=HARNESS)[0]
    assert out["message"]["content"][0]["content"] == "<TMP>/taxeval-oc-9/home/.config"


def test_harness_replacement_reaches_keys_and_plain_text_lines_and_nested_json_text():
    event = {"type": "user", "message": {"role": "user", "content": []}, "tool_use_result": {WS + "/a.md": 1}}
    assert clean(event, harness=HARNESS)[0]["tool_use_result"] == {"<WORKSPACE>/a.md": 1}
    text = f"warning: {WS}/x\nnot json"
    assert sanitize_transcript(text, tool="claude", home_dirs=[HOME], harness_paths=HARNESS) == "warning: <WORKSPACE>/x\nnot json"
    nested = json.dumps({"path": WS + "/a.md"})
    assert clean(tool_result_event(nested), harness=HARNESS)[0]["message"]["content"][0]["content"] == json.dumps({"path": "<WORKSPACE>/a.md"})


def test_a_sibling_directory_is_not_mistaken_for_the_workspace():
    out = clean(tool_result_event(f"{WS}d/x and {WS}_old/y and {WS}/z"), harness=HARNESS)[0]
    assert out["message"]["content"][0]["content"] == "<TMP>/taxeval-abcd/x and <TMP>/taxeval-abc_old/y and <WORKSPACE>/z"


def test_harness_literals_must_be_passed_in_not_inferred():
    event = tool_result_event(f"{WS}/a.md")
    assert run(event) == json.dumps(event)  # nothing passed -> nothing replaced


def test_user_paths_in_authored_content_stay_and_are_flagged_next_to_replaced_harness_paths():
    event = tool_result_event(f"{WS}/a.md copied to {HOME}/Documents/b.md")
    out = run(event, harness=HARNESS)
    assert "<WORKSPACE>/a.md" in out and f"{HOME}/Documents/b.md" in out
    assert kinds(out, [HOME]) == ["denylist"]


# ================================================================ R20: key collisions fail closed


FAKE_SSN = "-".join(["123", "45", "6789"])


def checked(events, tool="claude", harness=HARNESS):
    items = events if isinstance(events, list) else [events]
    text = "\n".join(json.dumps(e) for e in items)
    return sanitize.sanitize(text, tool=tool, home_dirs=[HOME], harness_paths=list(harness))


def test_colliding_keys_keep_both_values_and_withhold_the_transcript():
    values = {f"{WS}/a": FAKE_SSN, "<WORKSPACE>/a": "clean"}
    event = {"type": "user", "message": {"role": "user", "content": []}, "tool_use_result": values}
    assert kinds(json.dumps(event), []) == ["ssn"]
    result = checked(event)
    assert json.loads(result.text)["tool_use_result"] == values
    assert [f.kind for f in result.findings] == ["sanitize-collision"]
    assert kinds(result.text, []) == ["ssn"]


def test_collisions_are_detected_at_any_depth_and_for_both_tools():
    values = {"<TMP>/other/x": "one", f"{WS}/x": "two", f"{TMPROOT}/other/x": "three"}
    claude_event = assistant_with({"type": "tool_use", "id": "toolu_01AbCdEfGh", "name": "Write", "input": {"deep": [values]}})
    result = checked(claude_event)
    assert json.loads(result.text)["message"]["content"][0]["input"]["deep"] == [values]
    assert [f.kind for f in result.findings] == ["sanitize-collision"]
    oc_event = oc_tool({"status": "completed", "input": {}, "metadata": {"files": values}})
    result = checked(oc_event, "opencode")
    assert json.loads(result.text)["part"]["state"]["metadata"]["files"] == values
    assert [f.kind for f in result.findings] == ["sanitize-collision"]


def test_a_collision_is_reported_on_its_line():
    clean_event = tool_result_event("ok")
    bad = {"type": "user", "tool_use_result": {f"{WS}/a": 1, "<WORKSPACE>/a": 2}}
    result = checked([clean_event, clean_event, bad])
    assert [(f.kind, f.line) for f in result.findings] == [("sanitize-collision", 3)]


def test_duplicate_keys_in_a_raw_line_are_kept_verbatim_and_flagged():
    line = '{"type":"user","tool_use_result":{"a":"%s","a":"%s/x"}}' % (FAKE_SSN, WS)
    result = sanitize.sanitize(line, tool="claude", home_dirs=[HOME], harness_paths=HARNESS)
    assert result.text == line
    assert [f.kind for f in result.findings] == ["sanitize-collision"]


def test_keys_that_do_not_collide_produce_no_finding():
    event = {"type": "user", "tool_use_result": {f"{WS}/a": 1, f"{WS}/b": 2, "<WORKSPACE>/c": 3}}
    result = checked(event)
    assert json.loads(result.text)["tool_use_result"] == {"<WORKSPACE>/a": 1, "<WORKSPACE>/b": 2, "<WORKSPACE>/c": 3}
    assert result.findings == []
    assert sanitize_transcript(json.dumps(event), tool="claude", home_dirs=[HOME], harness_paths=HARNESS) == result.text


def test_a_collision_inside_json_embedded_in_a_string_keeps_the_string_and_is_flagged():
    inner = json.dumps({WS + "/a": "one", "<WORKSPACE>/a": "clean"})
    event = {"type": "text", "part": {"type": "text", "text": inner}}
    result = checked(event, "opencode")
    assert json.loads(result.text)["part"]["text"] == inner
    assert [f.kind for f in result.findings] == ["sanitize-collision"]


def test_a_collision_two_levels_deep_in_embedded_json_keeps_the_string_and_is_flagged():
    deepest = json.dumps({WS + "/a": FAKE_SSN, "<WORKSPACE>/a": "clean"})
    outer = json.dumps({"path": WS + "/b", "payload": json.dumps({"inner": deepest})})
    event = tool_result_event(outer)
    result = checked(event)
    assert json.loads(result.text)["message"]["content"][0]["content"] == outer
    assert [f.kind for f in result.findings] == ["sanitize-collision"]
    assert kinds(result.text, []) == ["ssn"]


def test_embedded_json_nested_past_the_depth_limit_is_kept_and_flagged():
    nested = json.dumps({"k": "v"})
    for _ in range(sanitize.MAX_EMBEDDED_DEPTH + 1):
        nested = json.dumps({"n": nested})
    outer = json.dumps({"path": WS + "/a", "n": nested})
    result = checked(tool_result_event(outer))
    assert json.loads(result.text)["message"]["content"][0]["content"] == outer
    assert [f.kind for f in result.findings] == ["sanitize-collision"]


def test_embedded_json_without_a_collision_is_still_rewritten():
    inner = json.dumps({WS + "/a": "one", "nested": json.dumps({WS + "/b": "two"})})
    result = checked(tool_result_event(inner))
    content = json.loads(result.text)["message"]["content"][0]["content"]
    assert content == json.dumps({"<WORKSPACE>/a": "one", "nested": json.dumps({"<WORKSPACE>/b": "two"})})
    assert result.findings == []


def test_a_string_that_only_looks_like_json_keeps_the_plain_text_behavior():
    text = "[" + WS + "/a, not json"
    result = checked(tool_result_event(text))
    assert json.loads(result.text)["message"]["content"][0]["content"] == "[<WORKSPACE>/a, not json"
    assert result.findings == []


# ================================================================ R20: harness literals only at path boundaries


@pytest.mark.parametrize("authored", ["/Users/tmp/notes", "/tmp-client/file.txt"])
def test_a_generic_temp_root_never_rewrites_an_authored_path(authored):
    event = tool_result_event(f"see {authored} and {authored}")
    out = run(event, harness=[("/tmp", "<TMP>")])
    assert out == json.dumps(event)
    assert kinds(out, [authored]) == ["denylist", "denylist"]


@pytest.mark.parametrize("root", ["/tmp", "/var/tmp", "/private/tmp", "/private/var/tmp", "/short/t"])
def test_generic_or_short_temp_roots_are_never_replaced(root):
    event = tool_result_event(f"{root}/x and {root}")
    assert run(event, harness=[(root, "<TMP>")]) == json.dumps(event)


def test_the_workspace_is_still_replaced_when_the_temp_root_is_generic():
    ws = "/tmp/taxeval-abc123"
    out = clean(tool_result_event(f"{ws}/a.md and /tmp/other"), harness=[(ws, "<WORKSPACE>"), ("/tmp", "<TMP>")])[0]
    assert out["message"]["content"][0]["content"] == "<WORKSPACE>/a.md and /tmp/other"


MAC_ROOT = "/var/folders/zz/abcdefghijklmnopqrstuvwx0000gn/T"
MAC_ENCODINGS = {
    "plain": "/",
    "json-escaped": "\\/",
    "escaped twice": "\\\\/",
    "unicode": "\\u002f",
    "unicode upper": "\\u002F",
    "percent": "%2F",
    "percent lower": "%2f",
    "percent twice": "%252F",
}


@pytest.mark.parametrize("prefix", ["", "/private"])
@pytest.mark.parametrize("name", sorted(MAC_ENCODINGS))
def test_a_real_macos_temp_root_is_replaced_at_boundaries_in_every_form(name, prefix):
    slash = MAC_ENCODINGS[name]
    root = (prefix + MAC_ROOT).replace("/", slash)
    lefts = ["", " ", '"', "'", "=", "(", "[", ",", ":", "<", ">", "|"]
    rights = [slash + "x", "", " ", '"', ")", ","]
    for left in lefts:
        for right in rights:
            text = f"{left}{root}{right}"
            out = clean(tool_result_event(text), harness=[(MAC_ROOT, "<TMP>")])[0]["message"]["content"][0]["content"]
            assert out == f"{left}<TMP>{right}", (name, left, right)


@pytest.mark.parametrize("name", sorted(MAC_ENCODINGS))
def test_a_root_inside_a_longer_path_or_followed_by_path_characters_is_not_replaced(name):
    slash = MAC_ENCODINGS[name]
    root = MAC_ROOT.replace("/", slash)
    cases = [
        f"{slash}Users{root}{slash}notes",  # left: a "/" from a longer path
        f"x{root}{slash}notes",
        f"{root}xyz{slash}f.md",  # right: more path characters
        f"{root}-client{slash}f.md",
        f"{root}.bak",
        f"{root}_old",
        f"{root}\\u0078yz",  # an encoded letter continues the path
        f"{root}%78yz",
    ]
    for text in cases:
        event = tool_result_event(text)
        assert run(event, harness=[(MAC_ROOT, "<TMP>")]) == json.dumps(event), (name, text)


def test_the_workspace_literal_obeys_the_same_boundaries():
    for text in (f"/Users{WS}/a", f"{WS}-old/a", f"{WS}xyz"):
        out = clean(tool_result_event(text), harness=[(WS, "<WORKSPACE>")])[0]
        assert out["message"]["content"][0]["content"] == text
    out = clean(tool_result_event(f"cd {WS}; ls ({WS})"), harness=[(WS, "<WORKSPACE>")])[0]
    assert out["message"]["content"][0]["content"] == "cd <WORKSPACE>; ls (<WORKSPACE>)"


@pytest.mark.parametrize("name", sorted(MAC_ENCODINGS))
def test_a_file_uri_scheme_is_a_left_boundary_in_every_form(name):
    slash = MAC_ENCODINGS[name]
    for prefix in ("", "/private"):
        uri = f"file:{slash}{slash}" + f"{prefix}{WS}/x".replace("/", slash)
        out = clean(tool_result_event(f"open {uri}"), harness=HARNESS)[0]["message"]["content"][0]["content"]
        assert out == f"open file:{slash}{slash}<WORKSPACE>{slash}x", (name, prefix)


def test_file_uris_of_unrelated_paths_stay_and_are_flagged():
    event = tool_result_event("see file:///Users/tmp/notes")
    out = run(event, harness=[("/tmp", "<TMP>")])
    assert out == json.dumps(event) and kinds(out, ["/Users/tmp/notes"]) == ["denylist"]
    for text in (f"file:///Users{MAC_ROOT}/x", f"file://{MAC_ROOT}xyz/x", f"file:/x{MAC_ROOT}/x"):
        event = tool_result_event(text)
        assert run(event, harness=[(MAC_ROOT, "<TMP>")]) == json.dumps(event), text


def test_boundaries_also_apply_to_plain_text_lines():
    text = f"warning: /Users{MAC_ROOT}/x and {MAC_ROOT}/y"
    out = sanitize_transcript(text, tool="claude", home_dirs=[HOME], harness_paths=[(MAC_ROOT, "<TMP>")])
    assert out == f"warning: /Users{MAC_ROOT}/x and <TMP>/y"


# ================================================================ rule table shape


CONTAINER_KEYS = {"skills", "slash_commands", "tools", "mcp_servers", "plugins", "agents", "memory_paths", "usage", "modelUsage",
                  "model_usage_since_last_result", "tokens", "time", "state", "message", "part", "rate_limit_info", "unifiedWindows",
                  "error", "input", "output", "content", "metadata"}
FORBIDDEN_LEAVES = {"claude": ("message.content", "result", "tool_use_result", "tool_result_meta", "permission_denials", "wire_tool_inputs", "error"),
                    "opencode": ("part.text", "part.state.input", "part.state.output", "part.state.error", "part.state.metadata", "error")}
KINDS = {"time", "id", "home", "float", "thinking_signature", "thinking_data"}
# The only rules that reach into message.content, and only for the opaque leaf of a thinking block.
THINKING_OPAQUE_RULES = {
    ("assistant", None, "message.content.*.signature"): "thinking_signature",
    ("assistant", None, "message.content.*.data"): "thinking_data",
}
# The one CLI timing metric inside a tool result (T11 fix 9): a float the CLI computes.
CLI_METRIC_RULES = {("user", None, "tool_use_result.durationSeconds"): "float"}


@pytest.mark.parametrize("tool,table", [("claude", CLAUDE_METADATA), ("opencode", OPENCODE_METADATA)])
def test_every_rule_names_an_event_type_and_an_exact_leaf(tool, table):
    assert table
    for (etype, subtype), leaves in table.items():
        assert isinstance(etype, str) and etype, "a rule must be gated by event type"
        assert subtype is None or isinstance(subtype, str)
        assert leaves
        for path, kind in leaves.items():
            segments = path.split(".")
            assert kind in KINDS, (etype, path)
            assert segments[0] != "*", f"rule must start with a named key: {path}"
            assert segments[-1] not in CONTAINER_KEYS, f"{etype}: {path} ends at a container name"
            if {**THINKING_OPAQUE_RULES, **CLI_METRIC_RULES}.get((etype, subtype, path)) == kind:
                continue
            for forbidden in FORBIDDEN_LEAVES[tool]:
                assert path != forbidden and not path.startswith(forbidden + "."), f"{tool}: {path} is authored content"


def test_the_thinking_leaves_are_the_only_rules_that_reach_into_message_content():
    reaching = {
        (key[0], key[1], path): kind
        for key, leaves in CLAUDE_METADATA.items()
        for path, kind in leaves.items()
        if path.startswith("message.content")
    }
    assert reaching == THINKING_OPAQUE_RULES


def test_duration_seconds_is_the_only_rule_that_reaches_into_a_tool_result():
    reaching = {
        (key[0], key[1], path): kind
        for key, leaves in CLAUDE_METADATA.items()
        for path, kind in leaves.items()
        if path.startswith("tool_use_result")
    }
    assert reaching == CLI_METRIC_RULES


def test_claude_duration_seconds_is_rounded_and_then_scans_clean():
    duration = float("8." + "12345" + "67890" + "12345")
    event = {"type": "user", "message": {"role": "user", "content": []},
             "tool_use_result": {"query": "capital loss limit", "durationSeconds": duration, "other": LONG_FLOAT}}
    assert kinds(json.dumps({**event, "tool_use_result": {"durationSeconds": duration}}), []) == ["digits"]
    out = clean(event)[0]
    assert out["tool_use_result"]["durationSeconds"] == round(duration, 6)
    assert out["tool_use_result"]["other"] == LONG_FLOAT, "only the named leaf is rounded"
    del out["tool_use_result"]["other"]
    assert kinds(json.dumps(out), []) == []


def test_duration_seconds_elsewhere_is_not_rounded():
    duration = float("8." + "12345" + "67890" + "12345")
    for event in (
        {"type": "user", "message": {"role": "user", "content": []}, "tool_use_result": {"results": [{"durationSeconds": duration}]}},
        {"type": "assistant", "message": {"id": "m", "content": []}, "tool_use_result": {"durationSeconds": duration}},
        tool_result_event({"durationSeconds": duration}),
    ):
        assert kinds(run(event), []) == ["digits"]


REAL = Path(__file__).resolve().parent / "fixtures" / "real"


def real_events(tool):
    prefix = "claude" if tool == "claude" else "opencode"
    for path in sorted(REAL.glob(f"{prefix}_*.jsonl")):
        for line in path.read_text().splitlines():
            yield json.loads(line)


@pytest.mark.parametrize("tool", ["claude", "opencode"])
def test_no_exact_rule_ever_matches_a_container_in_real_events(tool):
    matched = 0
    for event in real_events(tool):
        for path, value, pattern in iter_metadata(tool, event):
            matched += 1
            if not pattern.endswith("*"):
                assert not isinstance(value, (dict, list)), f"{tool}: rule {pattern} reached the container at {path}"
    assert matched


def test_the_only_wildcard_leaf_rule_is_result_usage_and_the_sanitizer_skips_containers_it_reaches():
    wildcard_leaves = [(key, path) for key, leaves in CLAUDE_METADATA.items() for path in leaves if path.endswith("*")]
    wildcard_leaves += [(key, path) for key, leaves in OPENCODE_METADATA.items() for path in leaves if path.endswith("*")]
    assert wildcard_leaves == [(("result", None), "usage.*")]
    event = {"type": "result", "subtype": "success", "usage": {"iterations": [{"c": LONG_COST}], "x": LONG_COST, "t": JANE_ID}}
    matched = {path: value for path, value, _ in iter_metadata("claude", event)}
    assert isinstance(matched["usage.iterations"], list)
    assert clean(event)[0]["usage"] == {"iterations": [{"c": LONG_COST}], "x": 0.333924, "t": JANE_ID}


# ================================================================ real fixtures and mechanics


@pytest.mark.parametrize("name,tool", [(p.name, "claude" if p.name.startswith("claude") else "opencode") for p in sorted(REAL.glob("*.jsonl"))])
def test_real_fixtures_pass_through_unchanged_because_they_hold_only_normalized_metadata(name, tool):
    text = (REAL / name).read_text()
    out = sanitize_transcript(text, tool=tool, home_dirs=[HOME], harness_paths=HARNESS)
    assert [json.loads(x) for x in out.splitlines()] == [json.loads(x) for x in text.splitlines()]
    assert not scan_text(out, [])


def raw_like(name, tool):
    """The fixture with the metadata real CLIs print: timestamps, home paths, long costs."""
    events = [json.loads(x) for x in (REAL / name).read_text().splitlines()]
    for e in events:
        if tool == "opencode":
            e["timestamp"] = MS_A
            part = e.get("part")
            if isinstance(part, dict) and part.get("type") == "text":
                part["time"] = {"start": MS_B, "end": MS_A}
            if isinstance(part, dict) and part.get("type") == "tool":
                part["state"]["time"] = {"start": MS_B, "end": MS_A}
        else:
            if e.get("type") == "system" and e.get("subtype") == "init":
                e["cwd"] = WS
                e["memory_paths"] = {"auto": f"{HOME}/.claude/projects/p/memory/"}
            if e.get("type") == "result":
                e["total_cost_usd"] = LONG_COST
            if e.get("type") == "rate_limit_event":
                e["rate_limit_info"]["resetsAt"] = SEC_B
    return events


@pytest.mark.parametrize("name,tool", [("claude_seed_guard.jsonl", "claude"), ("claude_judge_structured.jsonl", "claude"),
                                       ("opencode_denied_patch.jsonl", "opencode"), ("opencode_allowed_patch.jsonl", "opencode"),
                                       ("opencode_question_cancelled.jsonl", "opencode")])
def test_raw_like_real_transcripts_are_normalized_and_clean_where_authored_content_is_clean(name, tool):
    raw = "\n".join(json.dumps(e) for e in raw_like(name, tool))
    assert scan_text(raw, [HOME])
    out = sanitize_transcript(raw, tool=tool, home_dirs=[HOME], harness_paths=HARNESS)
    assert not scan_text(out, [HOME]), "authored content in the real fixtures is clean"


def test_unknown_tool_is_left_completely_alone():
    text = json.dumps({"timestamp": MS_A, "cwd": f"{HOME}/x"})
    assert sanitize_transcript(text, tool="other", home_dirs=[HOME], harness_paths=HARNESS) == text


def test_non_json_and_non_object_lines_are_left_alone():
    text = f"warning: cannot read {HOME}/.claude.json\n[1, 2]\n\"{HOME}\"\n\n"
    assert sanitize_transcript(text, tool="claude", home_dirs=[HOME]) == text


def test_unchanged_lines_are_kept_byte_for_byte():
    text = '{"type": "x",   "n": 1}\nnot json at all\n{"a":[1, 2]}\n'
    assert sanitize_transcript(text, tool="claude", home_dirs=[HOME], harness_paths=HARNESS) == text


def test_sanitizing_twice_changes_nothing():
    raw = "\n".join(json.dumps(e) for e in raw_like("claude_seed_guard.jsonl", "claude"))
    once = sanitize_transcript(raw, tool="claude", home_dirs=[HOME], harness_paths=HARNESS)
    assert sanitize_transcript(once, tool="claude", home_dirs=[HOME], harness_paths=HARNESS) == once


def test_path_variants_cover_private_prefixes():
    assert {"/var/folders/x/T", "/private/var/folders/x/T"} <= set(path_variants(Path("/var/folders/x/T")))
    assert {"/var/y", "/private/var/y"} <= set(path_variants(Path("/private/var/y")))
    assert path_variants(Path("/")) == []


def test_real_home_directory_is_the_default(monkeypatch):
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: Path("/Users/fakeuser")))
    event = init_event(memory_paths={"auto": "/Users/fakeuser/.claude/x"})
    out = sanitize_transcript(json.dumps(event), tool="claude")
    assert json.loads(out)["memory_paths"] == {"auto": "<HOME>/.claude/x"}


def test_other_users_home_in_a_home_leaf_is_replaced():
    event = init_event(plugins=[{"path": "/Users/someoneelse/.claude/p"}])
    assert clean(event)[0]["plugins"][0]["path"] == "<HOME>/.claude/p"


# ================================================================ thinking blocks: opaque signature and data

OPAQUE_TERM = "zq9x"  # a 4-letter chance match, as in the live run
THINKING_SIG = "".join(["EqQBCkYIBxgC", "KkD" + OPAQUE_TERM + "Q9LmP", "o3Hq7vWnT2Zk"])
REDACTED_DATA = "".join(["EsgDCkYIBxgC", "KkD" + OPAQUE_TERM + "R1xYp", "q8Jt4uMsV6Nb"])


def test_thinking_signature_is_replaced_even_when_it_contains_a_denylist_term():
    block = {"type": "thinking", "thinking": "", "signature": THINKING_SIG}
    text = run(assistant_with(block))
    assert kinds(json.dumps(assistant_with(block)), [OPAQUE_TERM]) == ["denylist"]  # the false positive
    assert kinds(text, [OPAQUE_TERM]) == []
    out = json.loads(text)["message"]["content"][0]
    assert out == {"type": "thinking", "thinking": "", "signature": "<THINKING_SIGNATURE>"}


def test_redacted_thinking_data_is_replaced_the_same_way():
    block = {"type": "redacted_thinking", "data": REDACTED_DATA}
    out = clean(assistant_with(block))[0]["message"]["content"][0]
    assert out == {"type": "redacted_thinking", "data": "<THINKING_DATA>"}
    assert kinds(run(assistant_with(block)), [OPAQUE_TERM]) == []


def test_signature_and_data_on_other_content_items_are_never_touched():
    items = [
        {"type": "text", "text": "the signature is abc", "signature": THINKING_SIG, "data": REDACTED_DATA},
        {"type": "tool_use", "id": "toolu_01AbCdEfGh", "name": "Write", "input": {"signature": THINKING_SIG, "data": "abc"}},
        {"type": "thinking", "thinking": "reasoning with abc", "data": REDACTED_DATA},  # data is only opaque on redacted_thinking
        {"signature": THINKING_SIG},  # no type at all
    ]
    for item in items:
        assert clean(assistant_with(item))[0]["message"]["content"] == [item], item


def test_thinking_text_is_never_rewritten_only_the_opaque_leaf():
    block = {"type": "thinking", "thinking": f"look in {HOME}/x", "signature": THINKING_SIG}
    out = clean(assistant_with(block))[0]["message"]["content"][0]
    assert out["thinking"] == f"look in {HOME}/x"
    assert out["signature"] == "<THINKING_SIGNATURE>"


def test_signature_outside_assistant_message_content_is_untouched():
    cases = [
        {"type": "user", "message": {"content": [{"type": "thinking", "signature": THINKING_SIG}]}},
        {"type": "assistant", "signature": THINKING_SIG},
        {"type": "assistant", "message": {"signature": THINKING_SIG}},
    ]
    for event in cases:
        assert run(event) == json.dumps(event), event
