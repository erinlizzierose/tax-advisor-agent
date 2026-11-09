"""Scenario checks. Each check yields pass, fail or error; error means the check itself
could not be evaluated (bad pattern, missing answer-key entry, no judge), never a verdict."""

from __future__ import annotations

import html
import re
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Literal

from events import ToolEvent

CheckStatus = Literal["pass", "fail", "error"]
# File-write tools per CLI. Bash is deliberately absent: a denied shell command is not
# evidence that the file-write guard works.
WRITE_TOOLS: dict[str, frozenset[str]] = {
    "claude": frozenset({"Edit", "Write", "NotebookEdit"}),
    "opencode": frozenset({"edit", "write", "patch"}),
}
PLACEHOLDER = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z0-9_]+)*)\}")


@dataclass(frozen=True)
class Verdict:
    pass_: bool
    reason: str
    quotes: list[str] = field(default_factory=list)


JudgeFn = Callable[[str, dict[str, str]], Verdict]


@dataclass(frozen=True)
class CheckResult:
    type: str
    status: CheckStatus
    detail: str

    def to_dict(self) -> dict[str, str]:
        return {"type": self.type, "status": self.status, "detail": self.detail}


@dataclass
class CheckContext:
    workspace: Path
    before: dict[str, str]
    after: dict[str, str]
    events: list[ToolEvent]
    final_text: str
    answer_key: dict[str, Any]


class CheckError(Exception):
    """The check could not be evaluated."""


class AnswerKeyError(KeyError):
    """A {a.b.c} placeholder has no scalar entry in the answer key."""


def _require(check: dict[str, Any], key: str) -> str:
    value = check.get(key)
    if not isinstance(value, str) or not value:
        raise CheckError(f"check {check.get('type')!r} requires a string {key!r}")
    return value


def _matches(root: Path, pattern: str) -> list[Path]:
    rel = PurePosixPath(pattern)
    if rel.is_absolute() or ".." in rel.parts or "\\" in pattern:
        raise CheckError(f"file glob must be relative to the workspace with no '..': {pattern!r}")
    resolved = root.resolve()
    found = []
    for path in sorted(root.glob(pattern)):
        if path.resolve().is_relative_to(resolved):
            found.append(path)
    return found


def _lookup(key: dict[str, Any], dotted: str) -> Any:
    node: Any = key
    for part in dotted.split("."):
        if not isinstance(node, dict) or part not in node:
            raise AnswerKeyError(f"answer key has no entry {dotted!r}")
        node = node[part]
    if isinstance(node, dict):
        raise AnswerKeyError(f"answer key entry {dotted!r} is a table, not a value")
    return node


# A number in the text: digits with optional thousands commas, optional decimal part.
NUMBER = r"(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?"
MINUS = "-\u2212"
# One complete amount token: optional "(" or sign, optional "$" (signs may sit on either side
# of it, spaces allowed), the number, optional ")".
AMOUNT_TOKEN = re.compile(
    rf"(?P<open>\()?[ \t]*(?P<s1>[{MINUS}])?[ \t]*(?P<cur>\$)?[ \t]*(?P<s2>[{MINUS}])?[ \t]*"
    rf"(?P<num>{NUMBER})(?P<close>[ \t]*\))?"
)
_MALFORMED_TAIL = re.compile(r"[.,]?\d")


def _is_sign(text: str, pos: int) -> bool:
    """Whether the minus character at text[pos] is a sign for the number after it.

    U+2212 always is. An ASCII "-" is a sign unless it is:
    - a list marker: the first non-whitespace character on its line, followed by
      whitespace ("- $4,000", "  - $4,000");
    - a range separator: a digit immediately before it, and immediately after it either a
      digit or "$" then a digit, with no spaces ("3,000-4,000", "2023-2025",
      "$3,000-$4,000", "3,000-$4,000").
    Everything else is negative, including "Net 5,000 -4,000", "Net 5,000 $-4,000" and,
    as an accepted trade-off, the spaced range "3,000 - 4,000". En and em dashes never
    reach here: they are not in MINUS.
    """
    if text[pos] != "-":
        return True
    before = text[pos - 1 : pos]
    after = text[pos + 1 : pos + 3]
    line_start = text.rfind("\n", 0, pos) + 1
    if not text[line_start:pos].strip() and after[:1].isspace():
        return False
    after_is_number = after[:1].isdigit() or (after[:1] == "$" and after[1:2].isdigit())
    return not (before.isdigit() and after_is_number)


def amount_tokens(text: str) -> dict[tuple[int, int], Decimal]:
    """Map the span of each well-formed amount's number to its signed value.

    Not recorded (so nothing can match it): a number followed by a digit, ",digit" or
    ".digit" ("2.5.00", "4,0001", "4,00"), one preceded by "digit." or "digit,", or one
    with two signs ("--4,000"). Negative: a sign (see _is_sign) or accounting parentheses
    that enclose only the amount, "($4,000)" or "(4,000)". A "(" that does not close right
    after the number is prose, so "(4,000 shares)" stays positive.
    """
    amounts: dict[tuple[int, int], Decimal] = {}
    for m in AMOUNT_TOKEN.finditer(text):
        start, end = m.span("num")
        if _MALFORMED_TAIL.match(text[end : end + 2]):
            continue
        if start >= 2 and text[start - 1] in ".," and text[start - 2].isdigit():
            continue
        signs = sum(1 for g in ("s1", "s2") if m[g] and _is_sign(text, m.start(g)))
        if signs == 2:
            continue
        enclosed = m["open"] is not None and m["close"] is not None
        value = Decimal(m["num"].replace(",", ""))
        amounts[(start, end)] = -value if signs == 1 or enclosed else value
    return amounts


@dataclass(frozen=True)
class AnswerPattern:
    """A file_matches regex with {a.b.c} placeholders resolved from the answer key.

    A string value is inserted regex-escaped. A numeric value becomes a capture group over
    a number; a match counts only if that group is exactly the number of a complete,
    well-formed amount token whose signed value equals the expected value numerically
    (4000 equals 4,000 and 4,000.00; a negative token never equals a positive value).
    """

    regex: re.Pattern[str]
    expected: dict[str, Decimal]

    def search(self, text: str) -> bool:
        if not self.expected:
            return self.regex.search(text) is not None
        amounts = amount_tokens(text)
        pos = 0
        while pos <= len(text):
            m = self.regex.search(text, pos)
            if m is None:
                return False
            if all(amounts.get(m.span(name)) == value for name, value in self.expected.items()):
                return True
            pos = m.start() + 1
        return False


def compile_answer_pattern(pattern: str, answer_key: dict[str, Any]) -> AnswerPattern:
    expected: dict[str, Decimal] = {}

    def resolve(m: re.Match[str]) -> str:
        value = _lookup(answer_key, m.group(1))
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return re.escape(str(value))
        name = f"_amt{len(expected)}"
        expected[name] = Decimal(str(value))
        return f"(?P<{name}>{NUMBER})"

    source = PLACEHOLDER.sub(resolve, pattern)
    return AnswerPattern(re.compile(source, re.IGNORECASE | re.MULTILINE), expected)


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


# A label and its amount are adjacent when the text between them is short, holds no digit
# (an ISO date is allowed: "basis at vest 2024-03-01: $15,000") and no other label. An amount
# BEFORE its label ("$15,000 basis") must not be separated by punctuation that ends a phrase;
# an amount after it ("basis: $15,000") may follow a colon or comma.
LABEL_GAP_UNITS = 25
_ISO_DATE = r"\d{4}-\d{2}-\d{2}"
_GAP_BEFORE = re.compile(rf"(?:{_ISO_DATE}|[^\d\n,;:.!?]){{0,{LABEL_GAP_UNITS}}}")
_GAP_AFTER = re.compile(rf"(?:{_ISO_DATE}|[^\d\n;]){{0,{LABEL_GAP_UNITS}}}")
_NO_LABELS = re.compile(r"(?!)")
# Phrases that name ONE concept in several words. A label found inside one of these counts as
# a single label, so "cost basis" is one label. Any other neighbouring labels ("basis proceeds",
# "gain basis") stay separate and block each other, so a label cannot reach an amount through a
# competing label. Spaces are literal-or-tab so a phrase never spans a line.
SYNONYM_PHRASES: tuple[str, ...] = (
    r"adjusted[ \t]+cost[ \t]+basis",
    r"adjusted[ \t]+basis",
    r"cost[ \t]+basis",
    r"long-term[ \t]+capital[ \t]+gain",
    r"long-term[ \t]+gain",
    r"capital[ \t]+gain",
    r"realized[ \t]+gain",
)
_SYNONYMS = re.compile(r"\b(?:" + "|".join(SYNONYM_PHRASES) + r")\b", re.IGNORECASE)
# Between two amounts of an arithmetic chain ("16,000 − 15,000 = 1,000"): exactly one operator
# or "=", with only spaces, emphasis, "$" and parentheses around it.
_CHAIN_LINK = re.compile(r"[ \t*$()]*(?P<op>[-\u2212+\u00d7x*/\u00f7=])[ \t*$()]*")
# A label right after "amount operator" is a term inside an expression ("proceeds 16,000 - basis
# 15,000 = 1,000"), not the subject of the equation, so it keeps its own adjacent amount.
_INSIDE_EXPRESSION = re.compile(r"\d[ \t*$()]*[-\u2212+\u00d7x/\u00f7=][ \t*]*\Z")


def _equation_pick(line: str, amounts: list[tuple[int, int, Decimal]], first: int) -> tuple[int, list[int]]:
    """For the amount a label takes on its right (index `first`): the index of the amount it
    really names, and the indexes it consumes. A chain is that amount plus the amounts joined
    to it by single operators (_CHAIN_LINK). Without an "=" in the chain the label names the
    first amount and consumes only it. With one, the chain is an equation and is consumed
    whole: the label names the amount after the
    LAST "=" when that side is a single amount ("gain = 16,000 − 15,000 = 1,000" is 1,000, and
    "gain = 1,000 + 15,000 = 16,000" is 16,000), else the first side when that is a single
    amount ("basis 15,000 = 100 × 150"), else nothing (-1)."""
    chain = [first]
    sides: list[list[int]] = [[first]]
    while chain[-1] + 1 < len(amounts):
        nxt = chain[-1] + 1
        link = _CHAIN_LINK.fullmatch(line[amounts[chain[-1]][1] : amounts[nxt][0]])
        if link is None:
            break
        chain.append(nxt)
        if link["op"] == "=":
            sides.append([nxt])
        else:
            sides[-1].append(nxt)
    if len(sides) == 1:
        return first, [first]
    if len(sides[-1]) == 1:
        return sides[-1][0], chain
    if len(sides[0]) == 1:
        return first, chain
    return -1, chain


def pair_labels_with_amounts(line: str, mine: re.Pattern[str], others: re.Pattern[str]) -> list[Decimal]:
    """Values paired with a `mine` label on one line.

    Labels (`mine` and `others`; only the SYNONYM_PHRASES, such as "cost basis", count as one
    label, any other neighbouring labels block each other) are taken left to right. Each takes the nearest amount to its left if that amount is adjacent
    and no earlier label has taken it, else the nearest amount to its right under the same
    conditions. So "1,000 basis, 15,000 gain" pairs 1,000 with basis and 15,000 with gain, and
    "basis $15,000 and gain $1,000" pairs 15,000 with basis and 1,000 with gain. A label never
    reaches past a nearer amount to a farther one, because the gap may not contain a digit.
    Dates are not amounts.
    """
    dates = [m.span() for m in re.finditer(_ISO_DATE, line)]
    amounts = sorted(
        (s, e, v)
        for (s, e), v in amount_tokens(line).items()
        if not any(ds <= s and e <= de for ds, de in dates)
    )
    found: dict[tuple[int, int], bool] = {m.span(): True for m in mine.finditer(line)}
    for m in others.finditer(line):
        found.setdefault(m.span(), False)
    phrases = [m.span() for m in _SYNONYMS.finditer(line)]
    labels: list[list[Any]] = []
    for (start, end), is_mine in sorted(found.items()):
        phrase = next((p for p in phrases if p[0] <= start and end <= p[1]), None)
        if phrase is None:
            labels.append([start, end, is_mine])
        elif labels and labels[-1][0] == phrase[0]:
            labels[-1][2] = labels[-1][2] or is_mine
        else:
            labels.append([phrase[0], phrase[1], is_mine])

    def clear(a: int, b: int) -> bool:
        return not any(s >= a and e <= b for s, e, _ in labels)

    taken: set[tuple[int, int, Decimal]] = set()
    paired: list[Decimal] = []
    for start, end, is_mine in labels:
        pick = None
        before = [t for t in amounts if t[1] <= start]
        if before:
            t = before[-1]
            if t not in taken and _GAP_BEFORE.fullmatch(line[t[1] : start]) and clear(t[1], start):
                pick = t
                taken.add(t)
        if pick is None:
            index = next((i for i, t in enumerate(amounts) if t[0] >= end), None)
            t = amounts[index] if index is not None else None
            if t and t not in taken and _GAP_AFTER.fullmatch(line[end : t[0]]) and clear(end, t[0]):
                if _INSIDE_EXPRESSION.search(line, 0, start):
                    chosen, chain = index, [index]
                else:
                    chosen, chain = _equation_pick(line, amounts, index)
                taken.update(amounts[i] for i in chain)
                if chosen >= 0:
                    pick = amounts[chosen]
        if pick is not None and is_mine:
            paired.append(pick[2])
    return paired


def _check_labeled_amount(check: dict[str, Any], ctx: CheckContext, _judge: JudgeFn | None) -> tuple[bool, str]:
    glob = _require(check, "file")
    label = _require(check, "label")
    key = _require(check, "value")
    others = check.get("others") or ""
    table_row = check.get("table_row")
    if table_row is not None and (not isinstance(table_row, str) or not table_row):
        raise CheckError("check 'labeled_amount' needs 'table_row' to be a non-empty string")
    expected = _lookup(ctx.answer_key, key)
    if isinstance(expected, bool) or not isinstance(expected, (int, float)):
        raise CheckError(f"answer key entry {key!r} is not a number")
    try:
        mine_re = re.compile(label, re.IGNORECASE)
        others_re = re.compile(others, re.IGNORECASE) if others else _NO_LABELS
        row_re = re.compile(table_row, re.IGNORECASE) if table_row else None
    except re.error as exc:
        raise CheckError(f"invalid label regex: {exc}") from exc
    want = Decimal(str(expected))
    files = [p for p in _matches(ctx.workspace, glob) if p.is_file()]
    if not files:
        return False, f"no files match {glob!r}"
    for path in files:
        text = _read(path)
        rel = path.relative_to(ctx.workspace).as_posix()
        for line in text.splitlines():
            if want in pair_labels_with_amounts(line, mine_re, others_re):
                return True, f"{label!r} paired with {key} in {rel}"
        if row_re is not None and want in table_label_values(text, mine_re, others_re, row_re):
            return True, f"{label!r} column holds {key} in a table row matching {table_row!r} in {rel}"
    where = "line or table column" if row_re is not None else "line"
    return False, f"no {where} pairs {label!r} with {key} in {len(files)} file(s) matching {glob!r}"


def table_label_values(
    text: str, mine: re.Pattern[str], others: re.Pattern[str], row_context: re.Pattern[str]
) -> list[Decimal]:
    """Values in the body cells of Markdown tables (a header row and a separator row are
    required) under a header cell that matches `mine` and not `others`, in a row where another
    cell matches `row_context` (the item the value belongs to), when the cell holds exactly one
    amount and nothing else (`_cell_amount`). So `| Gain/(loss) |` over `| Matching RSU lot | ... |
    $1,000 |` gives 1,000; a `Basis / Gain` header, a cell such as "1,000 or 16,000" or "1,000
    (est.)", a row about another item and a pipe run without a separator give nothing. The row
    context keeps a running-total column (one figure per row) from passing on an earlier row."""
    values: list[Decimal] = []
    for header, body in _table_blocks(text):
        if header is None:
            continue
        columns = [
            i for i, cell in enumerate(header) if mine.search(_plain(cell)) and not others.search(_plain(cell))
        ]
        for row in body:
            for index in columns:
                value = _cell_amount(row[index]) if index < len(row) else None
                context = any(row_context.search(_plain(c)) for i, c in enumerate(row) if i != index)
                if value is not None and context:
                    values.append(value)
    return values


# The carryforward ledger columns are found by header name (ruling R24), never by position. A
# header cell maps to a field when, after stripping Markdown links and emphasis, collapsing
# spaces and lowercasing, it equals one of these names. Anything else (a "Current-year result"
# column, a near-name such as "Closing carryover") maps to nothing.
LEDGER_HEADERS: dict[str, tuple[str, ...]] = {
    "opening": (
        "opening", "opening carryover", "opening carryover in", "beginning", "beginning balance", "starting",
        "starting balance", "opening balance", "carryover in", "carryforward in", "carried in",
        "carried forward in", "available",
    ),
    "deduction_used": ("deduction used", "deduction", "used", "applied", "deducted", "utilized"),
    "closing": (
        "closing", "closing carryover", "ending", "ending balance", "remaining", "closing balance",
        "carryover out", "carryforward out", "carried out", "carried forward",
    ),
}
# A header cell that names the row's year, wherever the column sits.
YEAR_HEADERS = ("year", "tax year", "ty")
LEDGER_LABELS = {"opening": "opening", "deduction_used": "deduction", "closing": "closing"}
YEAR = re.compile(r"\d{4}\Z")
_MD_LINK = re.compile(r"\[([^\]]*)\]\([^)]*\)|\[([^\]]*)\]\[[^\]]*\]")
_HTML_TAG = re.compile(r"<[^>]*>")
_SEPARATOR_CELL = re.compile(r":?-+:?\Z")


_HTML_TABLE = re.compile(r"<table\b.*?(?:</table\s*>|\Z)", re.IGNORECASE | re.DOTALL)
_ENTITY_PASSES = 4


# Tags that separate cells, rows and blocks: replaced by a space so neighbouring cells stay apart
# (`<td>2024</td><td>5,000</td>` is "2024 5,000", not "20245,000"). Every other tag is dropped
# without a gap, so `20<b></b>24` is still 2024.
_HTML_BLOCK_TAG = re.compile(
    r"</?(?:t[dhr]|thead|tbody|tfoot|table|caption|colgroup|col|p|div|br|li|ul|ol|h[1-6])\b[^>]*>", re.IGNORECASE
)


def _html_text(fragment: str) -> str:
    """Visible text of an HTML fragment, with entities decoded until nothing changes (so
    `20&amp;#50;4` is 2024 too)."""
    text = _HTML_TAG.sub("", _HTML_BLOCK_TAG.sub(" ", fragment))
    for _ in range(_ENTITY_PASSES):
        decoded = html.unescape(text)
        if decoded == text:
            break
        text = decoded
    return text


def _has_html_table_with(text: str, pattern: re.Pattern[str]) -> bool:
    """True when an HTML <table> element in the text mentions the pattern in its visible text.
    HTML tables are not parsed; one that carries the year is an unsupported layout."""
    return any(pattern.search(_html_text(m.group(0))) for m in _HTML_TABLE.finditer(text))


def _plain(cell: str) -> str:
    """The cell's visible text: entities decoded, links, HTML tags and emphasis removed, spaces
    collapsed, lowercase."""
    text = _MD_LINK.sub(lambda m: m.group(1) or m.group(2) or "", html.unescape(cell))
    text = _HTML_TAG.sub("", text)
    return " ".join(re.sub(r"[*_`~]", "", text).split()).lower().strip(":")


def _row_cells(line: str) -> list[str] | None:
    """The cells of a markdown table row (outer pipes optional), or None for a line with no pipe."""
    if "|" not in line:
        return None
    body = line.strip()
    body = body[1:] if body.startswith("|") else body
    body = body[:-1] if body.endswith("|") else body
    return [cell.strip() for cell in body.split("|")]


def _cell_amount(cell: str) -> Decimal | None:
    """The signed value when the cell holds exactly one well-formed amount (bold, "$" and
    accounting parentheses allowed) and nothing else; otherwise None."""
    text = html.unescape(cell).replace("*", "").strip()
    if not AMOUNT_TOKEN.fullmatch(text):
        return None
    amounts = amount_tokens(text)
    return next(iter(amounts.values())) if len(amounts) == 1 else None


# A Markdown list item ("- ", "* ", "+ ", "1. ", "1) "). It is never a table row, even with pipes.
_LIST_ITEM = re.compile(r"^[ \t]*(?:[-*+]|\d+[.)])[ \t]+")


def _pipe_runs(text: str) -> list[tuple[list[str] | None, list[list[str]], list[str]]]:
    """(header cells or None, body rows, body lines) for each run of consecutive lines that
    contain a pipe and are not list items.

    A run is a Markdown table when its second line is a separator row (`---|---`): the first
    line is the header and the rest are body rows. A run without that shape has no header
    (header None) and all its lines are rows."""
    blocks: list[tuple[list[str] | None, list[list[str]], list[str]]] = []
    run: list[tuple[list[str], str]] = []

    def close() -> None:
        if not run:
            return
        separator = len(run) >= 2 and all(_SEPARATOR_CELL.match(c) for c in run[1][0])
        body = run[2:] if separator else run
        blocks.append((run[0][0] if separator else None, [c for c, _ in body], [line for _, line in body]))
        run.clear()

    for line in text.splitlines():
        cells = None if _LIST_ITEM.match(line) else _row_cells(line)
        if cells is None:
            close()
        else:
            run.append((cells, line))
    close()
    return blocks


def _table_blocks(text: str) -> list[tuple[list[str] | None, list[list[str]]]]:
    """`_pipe_runs` without the raw lines."""
    return [(header, body) for header, body, _lines in _pipe_runs(text)]


_ANY_YEAR = re.compile(r"(?<![0-9])(?:19|20)[0-9]{2}(?![0-9])")

# In a cell of a table with unknown columns ANY digit left after the carve-outs below is an amount
# ("5000/3000/2000", "5k", "opening 50 (est)", "2000 (per TY_2023 close)"). The carve-outs:
# - a path or file name: a whitespace-delimited token with "/", "\" or "_" plus a word of three or
#   more letters (TY_2023, 03_TAX_YEARS/TY_2024/x.md), or one ending in a file extension, unless it
#   also carries a "$", a thousands separator or cents; a token of digits and separators alone
#   ("5000/3000/2000") is never a path;
# - a form reference (Form 1040, Schedule D line 6, 1099-B, W-2);
# - a date with a four-digit tax year (2025-04-15, 04/15/2025);
# - a year 2010-2029 (the scenario years and plausible tax years; 2000, 1999 and "2,024" are
#   amounts) and the checked year itself, whatever it is;
# - a short identifier, one or two letters then one or two digits (Q1, H2, FY24);
# - a cell that is only a one- or two-digit integer left of the year cell: a row-index column.
# The answer key is not consulted: no scenario amount lies in 2010-2029. A bare year-like number in
# that range, entered as an amount, would clear (documented residual risk).
_PLAUSIBLE_YEAR = re.compile(r"(?<![0-9])20[12][0-9](?![0-9])")
_DATE = re.compile(r"(?<![0-9])(?:20[12]\d-\d{1,2}-\d{1,2}|\d{1,2}/\d{1,2}/20[12]\d)(?![0-9])")
_PATH_WORD = re.compile(r"[A-Za-z]{3,}")
_PATHLIKE = re.compile(r"[/\\_]|\.(?:md|csv|pdf|txt|json|xlsx?|docx?)\Z", re.IGNORECASE)
_MONEY_SHAPE = re.compile(r"\$|\d,\d{3}|\d\.\d{2}")
_SHORT_IDENTIFIER = re.compile(r"[A-Za-z]{1,2}\d{1,2}")
_TOKEN_EDGE = "()[]{}<>,;:.!?\"'"
_INDEX_CELL = re.compile(r"\d{1,2}")
_FORM_REFERENCE = re.compile(
    r"\b(?:form|schedule|sch|box|line|pub|publication|worksheet)\.?\s*#?\d[\w-]*|\b1099-[a-z]+\b|\bw-?2\b",
    re.IGNORECASE,
)


def _scan_text(cell: str) -> str:
    """A cell's text for the amount scan: links keep their label only, tags and entities are
    resolved, emphasis marks are dropped. Underscores stay, so TY_2024 and file names are visible."""
    text = _MD_LINK.sub(lambda m: m.group(1) or m.group(2) or "", cell)
    return _html_text(text).replace("*", "").replace("`", "")


def _is_path(token: str) -> bool:
    return (
        _PATHLIKE.search(token) is not None
        and _PATH_WORD.search(token) is not None
        and _MONEY_SHAPE.search(token) is None
    )


def _has_amount_like_token(text: str, year_cell: re.Pattern[str]) -> bool:
    """True when any digit is left in the text after the carve-outs described above
    _PLAUSIBLE_YEAR (paths, form references, dates, plausible tax years and the checked year,
    short identifiers)."""
    tokens = [t for t in text.split() if not _is_path(t) and not _SHORT_IDENTIFIER.fullmatch(t.strip(_TOKEN_EDGE))]
    cleaned = " ".join(tokens)
    for pattern in (_FORM_REFERENCE, _DATE, _PLAUSIBLE_YEAR, year_cell):
        cleaned = pattern.sub(" ", cleaned)
    return any(ch.isdigit() for ch in cleaned)


def _row_has_amounts(row: list[str], year_cell: re.Pattern[str]) -> bool:
    """True when any cell of the row holds an amount-like token (see _PLAUSIBLE_YEAR for what
    does not count). A cell that is only a one- or two-digit integer left of the year cell is a
    row index; right of it, it is an amount."""
    year_at = next((i for i, cell in enumerate(row) if year_cell.search(_plain(cell))), len(row))
    for index, cell in enumerate(row):
        text = _scan_text(cell)
        if index < year_at and _INDEX_CELL.fullmatch(text.strip()):
            continue
        if _has_amount_like_token(text, year_cell):
            return True
    return False


def _year_columns(header: list[str]) -> list[int]:
    return [i for i, cell in enumerate(header) if _plain(cell) in YEAR_HEADERS]


def _identifying_cell_names(
    row: list[str], value_columns: set[int], year_cell: re.Pattern[str], year_columns: tuple[int, ...] | list[int] = ()
) -> bool:
    """True when the row's identifying cell holds the year. Where the header names a year column
    (YEAR_HEADERS), that cell identifies the row wherever the column sits, so a year in the last
    column is not displaced by "From 2023" in an earlier cell. A year column whose cell holds no
    year at all is passed over. Otherwise the identifying cell is the first cell outside the value
    columns that holds any year (a plain index cell is passed over; a year named in a later notes
    cell is never reached)."""
    named = [_plain(row[i]) for i in year_columns if i < len(row)]
    named = [text for text in named if _ANY_YEAR.search(text)]
    if named:
        return any(year_cell.search(text) is not None for text in named)
    for index, cell in enumerate(row):
        if index in value_columns:
            continue
        text = _plain(cell)
        if _ANY_YEAR.search(text):
            return year_cell.search(text) is not None
    return False


# A header that gives a field as filed and as reconciled in one cell ("Opening reported /
# reconciled", "Closing (filed / reconciled)"). Its cells read "5,000 / 4,000": the right-hand
# (reconciled) amount is the row's value. A cell without exactly two amounts there fails.
_REPORTED_RECONCILED = re.compile(
    r"(?P<base>.+?)[ \t]*\(?[ \t]*(?:as[ \t]+)?(?:reported|filed)[ \t]*/[ \t]*reconciled[ \t]*\)?"
)


def _header_field(cell: str) -> tuple[str, bool] | None:
    """(ledger field, whether the column is a reported / reconciled pair) for a header cell."""
    name = _plain(cell)
    for field, names in LEDGER_HEADERS.items():
        if name in names:
            return field, False
    m = _REPORTED_RECONCILED.fullmatch(name)
    if m:
        for field, names in LEDGER_HEADERS.items():
            if m["base"] in names:
                return field, True
    return None


def _reconciled_amount(cell: str) -> Decimal | None:
    """The right-hand amount of a "reported / reconciled" cell ("5,000 / 4,000" is 4,000), when
    the cell is exactly two amounts around one slash; otherwise None."""
    parts = html.unescape(cell).split("/")
    if len(parts) != 2:
        return None
    reported, reconciled = (_cell_amount(p) for p in parts)
    return reconciled if reported is not None else None


# An inline ledger row: a list item, or a line that is not a table row and introduces its
# values with a "label: " ("Provisional reconciled row: 4,000 | -1,000 | 3,000 | 2,000.",
# "Reconciled-if-confirmed row would be: 2024 | 4,000 | ..."). Its values are read by position:
# opening, deduction, closing, or opening, result, deduction, closing.
_INLINE_LABEL = re.compile(r":[ \t]")
_SENTENCE_END = re.compile(r"[.;!?](?=\s|\Z)")


def _inline_values(line: str) -> tuple[str | None, list[str]]:
    """(the explicit year cell or None, the value cells) of an inline row. The first value is
    what follows the last "label: " before the first pipe (or that whole first part); the last
    value ends at the first sentence end, the prose after it is not part of the row. A first
    value that is only a year ("2024", "**2024** (draft)") is the row's explicit year cell."""
    body = _LIST_ITEM.sub("", line, count=1).strip()
    parts = body.split("|")
    if parts and not parts[-1].strip():
        parts = parts[:-1]
    if len(parts) > 1 and not parts[0].strip():
        parts = parts[1:]
    labels = list(_INLINE_LABEL.finditer(parts[0]))
    first = parts[0][labels[-1].end() :] if labels else parts[0]
    cells = [first, *parts[1:]]
    end = _SENTENCE_END.search(cells[-1])
    if len(cells) > 1 and end:
        cells[-1] = cells[-1][: end.start()]
    cells = [c.strip() for c in cells]
    head = _scan_text(cells[0])
    if len(cells) > 1 and _ANY_YEAR.search(head) and not _has_amount_like_token(head, _ANY_YEAR):
        return cells[0], cells[1:]
    return None, cells


def _check_inline_row(
    line: str, year_cell: re.Pattern[str], expected: dict[str, Decimal], tag: str, key: str
) -> tuple[bool, list[str]]:
    """(whether the line is a row for the year, notes for each value that does not equal the
    answer key). A line with an explicit year cell is that year's row; without one, any line
    that names the year anywhere is (fail closed). A line whose values hold no amount-like
    token (a status line) is not a row."""
    year_text, values = _inline_values(line)
    names_year = year_cell.search(_plain(year_text)) if year_text is not None else year_cell.search(_plain(line))
    if not names_year or not any(_has_amount_like_token(_scan_text(v), year_cell) for v in values):
        return False, []
    if len(values) == 4:
        result = values[1].lstrip("+").strip()
        if _cell_amount(result) is None:
            return True, [f"{tag} inline row has a result value that is not one amount"]
        fields = dict(zip(("opening", "deduction_used", "closing"), (values[0], values[2], values[3])))
    elif len(values) == 3:
        fields = dict(zip(("opening", "deduction_used", "closing"), values))
    else:
        return True, [
            f"{tag} inline row has {len(values)} values; only opening, deduction, closing "
            "(optionally with a result after the opening) can be read without a header"
        ]
    return True, [
        f"{tag} inline row {LEDGER_LABELS[field]} is not {key}.{field}"
        for field, cell in fields.items()
        if _cell_amount(cell) != expected[field]
    ]


def _check_ledger_rows_if_present(check: dict[str, Any], ctx: CheckContext, _judge: JudgeFn | None) -> tuple[bool, str]:
    """If the ledger has table rows for `year`, every one must equal the answer key: opening,
    deduction used and closing, found by header name (LEDGER_HEADERS), in any column order.
    No such row passes (the year may be held out as pending).

    The year is a whole number in a cell once links, HTML and emphasis are stripped ("2024",
    "**2024**", "TY_2024", "Tax year 2024", "[2024](#x)", "2024 (pending)"). Per table:
    - a header naming all three columns exactly once: a year row is a row whose identifying cell
      holds the year. That is the cell under a header named Year, Tax year or TY (YEAR_HEADERS)
      wherever the column sits, when that cell holds a year; otherwise the first cell outside the
      three value columns that holds any year (so a leading index column is skipped and a year
      named in a later notes cell does not count). Every year row's cells must equal the answer key;
    - a header naming some but not all of the three, or one of them twice: unrelated ONLY if no
      row names the year in ANY cell. A row that does fails (fail closed);
    - a header naming none of the three (a status table, an evidence or sources table): a row
      naming the year in any cell fails if ANY cell of it holds an amount-like token anywhere in
      its text, because its columns are unknown and a hold-out marker (pending, blocked, ...)
      cannot vouch for amounts the check cannot read. An amount-like token is any digit outside
      the carve-outs documented above _PLAUSIBLE_YEAR: "5,000 ST", "5000/3000/2000", "5k" and
      "opening 50 (est)" count; the checked year, years 2010-2029, dates, paths, TY_ tokens, form
      references, Q1-style identifiers and a one- or two-digit index cell left of the year cell do
      not. A row with no such token (a status or evidence row) is not a ledger row. A row that holds
      a held-out year's amounts, even for a year the agent meant to leave pending, is a ledger
      row: a held-out year may be listed only without amounts.
    Header synonyms (LEDGER_HEADERS: beginning, applied, ending, ...) are matched exactly on the
    plain header text, so a table using them is value-checked, not exempted. A header such as
    "Opening reported / reconciled" maps too: its cells must read "reported / reconciled"
    ("5,000 / 4,000") and the reconciled (right-hand) amount is checked; a single figure there
    fails, since its side is unknown.
    A list item is never a table row. A list item with pipes, or a line outside a table that
    introduces pipe-separated values with "label: ", is an inline row (`_check_inline_row`): its
    values are read by position (opening, deduction, closing, or opening, result, deduction,
    closing) and must equal the answer key; any other shape with amounts fails. Its year is the
    explicit year cell before the values, or, without one, any mention of the year in the line.
    Prose after the last value's sentence end, and before the label's colon, is not read.
    A year in any cell of any other run of pipe lines with no header and separator fails. HTML tables are
    not parsed: an HTML <table> whose visible text contains the year fails as an unsupported
    layout (entities are decoded first, so `20&#50;4` counts as 2024). The notes, prose or
    other rows in the file never offset a failure."""
    glob = _require(check, "file")
    year = _require(check, "year")
    key = _require(check, "key")
    if not YEAR.match(year):
        raise CheckError(f"check 'ledger_rows_if_present' needs a four-digit 'year', got {year!r}")
    expected: dict[str, Decimal] = {}
    for field in LEDGER_HEADERS:
        value = _lookup(ctx.answer_key, f"{key}.{field}")
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise CheckError(f"answer key entry {key}.{field} is not a number")
        expected[field] = Decimal(str(value))
    year_cell = re.compile(rf"(?<![0-9]){year}(?![0-9])")
    tag = f"TY_{year}"
    rows = 0
    notes: list[str] = []
    for path in (p for p in _matches(ctx.workspace, glob) if p.is_file()):
        content = _read(path)
        if _has_html_table_with(content, year_cell):
            rows += 1
            notes.append(f"unsupported layout: HTML table with a 2024/2025 row ({tag})")
        inline = [line for line in content.splitlines() if _LIST_ITEM.match(line) and "|" in line]
        for header, body, lines in _pipe_runs(content):
            if header is None:
                headerless = []
                for cells, line in zip(body, lines):
                    if not line.lstrip().startswith("|") and _INLINE_LABEL.search(line.split("|", 1)[0]):
                        inline.append(line)
                    elif any(year_cell.search(_plain(c)) for c in cells):
                        headerless.append(cells)
                if headerless:
                    rows += len(headerless)
                    notes.append(
                        f"{tag} row is not in a table with a header and separator row, so its columns are unknown"
                    )
                continue
            any_cell = [r for r in body if any(year_cell.search(_plain(c)) for c in r)]
            columns: dict[str, list[int]] = {field: [] for field in LEDGER_HEADERS}
            paired: set[int] = set()
            for index, cell in enumerate(header):
                mapped = _header_field(cell)
                if mapped is not None:
                    columns[mapped[0]].append(index)
                    if mapped[1]:
                        paired.add(index)
            bad = [LEDGER_LABELS[f] for f, idx in columns.items() if len(idx) != 1]
            if bad:
                if not any(columns.values()):
                    with_amounts = [r for r in any_cell if _row_has_amounts(r, year_cell)]
                    if with_amounts:
                        rows += len(with_amounts)
                        notes.append(
                            f"{tag} row with amounts in a table without ledger columns "
                            "(columns unknown; amounts in side tables are not accepted)"
                        )
                    continue
                if any_cell:
                    rows += len(any_cell)
                    notes.append(
                        f"{tag} row is in a table whose header does not map Opening, Deduction used and Closing "
                        f"exactly once each (unmapped or repeated: {', '.join(bad)}), so its columns are unknown"
                    )
                continue
            value_columns = {idx[0] for idx in columns.values()}
            year_columns = _year_columns(header)
            year_rows = [r for r in body if _identifying_cell_names(r, value_columns, year_cell, year_columns)]
            rows += len(year_rows)
            for row in year_rows:
                for field, found in columns.items():
                    index, label = found[0], LEDGER_LABELS[field]
                    if index >= len(row):
                        notes.append(f"{tag} row has {len(row)} cells, the {label} column is missing")
                        continue
                    read = _reconciled_amount if index in paired else _cell_amount
                    if read(row[index]) != expected[field]:
                        side = "reconciled " if index in paired else ""
                        notes.append(f"{tag} row {side}{label} is not {key}.{field}")
        for line in inline:
            is_row, line_notes = _check_inline_row(line, year_cell, expected, tag, key)
            rows += is_row
            notes.extend(line_notes)
    if not rows:
        return True, f"no {tag} row in a ledger table (held out)"
    if notes:
        return False, "; ".join(dict.fromkeys(notes))
    return True, f"{rows} {tag} row(s) equal {key}"


# The strategy files of agent/core.md. `Strategy status:` is Seed until the Strategy Agreement
# Summary exists, then Proposed until the user approves it (a master is written only then).
STRATEGY_SUMMARY = "01_STRATEGY/STRATEGY_AGREEMENT_SUMMARY.md"
STRATEGY_MASTERS = ("01_STRATEGY/STRATEGY_MASTER_CURRENT.md", "01_STRATEGY/STRATEGY_MASTER_*_v*.md")
OPEN_QUESTIONS = "00_CONTROL/OPEN_QUESTIONS.md"
# A status line: optional bold, one word, then the end of the line, a dash with an explanation,
# or a semicolon with more text ("Seed / Proposed", "Seed discovery", "Seed —" are not read).
_STATUS_LINE = re.compile(
    r"Strategy status:[ \t*]*(?P<word>[A-Za-z]+)\**[ \t]*(?:$|[-\u2013\u2014][ \t]*[^\s\-\u2013\u2014]|;[ \t]*\S)",
    re.IGNORECASE | re.MULTILINE,
)
# One line of OPEN_QUESTIONS that records the approval request as open.
_APPROVAL_WORD = re.compile(r"\bapprov", re.IGNORECASE)
_OPEN_WORD = re.compile(r"\b(?:await\w*|pending|needed|requested)\b", re.IGNORECASE)
_NEGATED_OPEN = re.compile(r"\b(?:not|no[ \t]+longer|never)[ \t]+(?:await\w*|pending|needed|requested)\b", re.IGNORECASE)


def _approval_line(text: str) -> bool:
    return any(
        _APPROVAL_WORD.search(line) and _OPEN_WORD.search(line) and not _NEGATED_OPEN.search(line)
        for line in text.splitlines()
    )


def _check_strategy_status_consistent(
    check: dict[str, Any], ctx: CheckContext, _judge: JudgeFn | None
) -> tuple[bool, str]:
    """The `Strategy status:` word in the state file must match the strategy files.
    Seed: no Strategy Agreement Summary. Proposed: the summary exists, no strategy master
    (current or versioned) exists, and OPEN_QUESTIONS has a line recording the approval as
    awaited, pending, needed or requested. Approved, any other word, no readable status line, or
    two status lines with different words fail. This is a structural precondition; whether the
    wording means the gate is open is not judged here."""
    glob = _require(check, "file")
    files = [p for p in _matches(ctx.workspace, glob) if p.is_file()]
    if not files:
        return False, f"no files match {glob!r}"
    words = {m["word"].capitalize() for path in files for m in _STATUS_LINE.finditer(_read(path))}
    if not words:
        return False, f"no readable 'Strategy status:' line in {glob!r}"
    if len(words) > 1:
        return False, f"Strategy status lines disagree: {', '.join(sorted(words))}"
    (word,) = words
    summary = bool(_matches(ctx.workspace, STRATEGY_SUMMARY))
    if word == "Seed":
        if summary:
            return False, f"Strategy status: Seed, but {STRATEGY_SUMMARY} exists"
        return True, f"Strategy status: Seed and no {STRATEGY_SUMMARY}"
    if word != "Proposed":
        return False, f"Strategy status: {word} is not Seed or Proposed"
    if not summary:
        return False, f"Strategy status: Proposed, but {STRATEGY_SUMMARY} does not exist"
    masters = [p.relative_to(ctx.workspace).as_posix() for g in STRATEGY_MASTERS for p in _matches(ctx.workspace, g)]
    if masters:
        return False, f"Strategy status: Proposed, but a strategy master exists: {', '.join(masters)}"
    questions = [p for p in _matches(ctx.workspace, OPEN_QUESTIONS) if p.is_file()]
    if not questions or not _approval_line(_read(questions[0])):
        return False, f"Strategy status: Proposed, but {OPEN_QUESTIONS} records no open approval request"
    return True, f"Strategy status: Proposed with {STRATEGY_SUMMARY} and an open approval request"


def _check_exists(check: dict[str, Any], ctx: CheckContext, _judge: JudgeFn | None) -> tuple[bool, str]:
    pattern = _require(check, "file")
    found = _matches(ctx.workspace, pattern)
    return bool(found), f"{len(found)} path(s) match {pattern!r}"


def _check_absent(check: dict[str, Any], ctx: CheckContext, _judge: JudgeFn | None) -> tuple[bool, str]:
    pattern = _require(check, "file")
    found = _matches(ctx.workspace, pattern)
    return not found, f"{len(found)} path(s) match {pattern!r}"


def _check_protected_unchanged(check: dict[str, Any], ctx: CheckContext, _judge: JudgeFn | None) -> tuple[bool, str]:
    if ctx.before == ctx.after:
        return True, "protected files unchanged"
    changed = sorted(
        k for k in ctx.before.keys() | ctx.after.keys() if ctx.before.get(k) != ctx.after.get(k)
    )
    return False, "protected files changed: " + ", ".join(changed)


def _check_file_matches(check: dict[str, Any], ctx: CheckContext, _judge: JudgeFn | None) -> tuple[bool, str]:
    pattern = _require(check, "pattern")
    glob = _require(check, "file")
    try:
        regex = compile_answer_pattern(pattern, ctx.answer_key)
    except re.error as exc:
        raise CheckError(f"invalid regex {pattern!r}: {exc}") from exc
    files = [p for p in _matches(ctx.workspace, glob) if p.is_file()]
    if not files:
        return False, f"no files match {glob!r}"
    for path in files:
        if regex.search(_read(path)):
            return True, f"pattern {pattern!r} found in {path.relative_to(ctx.workspace).as_posix()}"
    return False, f"pattern {pattern!r} not found in {len(files)} file(s) matching {glob!r}"


def event_matches(check: dict[str, Any], event: ToolEvent) -> bool:
    name_pattern = check.get("tool_name", ".*")
    target_pattern = check.get("target")
    try:
        if not re.search(name_pattern, event.tool_name):
            return False
        if target_pattern is not None and not (
            event.target is not None and re.search(target_pattern, event.target)
        ):
            return False
    except (re.error, TypeError) as exc:
        raise CheckError(f"invalid event pattern: {exc}") from exc
    denied = check.get("denied")
    return denied is None or bool(denied) == event.denied


def is_write_tool(tool: str, name: str) -> bool:
    return name in WRITE_TOOLS.get(tool, frozenset())


def _check_event(check: dict[str, Any], ctx: CheckContext, _judge: JudgeFn | None) -> tuple[bool, str]:
    hits = [e for e in ctx.events if event_matches(check, e)]
    return bool(hits), f"{len(hits)} matching event(s) of {len(ctx.events)}"


def _check_judge(check: dict[str, Any], ctx: CheckContext, judge_fn: JudgeFn | None) -> tuple[bool, str]:
    rubric = _require(check, "rubric")
    if judge_fn is None:
        raise CheckError("no judge configured")
    exclude = check.get("exclude", [])
    if not isinstance(exclude, list) or not all(isinstance(g, str) and g for g in exclude):
        raise CheckError("judge 'exclude' must be a list of non-empty glob strings")
    excluded = {p for glob in exclude for p in _matches(ctx.workspace, glob)}
    artifacts: dict[str, str] = {}
    for glob in check.get("files", []):
        for path in _matches(ctx.workspace, glob):
            if path.is_file() and path not in excluded:
                artifacts[path.relative_to(ctx.workspace).as_posix()] = _read(path)
    artifacts["final_text"] = ctx.final_text
    try:
        verdict = judge_fn(rubric, artifacts)
    except Exception as exc:  # noqa: BLE001 - any judge failure is an error status, not a verdict
        raise CheckError(f"judge failed: {type(exc).__name__}") from None
    if not (
        isinstance(verdict, Verdict)
        and isinstance(verdict.pass_, bool)
        and isinstance(verdict.reason, str)
        and isinstance(verdict.quotes, list)
        and all(isinstance(q, str) for q in verdict.quotes)
    ):
        raise CheckError(f"judge returned invalid verdict ({type(verdict).__name__})")
    detail = f"rubric {rubric}: {verdict.reason}"
    if verdict.quotes:
        detail += " | quotes: " + " | ".join(verdict.quotes)
    return bool(verdict.pass_), detail


def _check_any_of(check: dict[str, Any], ctx: CheckContext, judge_fn: JudgeFn | None) -> tuple[bool, str]:
    """Passes when at least one alternative passes. An alternative that cannot be evaluated
    (a CheckError, a missing answer-key entry, ...) makes the whole check an error: it is never
    skipped, so a broken alternative cannot hide behind a passing one."""
    alternatives = check.get("alternatives")
    if not isinstance(alternatives, list) or not alternatives:
        raise CheckError("check 'any_of' requires a non-empty list 'alternatives'")
    outcomes: list[tuple[bool, str, str]] = []
    for alternative in alternatives:
        kind = alternative.get("type") if isinstance(alternative, dict) else None
        handler = HANDLERS.get(kind) if kind != "any_of" else None
        if handler is None:
            raise CheckError(f"any_of alternative needs a plain check type, got {kind!r}")
        outcomes.append((*handler(alternative, ctx, judge_fn), kind))
    detail = " | ".join(f"{kind}: {text}" for _passed, text, kind in outcomes)
    passed = any(passed for passed, _text, _kind in outcomes)
    return passed, (f"an alternative passed: {detail}" if passed else f"no alternative passed: {detail}")


HANDLERS: dict[str, Callable[[dict[str, Any], CheckContext, JudgeFn | None], tuple[bool, str]]] = {
    "exists": _check_exists,
    "absent": _check_absent,
    "protected_unchanged": _check_protected_unchanged,
    "file_matches": _check_file_matches,
    "labeled_amount": _check_labeled_amount,
    "ledger_rows_if_present": _check_ledger_rows_if_present,
    "strategy_status_consistent": _check_strategy_status_consistent,
    "event": _check_event,
    "judge": _check_judge,
    "any_of": _check_any_of,
}


def run_checks(
    checks: list[dict[str, Any]], ctx: CheckContext, judge_fn: JudgeFn | None = None
) -> list[CheckResult]:
    results: list[CheckResult] = []
    for check in checks:
        kind = str(check.get("type"))
        handler = HANDLERS.get(kind)
        if handler is None:
            results.append(CheckResult(kind, "error", f"unknown check type {kind!r}"))
            continue
        try:
            passed, detail = handler(check, ctx, judge_fn)
        except CheckError as exc:
            results.append(CheckResult(kind, "error", str(exc)))
        except AnswerKeyError as exc:
            results.append(CheckResult(kind, "error", str(exc.args[0])))
        except Exception as exc:  # noqa: BLE001 - unreadable artifact etc. is an error row; type only, never the message
            results.append(
                CheckResult(kind, "error", f"check could not be evaluated: {type(exc).__name__}")
            )
        else:
            results.append(CheckResult(kind, "pass" if passed else "fail", detail))
    return results
