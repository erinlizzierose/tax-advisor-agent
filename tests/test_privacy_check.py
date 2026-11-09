import subprocess

import privacy_check
from privacy_check import scan_text

# Built from parts so this file does not trip the scanner it tests.
SSN = "123-" + "45-" + "6789"
EIN = "12-" + "3456789"
LONG_DIGITS = "123456" + "789012"


def test_flags_ssn_pattern():
    findings = scan_text(f"ssn {SSN}", [])
    assert findings[0].kind == "ssn"


def test_flags_ein_and_long_digit_runs():
    assert scan_text(f"ein {EIN}", [])[0].kind == "ein"
    assert scan_text(f"acct {LONG_DIGITS}", [])[0].kind == "digits"


def test_hex_hash_is_not_a_digit_run():
    assert scan_text("sha256 = 1234567890abcdef1234567890abcdef", []) == []


def test_flags_denylist_case_insensitive():
    findings = scan_text("I work at Acme Widgets Ltd", ["acme widgets"])
    assert findings[0].kind == "denylist"


def test_finding_is_redacted():
    findings = scan_text(f"ssn {SSN}", [])
    assert SSN not in repr(findings)
    assert findings[0].redacted == "1*********9"


def test_denylist_finding_is_redacted():
    findings = scan_text("Acme Widgets", ["acme widgets"])
    assert "Acme Widgets" not in repr(findings)


def test_reports_line_numbers():
    findings = scan_text(f"ok\nok\nssn {SSN}", [])
    assert findings[0].line == 3


def test_ignores_masked_identifiers():
    assert scan_text("acct ***6789", []) == []


# --- decoded variants: the same text, hidden by an encoding layer -------------------

HOME = "/Users/fakeuser"
DENY = [HOME]
NOTES = "/Users/fakeuser/notes.md"


def _json_text_in_json(path):
    """A path inside JSON text that is itself a JSON string value (nested twice)."""
    import json

    inner = json.dumps({"path": path})
    return json.dumps({"content": inner})


def test_encoded_forms_of_a_denylisted_path_are_caught():
    forms = {
        "plain": NOTES,
        "url-encoded": "%2FUsers%2Ffakeuser%2Fnotes.md",
        "url-encoded lowercase hex": "%2fUsers%2ffakeuser%2fnotes.md",
        "double url-encoded": "%252FUsers%252Ffakeuser%252Fnotes.md",
        "json-escaped slashes": r"\/Users\/fakeuser\/notes.md",
        "unicode-escaped slashes": r"\u002fUsers\u002ffakeuser\u002fnotes.md",
        "unicode-escaped upper": r"\u002FUsers\u002Ffakeuser\u002Fnotes.md",
        "unicode-escaped letters": r"\u002f\u0055sers\u002ffakeuser\u002fnotes.md",
        "json text in json (dumps)": _json_text_in_json(NOTES.replace("/", "\\u002f")),
        "escaped slashes, twice": r"\\/Users\\/fakeuser\\/notes.md",
        "unicode-escaped, twice": r"\\u002fUsers\\u002ffakeuser\\u002fnotes.md",
        "mixed": r"%2FUsers\/fakeuser\u002fnotes.md",
    }
    for name, text in forms.items():
        found = scan_text(f'{{"x": "{text}"}}' if name != "json text in json (dumps)" else text, DENY)
        assert [f.kind for f in found] == ["denylist"], name


def test_encoded_digits_and_ssn_are_caught_too():
    # assembled at run time so this file does not trip the scanner it tests
    encoded_digits = "acct 12345" + "%" + "36" + "789012"
    encoded_ssn = "id 123" + "\\" + "u002d" + "45" + "\\" + "u002d" + "6789"
    assert [f.kind for f in scan_text(encoded_digits, [])] == ["digits"]
    assert [f.kind for f in scan_text(encoded_ssn, [])] == ["ssn"]


def test_decoded_findings_are_not_double_counted_and_keep_their_line_numbers():
    findings = scan_text(f"ok\nok\npath {NOTES}", DENY)
    assert len(findings) == 1 and findings[0].line == 3
    both = scan_text(f"{NOTES} and %2FUsers%2Ffakeuser", DENY)
    assert len(both) == 2


def test_decoding_does_not_invent_findings_in_clean_text():
    for text in ("100%25 sure", r"a\/b and a\u002fb", "caf%C3%A9", r"\u00e9t\u00e9", "%zz and \\u12", "plain /tmp/x"):
        assert scan_text(text, DENY) == [], text


def _layers(text, count):
    from urllib.parse import quote

    for _ in range(count):
        text = quote(text, safe="")
    return text


def test_six_layers_of_percent_encoding_are_still_decoded_and_caught():
    assert [f.kind for f in scan_text(_layers(NOTES, 6), DENY)] == ["denylist"]


def test_seven_layers_exceed_the_decoding_limit_and_fail_closed():
    found = scan_text(f"line one\nsee {_layers(NOTES, 7)}\nline three", DENY)
    assert [f.kind for f in found] == ["excessive-encoding"]
    assert found[0].line == 2
    assert "Users" not in repr(found) and "fakeuser" not in repr(found)


def test_excessive_encoding_is_reported_for_text_with_no_denylisted_term_too():
    assert [f.kind for f in scan_text(_layers("harmless words", 8), [])] == ["excessive-encoding"]


def test_ordinary_percent_text_never_trips_the_limit():
    for text in ("100%25 sure", "50% off", "%25%25%25", "a%2Fb", "%zz", "%"):
        assert [f.kind for f in scan_text(text, DENY)] == [], text


def test_excessive_encoding_makes_the_cli_exit_nonzero(tmp_path):
    target = tmp_path / "t.txt"
    target.write_text(_layers("hello world", 8), encoding="utf-8")
    deny = tmp_path / "deny.txt"
    deny.write_text("zzz\n")
    assert privacy_check.main(["--denylist", str(deny), str(target)]) == 1


def test_findings_stay_redacted_for_decoded_matches():
    found = scan_text("%2FUsers%2Ffakeuser", DENY)
    assert "fakeuser" not in repr(found) and "Users" not in repr(found)


def test_missing_denylist_exits_2(tmp_path):
    missing = tmp_path / "nope.txt"
    target = tmp_path / "file.txt"
    target.write_text("hello")
    assert privacy_check.main(["--denylist", str(missing), str(target)]) == 2


def test_parse_added_lines_keeps_content_starting_with_plus_plus():
    diff = (
        "diff --git a/f.txt b/f.txt\n"
        "new file mode 100644\n"
        "--- /dev/null\n"
        "+++ b/f.txt\n"
        "@@ -0,0 +1,3 @@\n"
        f"+++ssn {SSN}\n"
        "+plain\n"
        "-removed\n"
        " context\n"
        "diff --git a/g.txt b/g.txt\n"
        "--- a/g.txt\n"
        "+++ b/g.txt\n"
        "@@ -1 +1 @@\n"
        "+second file\n"
    )
    assert privacy_check.parse_added_lines(diff) == [
        f"++ssn {SSN}",
        "plain",
        "second file",
    ]


def test_staged_scan_flags_added_line_starting_with_plus_plus(tmp_path, monkeypatch):
    denylist = tmp_path / "denylist.txt"
    denylist.write_text("")
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True, capture_output=True, timeout=60)
    (repo / "f.txt").write_text(f"++ssn {SSN}\n")
    subprocess.run(["git", "add", "f.txt"], cwd=repo, check=True, capture_output=True, timeout=60)
    monkeypatch.chdir(repo)
    assert privacy_check.main(["--denylist", str(denylist), "--staged"]) == 1


def test_missing_target_exits_2(tmp_path, capsys):
    denylist = tmp_path / "denylist.txt"
    denylist.write_text("")
    missing = tmp_path / "typo.txt"
    assert privacy_check.main(["--denylist", str(denylist), str(missing)]) == 2
    assert "typo.txt" in capsys.readouterr().err


def test_directory_target_exits_2(tmp_path, capsys):
    denylist = tmp_path / "denylist.txt"
    denylist.write_text("")
    assert privacy_check.main(["--denylist", str(denylist), str(tmp_path)]) == 2
    assert "directory" in capsys.readouterr().err


def test_cli_exits_1_on_findings_and_0_when_clean(tmp_path):
    denylist = tmp_path / "denylist.txt"
    denylist.write_text("# comment\n\nsecretterm\n")
    dirty = tmp_path / "dirty.txt"
    dirty.write_text("contains SecretTerm here")
    clean = tmp_path / "clean.txt"
    clean.write_text("nothing to see")
    assert privacy_check.main(["--denylist", str(denylist), str(dirty)]) == 1
    assert privacy_check.main(["--denylist", str(denylist), str(clean)]) == 0


# --- staged and history scans must not fail open ------------------------------------

DENY_TERM = "secretterm"


def _git(repo, *args):
    return subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid", *args],
        cwd=repo, check=True, capture_output=True, text=True, timeout=60,
    ).stdout


def _repo(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    denylist = tmp_path / "denylist.txt"
    denylist.write_text(f"{DENY_TERM}\n")
    return repo, denylist


def _run(mode, repo, denylist, monkeypatch, capsys):
    monkeypatch.chdir(repo)
    code = privacy_check.main(["--denylist", str(denylist), mode])
    return code, capsys.readouterr().out


def test_a_staged_binary_file_is_reported(tmp_path, monkeypatch, capsys):
    repo, denylist = _repo(tmp_path)
    (repo / "blob.bin").write_bytes(b"\x00\x01\x02\xff\xfe\x00")
    _git(repo, "add", "blob.bin")
    code, out = _run("--staged", repo, denylist, monkeypatch, capsys)
    assert code == 1
    assert "binary" in out and "blob.bin" in out


def test_text_inside_a_staged_binary_file_is_still_scanned(tmp_path, monkeypatch, capsys):
    repo, denylist = _repo(tmp_path)
    (repo / "blob.bin").write_bytes(b"\x00\x01" + DENY_TERM.encode() + b"\x00\xff")
    _git(repo, "add", "blob.bin")
    code, out = _run("--staged", repo, denylist, monkeypatch, capsys)
    assert code == 1
    assert "denylist" in out and DENY_TERM not in out


def test_a_configured_textconv_does_not_hide_staged_content(tmp_path, monkeypatch, capsys):
    repo, denylist = _repo(tmp_path)
    (repo / ".gitattributes").write_text("*.dat diff=hide\n")
    _git(repo, "config", "diff.hide.textconv", "echo harmless #")
    (repo / "x.dat").write_text(f"{DENY_TERM}\n")
    _git(repo, "add", "x.dat")
    code, out = _run("--staged", repo, denylist, monkeypatch, capsys)
    assert code == 1 and "denylist" in out


def test_a_configured_external_diff_does_not_hide_staged_content(tmp_path, monkeypatch, capsys):
    repo, denylist = _repo(tmp_path)
    _git(repo, "config", "diff.external", "true")
    (repo / "x.txt").write_text(f"{DENY_TERM}\n")
    _git(repo, "add", "x.txt")
    code, out = _run("--staged", repo, denylist, monkeypatch, capsys)
    assert code == 1 and "denylist" in out


def test_a_configured_textconv_does_not_hide_history_content(tmp_path, monkeypatch, capsys):
    repo, denylist = _repo(tmp_path)
    (repo / ".gitattributes").write_text("*.dat diff=hide\n")
    _git(repo, "config", "diff.hide.textconv", "echo harmless #")
    (repo / "x.dat").write_text(f"{DENY_TERM}\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "add")
    code, out = _run("--history", repo, denylist, monkeypatch, capsys)
    assert code == 1 and "denylist" in out


def test_a_binary_file_in_history_is_reported(tmp_path, monkeypatch, capsys):
    repo, denylist = _repo(tmp_path)
    (repo / "blob.bin").write_bytes(b"\x00\x01\x02\xff\xfe\x00")
    _git(repo, "add", "blob.bin")
    _git(repo, "commit", "-q", "-m", "add")
    code, out = _run("--history", repo, denylist, monkeypatch, capsys)
    assert code == 1
    assert "binary" in out and "blob.bin" in out


def test_clean_text_deletions_and_empty_files_are_not_binary_findings(tmp_path, monkeypatch, capsys):
    repo, denylist = _repo(tmp_path)
    (repo / "keep.txt").write_text("fine\n")
    (repo / "old.txt").write_text("fine too\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "add")
    (repo / "old.txt").unlink()
    (repo / "empty.txt").write_text("")
    (repo / "keep.txt").write_text("")
    _git(repo, "add", "-A")
    code, out = _run("--staged", repo, denylist, monkeypatch, capsys)
    assert (code, out) == (0, "")


def test_the_binary_files_differ_line_is_a_finding():
    diff = "diff --git a/x.bin b/x.bin\nindex 1111111..2222222 100644\nBinary files a/x.bin and b/x.bin differ\n"
    found = privacy_check.binary_paths_from_diff(diff, lambda blob: 1)
    assert found == ["a/x.bin and b/x.bin"]


def test_a_changed_file_with_no_hunk_is_a_finding_unless_it_is_empty_or_deleted():
    def section(name, header, index):
        return f"diff --git a/{name} b/{name}\n{header}{index}\n"

    diff = (
        section("opaque.dat", "", "index 1111111..2222222 100644")
        + section("empty.txt", "new file mode 100644\n", "index 0000000..3333333")
        + section("gone.txt", "deleted file mode 100644\n", "index 4444444..0000000")
        + section("mode.sh", "old mode 100644\nnew mode 100755\n", "")
    )
    sizes = {"2222222": 5, "3333333": 0, "0000000": 0}
    assert privacy_check.binary_paths_from_diff(diff, lambda blob: sizes[blob]) == ["opaque.dat"]
