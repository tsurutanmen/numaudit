"""numaudit: every number you wrote should come from your data."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import __version__
from .audit import audit_text, collect_data


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="numaudit",
        description="Check that every number written in a document appears in the data it came from.",
    )
    ap.add_argument("documents", nargs="+", type=Path, help="Markdown or text files to check")
    ap.add_argument("--data", "-d", nargs="+", type=Path, required=True,
                    help="data files or directories (JSON, JSONL, CSV, TSV, TXT)")
    ap.add_argument("--allow", "-a", nargs="*", type=float, default=[],
                    help="values that need no source, e.g. hardware model numbers")
    ap.add_argument("--include-code", action="store_true", help="also check numbers inside code blocks and `code`")
    ap.add_argument("--json", action="store_true", help="print findings as JSON")
    ap.add_argument("--show-sourced", action="store_true", help="also list the numbers that were found in the data")
    ap.add_argument("--version", action="version", version=f"numaudit {__version__}")
    a = ap.parse_args(argv)
    # Never crash on a console that cannot show a character from the document (cp932 on Japanese Windows)
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")
        except (AttributeError, ValueError):
            pass

    try:
        pool, files = collect_data(a.data)
    except FileNotFoundError as e:
        print(f"numaudit: data path not found: {e}", file=sys.stderr)
        return 2
    if not files:
        print("numaudit: no data files found (looked for .json .jsonl .csv .tsv .txt)", file=sys.stderr)
        return 2

    allowed = set(a.allow)
    report = []
    unsourced_total = 0
    for doc in a.documents:
        text = doc.read_text(encoding="utf-8", errors="replace")
        findings, skipped = audit_text(text, pool, allowed, a.include_code, base=doc.parent)
        unsourced = [f for f in findings if f.status == "unsourced"]
        unsourced_total += len(unsourced)
        report.append((doc, findings, skipped, unsourced))

    if a.json:
        out = {"data_files": [str(f) for f in files], "data_values": len(pool), "documents": []}
        for doc, findings, skipped, _ in report:
            out["documents"].append({
                "path": str(doc),
                "findings": [{"line": f.number.line, "text": f.number.text, "status": f.status,
                              "nearest": f.nearest, "context": f.number.context} for f in findings],
                "skipped": [{"line": s.line, "text": s.text, "reason": s.reason} for s in skipped],
            })
        print(json.dumps(out, ensure_ascii=False, indent=1))
        return 1 if unsourced_total else 0

    print(f"data: {len(files)} file(s), {len(pool)} distinct values")
    for doc, findings, skipped, unsourced in report:
        counts = {s: sum(f.status == s for f in findings) for s in ("exact", "rounded", "allowed", "unsourced")}
        print(f"\n{doc}: {len(findings)} numbers: {counts['exact']} exact, {counts['rounded']} rounded, "
              f"{counts['allowed']} allowed, {counts['unsourced']} with no source")
        shown = findings if a.show_sourced else unsourced
        for f in shown:
            near = f" (nearest in data: {f.nearest:g})" if f.nearest is not None and f.status == "unsourced" else ""
            print(f"  {f.number.line:>5}  {f.status:<9} {f.number.text:<12}{near}")
            print(f"         {f.number.context}")
    if unsourced_total:
        print(f"\n{unsourced_total} number(s) have no source in the data. Fix them, or mark a line with"
              f" <!-- numaudit:ignore --> / pass --allow for values that need none.")
        return 1
    print("\nEvery number has a source in the data.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
