# How Forecast is evaluated

Forecast has no model trained on other people's data. Every range comes from
the user's own past turns, so the evaluation is a replay of that same history,
and the split is fixed by the rule below instead of a held-out file.

## The split

`eval/split_manifest.json` is the frozen definition. In words:

1. Take the user's turns in order. Failed turns, turns the user stopped, and
   turns during a major incident are left out.
2. Score each remaining turn against the range built from the turns **before
   it** (at most the last 300). The turn itself and later turns are never used.
3. No range is scored before 10 turns. Turns 10-29 are scored at the wide
   range (8 in 10) because that is what the user saw. From 30 on, the user's
   chosen range is used (half by default).
4. Coverage = share of scored turns that landed inside their range. The aim is
   about 50% for the half range and 80% for the wide range.

The same rule runs in the plugin (`predict.py: evaluate()`, shown by
`/forecast:stats`) and in the extension (`predict.js: evaluate()`, shown on the
options page). `tests/test_eval_manifest.py` fails if either one's constants
stop matching the manifest, and checks that the replay never looks ahead.

## Reproduce it

On any machine with your own history:

```
/forecast:stats                     # in Claude Code
python3 plugin/scripts/raindragon_eta_hook.py eval --data <data dir>   # raw numbers
```

`/forecast:doctor` prints the data directory.

## What is not reproducible from this repository

The incident slowdown in CLAIMS.md (turns 1.81x longer during major
incidents, 0.91x during minor ones) was measured on an earlier reference
corpus that is not in this repository. The incident rule itself is covered by
tests; the number is not re-measured here.
