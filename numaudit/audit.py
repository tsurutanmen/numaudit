"""Match every number in a document to a value in the data."""
from __future__ import annotations

import bisect
import re
from dataclasses import dataclass
from pathlib import Path

from .extract import DocNumber, data_values, scan_document

DATA_SUFFIXES = {".json", ".jsonl", ".ndjson", ".csv", ".tsv", ".txt"}


@dataclass
class Finding:
    number: DocNumber
    status: str          # "exact", "rounded", "unsourced", "allowed"
    nearest: float | None = None


class Pool:
    """All numbers from the data files, sorted for nearest-value lookups."""

    def __init__(self, values: list[float]):
        self.values = sorted(set(values))

    def nearest(self, x: float) -> float | None:
        if not self.values:
            return None
        i = bisect.bisect_left(self.values, x)
        cands = self.values[max(0, i - 1): i + 1]
        return min(cands, key=lambda v: abs(v - x))

    def __len__(self) -> int:
        return len(self.values)


def collect_data(paths: list[Path]) -> tuple[Pool, list[Path]]:
    files: list[Path] = []
    for p in paths:
        if p.is_dir():
            files.extend(sorted(f for f in p.rglob("*") if f.is_file() and f.suffix.lower() in DATA_SUFFIXES))
        elif p.is_file():
            files.append(p)
        else:
            raise FileNotFoundError(p)
    values: list[float] = []
    for f in files:
        values.extend(data_values(f))
    return Pool(values), files


def classify(num: DocNumber, pool: Pool, allowed: set[float]) -> Finding:
    """A written number is sourced when some data value rounds to it at the precision it was written with.
    "3,727" (0 decimals) is sourced by 3726.69; "2.81" by 2.8125; "0.293%" by 0.293027 or by 0.00293027."""
    if num.value in allowed:
        return Finding(num, "allowed")
    tol = 0.5 * 10 ** (-num.decimals) + 1e-9
    targets = [num.value]
    if num.percent:
        targets.append(num.value / 100)  # the data may hold a fraction
    best = None
    for t in targets:
        v = pool.nearest(t)
        if v is None:
            continue
        scale = 100 if (num.percent and t != num.value) else 1
        diff = abs(v * scale - num.value)
        if best is None or diff < best[1]:
            best = (v, diff)
        if abs(v - t) < 1e-12:
            return Finding(num, "exact", v)
    if best and best[1] <= tol:
        return Finding(num, "rounded", best[0])
    return Finding(num, "unsourced", best[0] if best else None)


SCOPE = re.compile(r"<!--\s*numaudit:data\s+(.+?)\s*-->")


def _scopes(text: str, base: Path | None) -> list[tuple[int, list[Path] | None]]:
    """Lines where a `<!-- numaudit:data PATH... -->` comment changes which data the following numbers
    are checked against. `*` goes back to all the --data given on the command line."""
    out: list[tuple[int, list[Path] | None]] = []
    for n, line in enumerate(text.splitlines(), 1):
        m = SCOPE.search(line)
        if not m:
            continue
        spec = m.group(1).split()
        if spec == ["*"]:
            out.append((n, None))
            continue
        paths: list[Path] = []
        for item in spec:
            root = base or Path(".")
            matched = sorted(root.glob(item)) if any(c in item for c in "*?[") else [root / item]
            paths.extend(matched)
        out.append((n, paths))
    return out


def audit_text(text: str, pool: Pool, allowed: set[float] | None = None, include_code: bool = False,
               base: Path | None = None):
    scan = scan_document(text, include_code=include_code)
    scopes = _scopes(text, base)
    pools: dict[tuple, Pool] = {}
    findings = []
    for num in scan.numbers:
        active = None
        for line, paths in scopes:
            if line <= num.line:
                active = paths
        if active is None:
            use = pool
        else:
            key = tuple(str(p) for p in active)
            if key not in pools:
                pools[key] = collect_data(active)[0]
            use = pools[key]
        findings.append(classify(num, use, allowed or set()))
    return findings, scan.skipped
