"""Find the numbers written in a document, and the numbers present in data files."""
from __future__ import annotations

import csv
import io
import json
import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

# 3,727.5 / 3727.5 / 0.25 / 12 — a leading minus only when it is not part of a word or range ("i5-14600", "10-20")
# ASCII word characters only: in Python, \w also matches kanji, and "約11倍" must still yield 11
_W = "A-Za-z0-9_"
# a unit may follow directly ("4,110ms", "20x") but the number itself is never cut short ("4" out of "4,110")
NUMBER = re.compile(rf"(?<![{_W}.,])(-)?(\d{{1,3}}(?:,\d{{3}})+(?:\.\d+)?|\d+(?:\.\d+)?)(?![0-9_]|[.,]\d)(\s?%)?")
VERSION = re.compile(r"\bv?\d+\.\d+\.\d+(?:[-.]\w+)*")
DATE = re.compile(r"\b\d{4}-\d{2}-\d{2}(?:[T ]\d{2}:\d{2}(?::\d{2})?)?")
TIME = re.compile(r"\b\d{1,2}:\d{2}(?::\d{2})?\b")
URL = re.compile(r"https?://\S+|\]\([^)]*\)")
INLINE_CODE = re.compile(r"`[^`]*`")
LIST_MARKER = re.compile(r"^\s*\d+[.)]\s")
IGNORE_MARK = "numaudit:ignore"


@dataclass
class DocNumber:
    line: int
    text: str        # as written, e.g. "3,727" or "0.293%"
    value: float
    decimals: int
    percent: bool
    context: str     # the line, trimmed
    reason: str = ""  # why it was skipped, if it was


@dataclass
class Scan:
    numbers: list[DocNumber] = field(default_factory=list)
    skipped: list[DocNumber] = field(default_factory=list)


def _mask(pattern: re.Pattern, s: str) -> str:
    return pattern.sub(lambda m: " " * len(m.group(0)), s)


def _name_before(s: str, hyphen: int) -> bool:
    """True when the ASCII word right before the hyphen at `hyphen` contains a letter."""
    i = hyphen - 1
    word = ""
    while i >= 0 and s[i].isascii() and s[i].isalnum():
        word = s[i] + word
        i -= 1
    return any(c.isalpha() for c in word)


def parse_token(digits: str) -> tuple[float, int]:
    plain = digits.replace(",", "")
    decimals = len(plain.split(".", 1)[1]) if "." in plain else 0
    return float(plain), decimals


def scan_document(text: str, include_code: bool = False) -> Scan:
    """Numbers a reader would take as claims: code blocks, inline code, URLs, versions,
    dates, times and list markers are skipped (and reported as skipped)."""
    out = Scan()
    in_fence = False
    for n, raw in enumerate(text.splitlines(), 1):
        line = unicodedata.normalize("NFKC", raw)
        stripped = line.strip()
        if stripped.startswith("```") or stripped.startswith("~~~"):
            in_fence = not in_fence
            continue
        if in_fence and not include_code:
            continue
        if IGNORE_MARK in line:
            continue
        masked = line
        masked = _mask(URL, masked)
        if not include_code:
            masked = _mask(INLINE_CODE, masked)
        for pat in (VERSION, DATE, TIME):
            masked = _mask(pat, masked)
        masked = LIST_MARKER.sub(lambda m: " " * len(m.group(0)), masked)
        masked = re.sub(r"<!--.*?-->", lambda m: " " * len(m.group(0)), masked)
        for m in NUMBER.finditer(masked):
            sign, digits, pct = m.group(1), m.group(2), m.group(3)
            value, decimals = parse_token(digits)
            if sign:
                value = -value
            start = m.start()
            prev = masked[start - 1] if start > 0 else ""
            num = DocNumber(n, m.group(0).strip(), value, decimals, bool(pct), stripped[:160])
            if prev == "#":
                num.reason = "issue or PR number"
                out.skipped.append(num)
                continue
            if prev == "-" and _name_before(masked, start - 1):
                num.reason = "part of a name"  # "i5-14600KF", "gpt-4"; "10-20" stays a range
                out.skipped.append(num)
                continue
            out.numbers.append(num)
    return out


def _numbers_in_string(s: str) -> list[tuple[float, int]]:
    s = unicodedata.normalize("NFKC", s)
    found = []
    for m in NUMBER.finditer(s):
        value, decimals = parse_token(m.group(2))
        if m.group(1):
            value = -value
        found.append((value, decimals))
    return found


def _walk_json(obj, out: list[float]) -> None:
    if isinstance(obj, bool) or obj is None:
        return
    if isinstance(obj, (int, float)):
        out.append(float(obj))
    elif isinstance(obj, str):
        out.extend(v for v, _ in _numbers_in_string(obj))
    elif isinstance(obj, dict):
        for k, v in obj.items():
            _walk_json(v, out)
    elif isinstance(obj, list):
        for v in obj:
            _walk_json(v, out)


def data_values(path: Path) -> list[float]:
    """Every number that appears in a data file: JSON/JSONL leaves (numbers inside strings too),
    CSV cells, or any number in a text file. Thousands separators are understood."""
    text = path.read_text(encoding="utf-8", errors="replace")
    suffix = path.suffix.lower()
    values: list[float] = []
    if suffix == ".json":
        try:
            _walk_json(json.loads(text), values)
            return values
        except json.JSONDecodeError:
            pass
    if suffix in (".jsonl", ".ndjson"):
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                _walk_json(json.loads(line), values)
            except json.JSONDecodeError:
                values.extend(v for v, _ in _numbers_in_string(line))
        return values
    if suffix in (".csv", ".tsv"):
        dialect = "excel-tab" if suffix == ".tsv" else "excel"
        for row in csv.reader(io.StringIO(text), dialect=dialect):
            for cell in row:
                values.extend(v for v, _ in _numbers_in_string(cell))
        return values
    return [v for v, _ in _numbers_in_string(text)]
