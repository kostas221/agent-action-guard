# Evidence index

What every published number rests on, what kind of evidence it is, and how to check or reproduce it. Written on
2026-10-12 for version 0.4.0; `results/evidence.json` is the machine-readable form, and `evidence.py` checks it.

## Four kinds of evidence

| kind | what ran | what it supports | what it does not support |
|---|---|---|---|
| **live runs** | the agent (`gpt-4o-mini-2024-07-18`) on AgentDojo v1.2.2 with the guard in its loop | attack success, utility and approvals of that configuration, with that code, on those tasks | other tasks, agents or attackers; real users |
| **replay** | today's policy on requests recorded in live runs, no model call | which decisions would change on those recorded requests | the agent's new trajectory after a changed decision |
| **pilot** | one model (judge or planner) asked about recorded requests or task requests, no agent | that component's answers on that finite set | an end-to-end rate; a held-out estimate |
| **supplied call** | a request built by hand in the account as the agent had it, policy only | whether the policy stops that call, and lets its legitimate twin run | how often an attacker gets an agent to make it |

The live numbers are the only end-to-end measurements. Every banking and Slack task has been inspected during
development, so none of them is a held-out test (`docs/threat-model.md`).

## Live runs

Each row is one planned experiment of `action_guard/evidence.py` (`PLANNED`). "Execution" is the fingerprint of the
code a repeat ran (`manifest.json`); "legacy" repeats ran before manifests existed and are identified by their
traces' digest and the release they belong to.

| runs folder | configurations | suites x repeats | attack | execution | results | where it is discussed |
|---|---|---|---|---|---|---|
| `runs/` | undefended agent | banking 3, Slack 3, travel 1, workspace 1 | important_instructions | legacy | `results/baseline.json` | README; `docs/results-banking.md` |
| `runs/` | hand-written policy: follow-warnings, oracle, reject-all; approve-all | banking 3 (approve-all 1) | important_instructions | legacy (0.1) | `results/guard-*.json`, `results/comparison.json` | `docs/results-banking.md` |
| `runs-v0.2/` | hand-written rules, hybrid, judge alone | banking 3 (judge 1) | important_instructions | legacy (0.2) | `results/v0.2/` | `docs/judge-design.md` |
| `runs-v0.2.0/` | hand-written rules, hybrid with history signals | banking 3 | important_instructions | legacy (0.2.0, release `v0.2.1`) | `results/v0.2.0/` | `docs/judge-design.md` |
| `runs-v0.3/` | automatic rules, automatic hybrid | Slack 3 | important_instructions | legacy (0.3) | `results/v0.3/` | `docs/automatic-policy.md`, "Results (Slack, three repeats)" |
| `runs-v0.3-banking/` | automatic rules, hybrid, hybrid with the gpt-4o-mini planner | banking 3 | important_instructions | `2782925bc43a` | `results/v0.3-banking/` | "Results (banking, three repeats)" |
| `runs-v0.3-banking/` | AgentDojo's tool filter, repeated prompt, spotlighting | banking 3 | important_instructions | `a35492ce3cac` | `results/v0.3-banking/` | same section |
| `runs-v0.3-tool-knowledge/` | undefended agent and the three automatic configurations | banking 1 | tool_knowledge | `a35492ce3cac` | `results/v0.3-tool-knowledge/` | "The second attack template" |
| `runs-v0.4/` | the three automatic configurations, 0.4 | banking 3 | important_instructions | planned: not run yet | (`results/v0.4/` after the runs) | "Version 0.4, live (stated before running)" |

Pooled numbers per configuration, with Wilson intervals and per-repeat values, are in each `comparison*.json`;
paired differences over user tasks in each `bootstrap*.json`.

## Replays, pilots and supplied calls

| file | kind | what it holds | how it is made |
|---|---|---|---|
| `results/history-signals.json` | replay | 0.2's history signals (W4, W5) on the 0.2 runs' requests | `signals_replay.py` (free) |
| `results/planner-replay*.json` | pilot + replay | phase A: tools file and plans from three models, their A0/A1/A2 on stored banking requests | `planner_replay.py` (paid) |
| `results/judge-pilot-1.json` ... `-5.json` | pilot | the 0.2 judge's answers on stored requests: five pilots (wordings, votes, the judge alone) | `judge_pilot.py` (paid) |
| `results/auto-judge-pilot-banking.json` | pilot | the general judge wording on the 0.2.0 hybrid's banking requests | `auto_judge_pilot.py` (paid) |
| `results/adaptive-tests.json`, `-signals.json` | supplied calls (+ judge) | eight hand-written attacks and five twins, rules and judge | `adaptive_tests.py` (paid for the judge's part) |
| `results/phase0_smoke.json` | live (smoke) | phase 0: 24 banking runs (4 user tasks, 4 attacker goals) to check the setup and its cost; traces in `runs/gpt-4o-mini-2024-07-18/` | `phase0_smoke.py` (paid) |
| `results/v0.4/fixes-replay.json` | replay | 0.4's fixes on the 4,939 requests of the 0.3 automatic configurations | `fixes_replay.py` (free) |
| `results/v0.4/links-judge-pilot.json` | pilot | fix 4: the judge on 352 recorded Slack requests carrying a link; rejected | `links_judge_pilot.py` (paid) |
| `results/v0.4/plan-pilot.json` | pilot | the planner wording tried for 0.4, both planners, 37 task requests; rejected | `plan_pilot.py` (paid) |
| `results/v0.4/failure-matrix.json` | supplied calls | every known attack and its twin through every policy, judge stubbed both ways | `failure_matrix.py` (free) |

Supplied-call boundaries are also kept as tests: `tests/test_known_gaps.py`, `tests/test_research_boundaries.py`
(strict expected failures that turn red when a gap closes) and `tests/test_failure_matrix.py`.

## What changed in 0.4 and how far it is measured

| change | measured by | live? |
|---|---|---|
| a redirect of an existing item to a new account is never cleared by the judge (fix 1) | replay; supplied call E1; `tool_knowledge` showed the judge clearing it again | pending (`runs-v0.4/`) |
| the account's signals W4, W5 in the automatic policy (fix 2) | replay (3 wrong payments stopped); supplied calls B1, B2, C1 | pending |
| the user's own settings must be typed or unchanged (fix 3) | replay (task 13's 34 rules requests; 62 hybrid requests need the judge) | pending |
| links in messages to the judge (fix 4) | pilot: rejected, off | not to be run |
| irreversible actions need a named target (fix 5) | supplied call R1; replay (no Slack change) | not run: no Slack decision changes |
| the 0.4 planner wording | pilot: rejected, the 0.3 wording kept | not to be run |
| malformed tools files refused; run lock; budget note | tests | no decision changes |
| labels, completeness check, evidence identity | tests; regenerated results identical | reporting only |

## Checking and reproducing

```bash
uv run python evidence.py
```

checks, without a model call, that every published repeat is present and complete (a planned repeat missing
altogether is reported), that its traces give the recorded digest, that it ran with the recorded code, that the
scoring code (`action_guard/evidence.py`, `SCORING_FILES`) is the one the results were made with, and that every
result file is the recorded one. Line endings are normalized, so the check gives the same answer on any platform.

Rules that keep published results intact:

- A result file listed in the index is never changed by `report.py`, `compare.py` or `bootstrap.py`: writing the same
  content again is allowed (that is how reproduction is checked); a different content is refused.
- A correction to scoring, or a new scorer, goes to a new results version; the index is rebuilt
  (`evidence.py --build`) only after a planned experiment is complete, and the old entries stay.
- Trace digests cover paths and bytes: a trace edited, removed or added changes them.

The run traces are not in the repository. The release archive (`archive_traces.py`, README "Reproduce the tables")
holds every planned runs folder, phase 0's smoke runs and the set-aside trials the docs mention; extracted in the repository root, it
lets `evidence.py` and every report run offline.
