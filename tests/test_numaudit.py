import json
import os
import subprocess
import sys
from pathlib import Path

from numaudit.audit import Pool, audit_text, classify
from numaudit.extract import data_values, scan_document

ROOT = Path(__file__).resolve().parents[1]


def texts(scan):
    return [n.text for n in scan.numbers]


# ---- reading numbers out of prose ----------------------------------------------------------------

def test_thousands_decimals_percent_and_ranges():
    s = scan_document("It took 3,727 to 4,110 ms, TPS 2.81 / 2.82, and 0.293% of blocks.")
    assert texts(s) == ["3,727", "4,110", "2.81", "2.82", "0.293%"]
    assert [n.decimals for n in s.numbers] == [0, 0, 2, 2, 3]


def test_japanese_text_around_numbers():
    s = scan_document("集まったFoliaは約11倍、3,727〜4,110msだった。")
    assert texts(s) == ["11", "3,727", "4,110"]


def test_units_glued_to_a_number_do_not_cut_it_short():
    assert texts(scan_document("4,110ms and 20x and 1.5GB")) == ["4,110", "20", "1.5"]


def test_model_names_versions_dates_times_and_issue_numbers_are_not_claims():
    s = scan_document("i5-14600KF, gpt-4, v1.1.0, 26.1.2, 2026-09-28, 18:30, #4879")
    assert texts(s) == []
    assert {n.reason for n in s.skipped} == {"part of a name", "issue or PR number"}


def test_a_hyphen_between_numbers_is_a_range():
    assert texts(scan_document("10-20 runs")) == ["10", "20"]


def test_code_list_markers_and_ignored_lines_are_skipped():
    doc = "1. first\n`python run.py 99`\n```\n77\n```\nkeep 5\nskip 6 <!-- numaudit:ignore -->\n"
    assert texts(scan_document(doc)) == ["5"]
    assert "99" in texts(scan_document(doc, include_code=True))


def test_numbers_inside_urls_are_skipped():
    assert texts(scan_document("see [page](https://x.org/a/123) and https://y.org/456 then 7")) == ["7"]


# ---- reading numbers out of data -----------------------------------------------------------------

def test_data_values_from_json_strings_keep_thousands_separators(tmp_path):
    # The bug this tool exists for: "3,828.71 MSPT" must become 3828.71, never 828.71.
    f = tmp_path / "r.jsonl"
    f.write_text(json.dumps({"tail": ["100.0% util at 3,828.71 MSPT at 0.24 TPS"], "cores": 1.23}) + "\n")
    vals = data_values(f)
    assert 3828.71 in vals and 0.24 in vals and 1.23 in vals
    assert 828.71 not in vals


def test_data_values_from_csv(tmp_path):
    f = tmp_path / "r.csv"
    f.write_text("name,value\na,1234.5\nb,\"2,000\"\n")
    assert {1234.5, 2000.0} <= set(data_values(f))


# ---- matching --------------------------------------------------------------------------------------

def one(text, pool_values, **kw):
    findings, _ = audit_text(text, Pool(pool_values), **kw)
    return [(f.number.text, f.status) for f in findings]


def test_rounded_to_the_written_precision_counts_as_sourced():
    assert one("3,727 and 2.81", [3726.69, 2.8125]) == [("3,727", "rounded"), ("2.81", "rounded")]


def test_one_digit_off_is_not_sourced():
    assert one("457.6", [467.6, 483.1]) == [("457.6", "unsourced")]


def test_percent_may_come_from_a_fraction():
    assert one("29.3%", [0.293]) == [("29.3%", "exact")]


def test_allowed_values_need_no_source():
    assert one("RTX 3060", [], allowed={3060.0}) == [("3060", "allowed")]


def test_scope_comment_limits_which_data_a_section_is_checked_against(tmp_path):
    (tmp_path / "a.json").write_text('{"x": 13.9}')
    (tmp_path / "b.json").write_text('{"x": 12.9}')
    doc = "13.9\n<!-- numaudit:data b.json -->\n13.9\n<!-- numaudit:data * -->\n13.9\n"
    pool = Pool(data_values(tmp_path / "a.json") + data_values(tmp_path / "b.json"))
    findings, _ = audit_text(doc, pool, base=tmp_path)
    assert [f.status for f in findings] == ["exact", "unsourced", "exact"]


# ---- the command line ------------------------------------------------------------------------------

def run_cli(*args, env_extra=None):
    env = dict(os.environ, **(env_extra or {}))
    env["PYTHONPATH"] = str(ROOT)
    return subprocess.run([sys.executable, "-m", "numaudit", *args], capture_output=True, env=env)


def test_cli_exit_codes(tmp_path):
    (tmp_path / "d.json").write_text('{"tps": 2.81}')
    (tmp_path / "ok.md").write_text("TPS 2.81\n", encoding="utf-8")
    (tmp_path / "bad.md").write_text("TPS 2.91\n", encoding="utf-8")
    assert run_cli(str(tmp_path / "ok.md"), "--data", str(tmp_path / "d.json")).returncode == 0
    assert run_cli(str(tmp_path / "bad.md"), "--data", str(tmp_path / "d.json")).returncode == 1
    assert run_cli(str(tmp_path / "ok.md"), "--data", str(tmp_path / "missing.json")).returncode == 2


def test_cli_does_not_crash_on_a_console_that_cannot_show_the_document(tmp_path):
    # Japanese Windows consoles use cp932, which has no em dash; the report quotes the line.
    (tmp_path / "d.json").write_text('{"v": 1}')
    (tmp_path / "doc.md").write_text("値は 5 — これは出所がない\n", encoding="utf-8")
    r = run_cli(str(tmp_path / "doc.md"), "--data", str(tmp_path / "d.json"), env_extra={"PYTHONIOENCODING": "cp932"})
    assert r.returncode == 1, r.stderr.decode("utf-8", "replace")
    assert b"Traceback" not in r.stderr


def test_cli_json_output(tmp_path):
    (tmp_path / "d.json").write_text('{"v": 1.5}')
    (tmp_path / "doc.md").write_text("1.5 and 9.5\n", encoding="utf-8")
    r = run_cli(str(tmp_path / "doc.md"), "--data", str(tmp_path / "d.json"), "--json")
    out = json.loads(r.stdout.decode("utf-8"))
    assert [f["status"] for f in out["documents"][0]["findings"]] == ["exact", "unsourced"]
