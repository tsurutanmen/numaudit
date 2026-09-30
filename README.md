# numaudit

**Every number you wrote should come from your data.**

```
pip install numaudit
numaudit README.md --data results/
```

numaudit reads a Markdown or text document, finds every number written in it, and looks for each one in your data files (JSON, JSONL, CSV, TSV, plain text). A number counts as sourced when some data value rounds to it at the precision it was written with: `3,727` is sourced by `3726.69`, `2.81` by `2.8125`, `29.3%` by `0.293`. Numbers that appear nowhere in the data are listed with their line, and the command exits with status 1, so it can run in CI before a report or README is published.

It exists because of a real mistake. A benchmark report said a server region took 727-989 ms per tick. The server had printed `3,828.71 MSPT`, and the parser that built the summary read only the part after the thousands separator. Run on that summary with the raw logs as data, numaudit flags both numbers:

```
     91  unsourced 989.0        (nearest in data: 990)
         | 256 | folia | 0.24 / 0.26 | 989.0 / 726.7 | 1.23 / 1.19 | [1, 1] |
     91  unsourced 726.7        (nearest in data: 727.3)
```

## What it reads as a number

- Thousands separators, decimals, percentages and ranges: `3,727`, `2.81`, `0.293%`, `3,727-4,110`, `3,727〜4,110`
- Numbers next to Japanese or other non-ASCII text: `約11倍` gives 11
- Units glued to a number: `4,110ms`, `20x`, `1.5GB`

It skips what a reader would not take as a measured claim, and lists these as skipped: fenced code blocks and `inline code` (unless `--include-code`), URLs and link targets, version strings (`v1.1.0`, `26.1.2`), dates and times, list markers, issue and PR numbers (`#4879`), and numbers that are part of a name (`i5-14600KF`, `gpt-4`).

## Options

```
numaudit DOC [DOC ...] --data PATH [PATH ...] [--allow VALUE ...] [--include-code] [--show-sourced] [--json]
```

- `--data`: files or directories. Directories are searched for `.json .jsonl .ndjson .csv .tsv .txt`. Inside JSON, numbers in strings count too (log lines, report text), and thousands separators are understood.
- `--allow`: values that need no source, such as a hardware model number or a random seed.
- `--show-sourced`: list every number, not only the unsourced ones.
- `--json`: machine-readable output.

Exit status: 0 when every number has a source, 1 when some do not, 2 when a data path is missing.

## In the document

- `<!-- numaudit:ignore -->` on a line skips that line, for numbers that are derived or come from elsewhere ("about 1 block in 7,000").
- `<!-- numaudit:data results/servers_*.jsonl -->` checks the numbers from that line on against only those files (paths relative to the document). `<!-- numaudit:data * -->` goes back to all of `--data`.

## Limits

A number is only checked for existence. If a written value is wrong but happens to equal some other value in the data, it passes. In a test on a real README with three planted errors and 26 data files (4,593 distinct values), two were flagged and the third (`13.9` for `12.9`) matched an unrelated value in another file. Scoping that section with `numaudit:data` to the files it came from flagged it too. The smaller the pool a section is checked against, the fewer wrong numbers slip through, and small integers match something in almost any data set.

Derived numbers (ratios, differences, "about 11x") have no direct source. Mark them with `numaudit:ignore`, or better, put the derived values into the data the document is built from.

## License

MIT
