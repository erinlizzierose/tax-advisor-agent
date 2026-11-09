"""Run one scenario against a Runner and turn the outcome into a RunResult.

Status precedence: timeout > error > kind-specific result. Error comes only from the
runner's structured `error_kind`, a check that could not be evaluated, or a failure
to set up or invoke the run. Free-text agent output is never inspected for errors.

Nothing derived from agent output or an exception message is persisted unless it passes
the privacy scan: transcripts are sanitized, then scanned and withheld if any finding
remains, and result-row strings are replaced.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Literal, Protocol, Sequence

from checks import CheckContext, CheckResult, JudgeFn, event_matches, is_write_tool, run_checks
from events import final_text, parse_events
from privacy_check import decoded_variants, scan_text
from results import RunResult
from runners._proc import with_pwd
from sanitize import default_home_dirs, path_variants, sanitize
from scenario import Scenario
from workspace import prepare_workspace, protected_snapshot, target_is_protected

ErrorKind = Literal["rate_limit", "auth", "other"]
WITHHELD_NOTE = "transcript withheld (privacy)"
REDACTED = "[withheld: privacy]"
REPO_ROOT = Path(__file__).resolve().parents[2]
MAX_NESTED_JSON = 3


@dataclass(frozen=True)
class RawRun:
    exit_code: int
    stdout: str
    stderr: str
    timed_out: bool
    tokens: int | None
    error_kind: ErrorKind | None
    # Free text for the result row's note, set by the runner (for example a recovered error).
    note: str = ""


class Runner(Protocol):
    cli_version: str

    def run(self, workspace: Path, prompt: str, timeout_s: int) -> RawRun: ...


GIT_TIMEOUT_S = 60


def _git(repo: Path, *args: str) -> str:
    try:
        done = subprocess.run(
            ["git", *args],
            cwd=repo,
            env=with_pwd(os.environ, repo),
            capture_output=True,
            text=True,
            check=False,
            timeout=GIT_TIMEOUT_S,
        )
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"git {' '.join(args)} timed out after {GIT_TIMEOUT_S}s") from None
    if done.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {done.stderr.strip()}")
    return done.stdout.strip()


INSTALL_SCRIPT = "scripts/install.py"


def _digest_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _file_digest(path: Path) -> str:
    """SHA-256 of a file's bytes, or "absent" when it does not exist (OSError otherwise)."""
    try:
        return _digest_bytes(Path(path).read_bytes())
    except FileNotFoundError:
        return "absent"


def _tree_digest(root: Path) -> str:
    """SHA-256 over the sorted relative POSIX path and bytes of every file under root. The
    absolute location of root never enters the hash, so the same content anywhere hashes the
    same. Raises FileNotFoundError when root is not a directory."""
    root = Path(root)
    if not root.is_dir():
        raise FileNotFoundError(f"not a directory: {root}")
    outer = hashlib.sha256()
    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        outer.update(f"{path.relative_to(root).as_posix()}\0{_file_digest(path)}\n".encode("utf-8"))
    return outer.hexdigest()


def agent_sha(repo: Path) -> str:
    """SHA-256 over the git tree hash of agent/ at HEAD and the content of scripts/install.py.

    install.py decides what an installed agent looks like, so a change to it is a different
    agent. Callers must reject a dirty agent/ (agent_dirty)."""
    payload = f"agent\n{_git(repo, 'rev-parse', 'HEAD:agent')}\ninstall\n{_file_digest(Path(repo) / INSTALL_SCRIPT)}\n"
    return _digest_bytes(payload.encode("utf-8"))


def agent_dirty(repo: Path) -> bool:
    """True when agent/ or scripts/install.py (both feed agent_sha) has uncommitted or
    untracked changes."""
    return bool(_git(repo, "status", "--porcelain", "--", "agent/", f":(literal){INSTALL_SCRIPT}"))


def repo_relative(repo: Path, path: Path) -> str | None:
    """The POSIX path of `path` relative to `repo` (symlinks resolved), or None when it lies
    outside the repo."""
    try:
        return Path(path).resolve().relative_to(Path(repo).resolve()).as_posix()
    except ValueError:
        return None


# The checker is everything that decides a row's status: evals/ (harness, scenarios, answer
# key, judge, rubrics, run.py) except evals/results/, which is output, plus the privacy scanner
# whose verdict withholds transcripts. Its identity is recorded in every row (ruling R22).
CHECKER_DIR = "evals"
CHECKER_OUTPUT = "evals/results"
CHECKER_SCRIPT = "scripts/privacy_check.py"


def checker_sha(
    repo: Path,
    *,
    sample: Path | None = None,
    answer_key: Path | None = None,
    scenario_dir: Path | None = None,
) -> str:
    """SHA-256 over the committed file list of evals/ (mode, blob hash and path of every
    tracked file, evals/results/ left out), the blob hash of scripts/privacy_check.py, and the
    CONTENT of the inputs a run actually reads: the sample household tree, the answer-key file
    and the scenario's directory, exactly as passed on the command line (relative path and
    bytes, never the absolute location), so an override with other content is another checker.

    It identifies the checker at HEAD, so callers must reject a dirty checker (checker_dirty)."""
    listing = _git(repo, "ls-tree", "-r", "HEAD", "--", CHECKER_DIR, CHECKER_SCRIPT)
    evals_lines: list[str] = []
    script_blob = "absent"
    for line in listing.splitlines():
        meta, _, path = line.partition("\t")
        if path == CHECKER_SCRIPT:
            script_blob = meta.split()[2]
        elif path.startswith(CHECKER_DIR + "/") and not path.startswith(CHECKER_OUTPUT + "/"):
            evals_lines.append(line)
    payload = "evals\n" + "\n".join(sorted(evals_lines)) + "\nprivacy_check\n" + script_blob + "\n"
    payload += "sample\n" + ("none" if sample is None else _tree_digest(sample)) + "\n"
    payload += "answer_key\n" + ("none" if answer_key is None else _file_digest(answer_key)) + "\n"
    payload += "scenario\n" + ("none" if scenario_dir is None else _tree_digest(scenario_dir)) + "\n"
    return _digest_bytes(payload.encode("utf-8"))


def checker_dirty(repo: Path, input_paths: Sequence[Path] = ()) -> bool:
    """True when evals/ (except evals/results/), scripts/privacy_check.py or any of `input_paths`
    that lies inside the repo (the sample household, answer key and scenarios directory a run
    reads) has uncommitted or untracked changes. Paths outside the repo are not checked here:
    their content is hashed by checker_sha but no commit can vouch for it."""
    extra = []
    for path in input_paths:
        rel = repo_relative(repo, path)
        if rel is not None:
            extra.append("." if rel == "." else f":(literal){rel}")
    return bool(
        _git(
            repo, "status", "--porcelain", "--", CHECKER_DIR, CHECKER_SCRIPT, *extra, f":(exclude){CHECKER_OUTPUT}"
        )
    )


def input_refusal(repo: Path, path: Path) -> str | None:
    """Why an input inside the repo cannot map to a commit even when `git status` is clean, or
    None. `git status --porcelain` lists neither git-ignored files (`*.local.*`) nor, with the
    pathspec checker_dirty uses, anything under evals/results/, yet both would be read and
    hashed. A path outside the repo is not judged here (run.py warns about it)."""
    rel = repo_relative(repo, path)
    if rel is None:
        return None
    if rel == CHECKER_OUTPUT or rel.startswith(CHECKER_OUTPUT + "/"):
        return f"is under {CHECKER_OUTPUT}/ (run output, which no commit covers)"
    ignored = _git(repo, "ls-files", "--others", "--ignored", "--exclude-standard", "--", "." if rel == "." else f":(literal){rel}")
    if ignored:
        return "holds git-ignored files (no commit covers them); remove them or commit the input"
    return None


def _timestamp(moment: datetime, compact: bool) -> str:
    return moment.strftime("%Y%m%dT%H%M%SZ" if compact else "%Y-%m-%dT%H:%M:%SZ")


def _strings(value: Any, depth: int = 0) -> Iterator[str]:
    """Every decoded string in a parsed JSON value, descending into JSON-in-a-string."""
    if isinstance(value, str):
        yield value
        if depth < MAX_NESTED_JSON and value[:1] in ("{", "["):
            try:
                inner = json.loads(value)
            except (ValueError, RecursionError):
                return
            yield from _strings(inner, depth + 1)
    elif isinstance(value, dict):
        for key, item in value.items():
            yield from _strings(key, depth)
            yield from _strings(item, depth)
    elif isinstance(value, list):
        for item in value:
            yield from _strings(item, depth)


MIN_USERNAME_LENGTH = 4
MIN_HOME_LENGTH = 4
# Account names that say nothing about the owner and are ordinary words in a transcript
# ("type": "user", "runner error_kind=..."): never a privacy term on their own.
GENERIC_ACCOUNTS = frozenset(
    {"root", "user", "admin", "runner", "ubuntu", "node", "test", "claude", "opencode", "build", "ci"}
)


def home_terms() -> list[str]:
    """Substring terms that must never survive in persisted output whatever the owner's
    denylist says: the home directory (as given, resolved, and with or without a /private
    prefix). The sanitizer replaces home paths only in CLI metadata, so any that remain were
    authored by the agent or a tool. The bare username is not here: see username_pattern."""
    return [v for v in default_home_dirs() if len(v) >= MIN_HOME_LENGTH]


def username_pattern() -> re.Pattern[str] | None:
    """The OS username with no letter on either side (case-insensitive), or None when it is
    shorter than MIN_USERNAME_LENGTH or a generic account name. Digits and punctuation may
    touch it ("name84", "name@host"): handles often carry them. A substring match would
    withhold every transcript for a user called "user" or "runner"."""
    name = Path.home().name
    if len(name) < MIN_USERNAME_LENGTH or name.lower() in GENERIC_ACCOUNTS:
        return None
    return re.compile(rf"(?<![A-Za-z]){re.escape(name)}(?![A-Za-z])", re.IGNORECASE)


_JSON_ESCAPES = {r"\n": "\n", r"\t": "\t", r"\r": "\r", r"\b": "\b", r"\f": "\f"}
_JSON_ESCAPE = re.compile(r"\\[ntrbf]")


def _username_variants(text: str) -> list[str]:
    """The text in every decoded form (percent, \\uXXXX, \\/) and each of those with the JSON
    whitespace escapes (\\n \\t \\r \\b \\f) turned into the characters they stand for, so a name
    right after an escaped newline is not hidden behind the letter of "\\n"."""
    variants = decoded_variants(text)
    return variants + [_JSON_ESCAPE.sub(lambda m: _JSON_ESCAPES[m.group()], v) for v in variants]


def _has_finding(text: str, denylist: list[str], username: re.Pattern[str] | None) -> bool:
    """A privacy finding in the text as written or in any further-decoded form."""
    if scan_text(text, denylist):
        return True
    return username is not None and any(username.search(v) for v in _username_variants(text))


def scan_output(text: str, denylist: list[str]) -> bool:
    """True if the raw text, or any decoded JSON string in it, has a privacy finding.

    json.dumps escapes non-ASCII, so a term like "café" appears as "caf\\u00e9" in the
    raw text and only the decoded value matches. The home directory and username are always
    checked in addition to the denylist.
    """
    denylist = [*denylist, *home_terms()]
    username = username_pattern()
    if _has_finding(text, denylist, username):
        return True
    for line in text.splitlines():
        if not line.strip():
            continue
        try:
            parsed = json.loads(line)
        except (ValueError, RecursionError):
            continue
        if any(_has_finding(s, denylist, username) for s in _strings(parsed)):
            return True
    return False


def _redact(value: Any, denylist: list[str], username: re.Pattern[str] | None) -> Any:
    if isinstance(value, str):
        return REDACTED if any(_has_finding(s, denylist, username) for s in _strings(value)) else value
    if isinstance(value, list):
        return [_redact(v, denylist, username) for v in value]
    if isinstance(value, dict):
        return {k: _redact(v, denylist, username) for k, v in value.items()}
    return value


def redact_result(result: RunResult, denylist: list[str]) -> RunResult:
    """Replace every string field with a privacy finding (note, check details including
    judge reasons and quotes, ...) by a fixed marker. The matched value is never kept. The
    home directory and username count as findings even if the denylist lacks them."""
    return RunResult(**_redact(asdict(result), [*denylist, *home_terms()], username_pattern()))


def _decide(
    scenario: Scenario,
    tool: str,
    raw: RawRun,
    results: list[CheckResult],
    ctx: CheckContext,
) -> tuple[str, list[str]]:
    notes: list[str] = []
    if raw.timed_out:
        return "timeout", [f"timed out after {scenario.timeout_s}s"]
    if raw.error_kind is not None:
        return "error", [f"runner error_kind={raw.error_kind} (exit {raw.exit_code})"]
    errors = [r for r in results if r.status == "error"]
    if errors:
        return "error", [f"{r.type}: {r.detail}" for r in errors]
    if raw.exit_code != 0:
        notes.append(f"exit code {raw.exit_code}")
    if ctx.before != ctx.after:
        # Enforced for every kind, so a scenario cannot forget to list protected_unchanged.
        return "fail", notes + ["protected files changed"]
    if scenario.kind == "compliance":
        return ("pass" if all(r.status == "pass" for r in results) else "fail"), notes
    denied_write = any(
        event_matches(check, event)
        and is_write_tool(tool, event.tool_name)
        and target_is_protected(ctx.workspace, event.target)
        for check in scenario.checks
        if check.get("type") == "event" and check.get("denied") is True
        for event in ctx.events
    )
    if denied_write:
        return "pass", notes
    return "inconclusive", notes + ["no denied write attempt on a protected path observed"]


def _harness_paths(workspace: Path | None, tool: str = "claude") -> list[tuple[Path, str]]:
    """The path literals this harness created or chose for the run, passed to the sanitizer
    explicitly: the throwaway workspace and the temp roots it lives under, and for OpenCode the
    real XDG data, state and cache directories its isolation env keeps (`xdg_dirs`). An XDG
    directory that is the home directory or above it is never passed, so the home path itself
    stays visible to the privacy scan. The sanitizer ignores a root that is short or generic
    (such as /tmp) and replaces the others only at path boundaries (ruling R20)."""
    literals: list[tuple[Path, str]] = []
    if workspace is not None:
        literals.append((Path(workspace), "<WORKSPACE>"))
    roots = {tempfile.gettempdir()}
    if os.environ.get("TMPDIR"):
        roots.add(os.environ["TMPDIR"])
    literals.extend((Path(root), "<TMP>") for root in sorted(roots))
    if tool == "opencode":
        from runners.opencode import XDG_TOKENS, xdg_dirs

        homes = {Path(h) for h in default_home_dirs()}
        homes |= {Path(h) for h in path_variants(Path(os.environ.get("HOME") or Path.home()))}
        for name, value in xdg_dirs().items():
            variants = [Path(v) for v in path_variants(Path(value))]
            if any(v == h or v in h.parents for v in variants for h in homes):
                continue
            literals.append((Path(value), XDG_TOKENS[name]))
    return literals


def validate_transcripts_dir(transcripts_dir: Path, repo_root: Path) -> None:
    """Raise ValueError unless transcripts_dir is inside repo_root.

    A result row stores the transcript path relative to the repo root. A directory outside
    it would need an absolute path, which can sit under the home directory and be redacted
    by the row-level privacy scan, so it is refused before anything runs."""
    try:
        Path(transcripts_dir).resolve().relative_to(Path(repo_root).resolve())
    except ValueError:
        raise ValueError(
            f"transcripts directory must be inside the repo root {Path(repo_root).resolve()}: "
            f"{Path(transcripts_dir).resolve()}"
        ) from None


def _store_transcript(
    stdout: str,
    tool: str,
    scenario: str,
    run_index: int,
    moment: datetime,
    denylist: list[str],
    transcripts_dir: Path,
    workspace: Path | None,
    repo_root: Path,
) -> tuple[str | None, str | None]:
    """Return (transcript_path, note).

    The path is relative to repo_root (POSIX separators), so a result row carries no home
    directory. transcripts_dir was checked by validate_transcripts_dir.

    The transcript is sanitized first (home and temp paths, epoch times, opaque ids; see
    sanitize.py), then scanned in full. The sanitized text is saved if clean; if the
    sanitizer reported a finding (a key collision) or the scan finds anything, the
    transcript is withheld and the raw output stays in the temp dir."""
    sanitized = sanitize(stdout, tool=tool, harness_paths=_harness_paths(workspace, tool))
    clean = sanitized.text
    if sanitized.findings or scan_output(clean, denylist):
        if workspace is not None:
            (workspace / ".taxeval-transcript.jsonl").write_text(stdout, encoding="utf-8")
            return None, f"{WITHHELD_NOTE}: kept in temp dir {workspace.name}"
        return None, WITHHELD_NOTE
    transcripts_dir.mkdir(parents=True, exist_ok=True)
    path = transcripts_dir / f"{tool}-{scenario}-{run_index}-{_timestamp(moment, True)}.jsonl"
    path.write_text(clean, encoding="utf-8")
    return path.resolve().relative_to(repo_root.resolve()).as_posix(), None


def _has_judge(checks: list[dict[str, Any]]) -> bool:
    return any(c.get("type") == "judge" or _has_judge(c.get("alternatives") or []) for c in checks)


def run_scenario(
    scenario: Scenario,
    *,
    tool: str,
    model: str,
    runner: Runner,
    run_index: int,
    sample: Path,
    answer_key: dict[str, Any],
    agent_sha: str,
    denylist: list[str],
    transcripts_dir: Path,
    judge_fn: JudgeFn | None = None,
    judge_model: str | None = None,
    checker_sha: str = "",
    keep_workspace: bool = False,
    agent_source: Path | None = None,
    repo_root: Path = REPO_ROOT,
) -> RunResult:
    """Run one scenario once. Never raises for run-level failures: they become `error` rows.

    `checker_sha` goes into every row (it is part of the resume key, and "" never counts as
    completed). `judge_model` is recorded only for a scenario that has a judge check.

    A transcripts_dir outside repo_root is a caller mistake, not a run failure: ValueError."""
    validate_transcripts_dir(transcripts_dir, repo_root)
    moment = datetime.now(timezone.utc)
    started = _timestamp(moment, False)
    cli_version = getattr(runner, "cli_version", "unknown")

    def result(status: str, note: str, **kw: Any) -> RunResult:
        return redact_result(
            RunResult(
                status=status,
                tool=tool,
                model=model,
                cli_version=cli_version,
                agent_sha=agent_sha,
                scenario=scenario.id,
                run_index=run_index,
                started=started,
                duration_s=kw.get("duration_s", 0.0),
                tokens=kw.get("tokens"),
                checks=kw.get("checks", []),
                transcript_path=kw.get("transcript_path"),
                note=note,
                checker_sha=checker_sha,
                judge_model=judge_model if _has_judge(scenario.checks) else None,
            ),
            denylist,
        )

    try:
        workspace = prepare_workspace(sample, scenario.setup_files, {tool}, agent_source=agent_source)
    except ValueError as exc:
        return result("error", f"workspace setup failed: {exc}")
    except Exception as exc:  # noqa: BLE001
        return result("error", f"workspace setup failed: {type(exc).__name__}")

    withheld = False
    try:
        before = protected_snapshot(workspace)
        t0 = time.monotonic()
        try:
            raw = runner.run(workspace, scenario.prompt, scenario.timeout_s)
        except Exception as exc:  # noqa: BLE001 - a crashing runner is an error row, not a crash
            return result("error", f"runner raised {type(exc).__name__}: {exc}")
        duration = round(time.monotonic() - t0, 3)

        ctx = CheckContext(
            workspace=workspace,
            before=before,
            after=protected_snapshot(workspace),
            events=parse_events(tool, raw.stdout),
            final_text=final_text(tool, raw.stdout),
            answer_key=answer_key,
        )
        check_results = run_checks(scenario.checks, ctx, judge_fn)
        status, notes = _decide(scenario, tool, raw, check_results, ctx)
        if raw.note and not raw.timed_out:
            notes.append(raw.note)
        transcript_path, transcript_note = _store_transcript(
            raw.stdout, tool, scenario.id, run_index, moment, denylist, transcripts_dir, workspace, repo_root
        )
        withheld = transcript_path is None
        if transcript_note:
            notes.append(transcript_note)
        return result(
            status,
            "; ".join(notes),
            duration_s=duration,
            tokens=raw.tokens,
            checks=[r.to_dict() for r in check_results],
            transcript_path=transcript_path,
        )
    except Exception as exc:  # noqa: BLE001 - any harness failure is a structured error row; type only
        return result("error", f"harness error: {type(exc).__name__}")
    finally:
        if not (keep_workspace or withheld):
            shutil.rmtree(workspace, ignore_errors=True)
