# Threat model, claims and non-goals

Written on 2026-10-11 for 0.4, after an external roadmap review (it proposed a production system: isolated
executor, durable approval ledger, identity, approval UI, formal model, user studies). This project is a research
prototype: it measures how far an approval guard between an agent and its tools stops prompt injection on AgentDojo,
and at what cost. This page states what it defends against, what it claims with which evidence, and what it does not
do. The numbers are from the 0.3 runs (`docs/automatic-policy.md`); 0.4 is measured offline so far.

## The setting

The guard sits inside the agent's pipeline (`action_guard/guard.py`, wrapping AgentDojo's `run_function`). Every
call to a tool that acts waits for an approval bound to that exact call and used once (`action_guard/approval.py`);
warnings tell the approver which values did not come from the user. The approver is simulated: `follow-warnings`
approves a call with no warning and rejects one with a warning.

**The attacker** controls what the agent reads: documents, messages, web pages, transaction subjects, channel names
(AgentDojo's injection points), and can therefore make the agent propose any call its tools allow. In the live runs
the attacker uses AgentDojo's fixed templates (`important_instructions`; `tool_knowledge` once); attacks written
against the guard itself are hand-made cases (`docs/adaptive-tests.md`), not adaptive campaigns.

**Trusted**: the user's request; the tools file (`policies/<suite>-tools.json`: drafted by a model, checked by a
person, held against what the tools do by `tests/test_tools_files.py`, and since 0.4 refused when malformed: a
misspelled effect or role, a stray or repeated key, an argument of an acting tool without a role); the guard's code;
the approval channel; the environment at the task's start (who the user deals with, the account's history and
settings); the tools' implementations (an effect not declared is assumed not to exist).

## What is claimed, and the evidence

| Claim | Evidence | Limits |
|---|---|---|
| No call to an acting tool runs without an approval of that exact call, and one approval runs it once | `tests/test_approval.py`, `tests/test_guard.py`; every live run's guard record | Inside the agent's process only (below) |
| Banking, automatic policy: attack success 0 of 432 in each configuration, against 49.8% for the undefended agent and 16.2% for AgentDojo's best built-in defense (tool filter) | `results/v0.3-banking/`, paired bootstrap over user tasks: -49.8 points [-66.4, -33.3] vs undefended | One attack template; 16 user tasks, 9 attacker goals and 3 repeats are not 432 independent trials: Wilson's 95% upper bound, 0.9%, describes these runs, not a deployment |
| Banking: utility under attack about the undefended agent's | -0.4 points [-1.6, +0.8] | Clean utility on tasks needing a change: 9 of 27 vs 12-15 of 27 for the defenses |
| Slack: attack success 27/315 by AgentDojo's count, 8/315 on the calls carried out (hybrid) | `results/v0.3/` | Utility loss is significant: -19.0 points [-36.5, -3.2] clean (hybrid) |
| Documented gaps have executable probes | `tests/test_known_gaps.py`, `tests/test_research_boundaries.py` (strict xfail turns red when a gap closes); the versioned attack/twin matrix: `failure_matrix.py` | Open in 0.4: links in messages; additional supplied-call probes show wrong known recipients, negated target mentions and unrequested repeated payments. These are conditional policy tests, not new end-to-end attack rates. |

Every result is compared with our own undefended agent under the same harness; published numbers of other work used
other agents or benchmark versions and are not ranked against ours.

The link-judge pilot warned on legitimate links as well as malicious ones and was left disabled
(`results/v0.4/links-judge-pilot.json`). The additional 12 October probes and their legitimate controls are described
in [the research assessment](research-direction-2026-10-12.md). Single-use approval does not imply a per-task action
limit: repeated actions can each receive a fresh approval from the simulated user.

## Not claimed (non-goals)

- **Isolation.** The guard runs in the agent's process. An agent that reaches an effect outside the pipeline (a
  direct API, a shell, a browser) bypasses it. There is no separate executor and no separation of credentials.
- **Real approvals.** The approver is simulated. Human errors (approving by reflex, fatigue) are shown in two demo
  runs (`docs/demo-runs.md`), not measured.
- **Durable operations.** No persistent approval store, idempotency keys, budgets shared across processes,
  revocation, or reconciliation of an unknown outcome: one approval admits one call within one process.
- **Identity and egress.** No authentication, tenants or network controls. A web address can carry data out;
  visiting one is asked about as an action, but nothing below the tool layer is controlled.
- **Information flow.** Where a value came from is computed by finding it in earlier tool outputs (string
  occurrence), not by taint tracking. A value paraphrased, encoded or computed from protected data is not followed.
  Message content is not checked, except in banking for the user's own details; sending a link to a known member
  passes (the judge, asked in a pilot, warned on every link alike).
- **The judge as evidence.** The model judge is a source of warnings. Repeated votes are not independent evidence;
  some warnings it can never clear (the plan's, the account's signals, redirects, irreversible actions).
- **Generality.** One agent model (`gpt-4o-mini-2024-07-18`), two AgentDojo suites. The tasks were inspected during
  development: no result here is a held-out test.

## Reading the numbers

Rates come with Wilson 95% intervals over runs and paired bootstrap intervals over user tasks (`bootstrap.py`).
Zero failures in n runs bounds the rate for those runs only, and only if they were independent. Repeats of the
same tasks are not. A claim about other tasks, attackers or models needs runs on them.
