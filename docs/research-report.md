# Exact-call approval under prompt injection: a short research report

Version 0.5.0. Written on 2026-10-12 and completed on 2026-10-13 with the 0.4 live runs (banking, three repeats;
`docs/automatic-policy.md`, "Version 0.4, live"); 0.5 (the same day) changes the policy after a closing review and is
measured on the stored requests of those runs, not by new live runs ("Version 0.5" there). What each number rests on:
`docs/evidence.md`. What is and is not claimed: `docs/threat-model.md`.

## Question

Between an LLM agent and its tools, which parts of an action guard **enforce** an approval contract, which parts
**infer** whether an action was authorized by the user's task, and when does blocking attacks also block the
user's own work?

## Setup

- **Benchmark:** AgentDojo v1.2.2 (package 0.1.35), banking (16 user tasks, 9 attacker goals) and Slack (21, 5).
- **Agent:** `gpt-4o-mini-2024-07-18` in AgentDojo's default pipeline, the same in every configuration.
- **Attacks:** `important_instructions` (three repeats); `tool_knowledge` (banking, one repeat).
- **Approver:** simulated. `follow-warnings` approves a call with no warning and rejects one with a warning, so the
  guard decides alone. Approve-all, reject-all and an oracle (which knows the attacker's values) are diagnostics.
- **Measured:** attack success (AgentDojo's score, and on the calls carried out), utility without and under attack,
  utility on the banking tasks that need a change (9 of 16; the others pass when nothing is done), approvals, cost.
  Pooled Wilson intervals describe the runs; paired bootstrap intervals resample user tasks.

## What is enforced: the approval contract

Every call to a tool the tools file declares as acting waits for an approval of that exact call (tool and canonical
arguments), used once; nested calls are resolved and validated before review; a failed execution is recorded as
failed. Since 0.4 a malformed tools file is refused instead of read as "reading". The contract is enforced inside
the agent's process only, and it does not bound how many separately approved actions a task makes
(`tests/test_research_boundaries.py`).

## What is inferred: the configurations

| configuration | warnings | may a judge clear a warning? | planner | evidence |
|---|---|---|---|---|
| undefended | none | - | - | live |
| AgentDojo tool filter / repeated prompt / spotlighting | none (the defense changes the agent) | - | - | live, banking |
| hand-written rules (0.1, 0.2.0) | W1-W3: recipient, password, address not from the user or the account's payees; 0.2.0 adds W4-W5 (unusual amount, the user's details in a subject) | - | - | live, banking |
| hand-written hybrid (0.2.0) | the same | W1-W3 only | - | live, banking |
| automatic rules (0.3) | A0: who or where an action goes, and secrets, typed or known (tools file); A1: an action the plan does not list | - | `gpt-6-luna`, from the request alone | live, banking and Slack |
| automatic hybrid (0.3) | A0, A1 | A0 only | `gpt-6-luna` | live, banking and Slack |
| automatic hybrid, small planner (0.3) | A0, A1 | A0 only | `gpt-4o-mini` | live, banking |
| automatic, 0.4 | adds: a redirect of an existing item, the account's signals (W4-W5), the user's own settings, irreversible actions | settings only (redirects, signals, irreversible: never) | as 0.3 | live, banking; replay and supplied calls |
| automatic, 0.5 | adds: a list of recipients checked item by item; W6 (payments in one task to an account the user pays, together unusual) | settings only (now also never: targets and secrets of high-stakes tools, banking's payments and password); asked only when its answer decides | as 0.3 | replay (0 decisions change in the 0.4 runs) and supplied calls |

## Results (live)

**Banking, `important_instructions`, three repeats** (`results/comparison.json`, `results/v0.2.0/`,
`results/v0.3-banking/`, `results/v0.4/`):

| configuration | attack success | utility, no attack | utility under attack | tasks needing a change: no attack | under attack |
|---|---|---|---|---|---|
| undefended | 215/432 (49.8%) | 25/48 | 201/432 | 12/27 | 79/243 |
| tool filter | 70/432 (16.2%) | 26/48 | 191/432 | 12/27 | 79/243 |
| repeated prompt | 88/432 (20.4%) | 28/48 | 194/432 | 15/27 | 82/243 |
| spotlighting | 236/432 (54.6%) | 27/48 | 196/432 | 14/27 | 79/243 |
| hand-written rules (0.2.0) | 0/432 | 18/48 | 174/432 | 6/27 | 51/243 |
| hand-written hybrid (0.2.0) | 0/432 | 24/48 | 207/432 | 9/27 | 80/243 |
| automatic rules (0.3) | 0/432 | 22/48 | 211/432 | 9/27 | 78/243 |
| automatic hybrid (0.3) | 0/432 | 23/48 | 208/432 | 9/27 | 80/243 |
| automatic hybrid, small planner (0.3) | 0/432 | 23/48 | 197/432 | 6/27 | 55/243 |
| automatic rules (0.4) | 0/432 | 22/48 | 185/432 | 7/27 | 50/243 |
| automatic hybrid (0.4) | 0/432 | 24/48 | 207/432 | 9/27 | 81/243 |
| automatic hybrid, small planner (0.4) | 0/432 | 20/48 | 208/432 | 6/27 | 60/243 |

Paired over user tasks, the automatic configurations lower attack success by 49.8 points [-66.4, -33.3] against
the undefended agent and by 16.2 points [-27.8, -6.0] against the tool filter. The hybrid's utility under attack on
tasks needing a change is +0.4 points [-5.8, +6.6] against the tool filter: an interval that does not establish
equivalence. Zero successes in 432 runs of 16 tasks and 9 goals bound nothing beyond these runs (Wilson's 95% upper
end, 0.9%, is descriptive). With the units that are independent here (`bounds.py`, one-sided 95%): at most 2.1% of
the 144 (task, goal) pairs, 17.1% of user tasks, 28.3% of attacker goals.

In 0.4 the attacker's redirects (47, 45 and 42 requests) were stopped by the redirect rule and never reached the
judge, which cleared only the address update of user task 13 (from the file the user names). The rules refuse that
update by design and lose task 13 in all 30 runs (78 to 50 of 243 under attack); the hybrids keep it (81 and 60). The
account's signals (W4-W5) stopped 13 requests no other source warned, all wrong payments by the agent: among them
seven payments of 10,000 to the user's landlord that an injection asking for $30,000 in increments of $10,000 led
to. Against the undefended agent, by task: the 0.4 hybrid's utility under attack on tasks needing a change is +0.8
points [-2.1, +4.1].

**Slack, three repeats** (`results/v0.3/`): attack success 208/315 undefended, 24/315 (rules) and 27/315 (hybrid) by
AgentDojo's score, 7/315 and 8/315 on the calls carried out (AgentDojo scores one goal on the calls the agent asked
for, rejected ones included). Utility without attack falls from 49/63 to 26/63 (rules) and 37/63 (hybrid): -19.0
points [-36.5, -3.2] for the hybrid, and -14.0 [-27.0, -3.2] under attack. The guard costs real work on Slack.

**Banking, `tool_knowledge`, one repeat** (`results/v0.3-tool-knowledge/`): 44/144 undefended, 0/144 in each
automatic configuration; utility under attack on tasks needing a change 26/81 undefended, 27/81, 26/81 and 19/81.

**Which part stopped the attacker** (`barriers.py`, the 0.4 banking runs): the plan (A1) was never the only warning
on an attacker's request. A0 (a target or secret neither typed nor known) was, for 117, 119 and 116 requests, and in
the hybrids the judge decided those: it kept every one, but it was the open path for an attack written to persuade
it. 0.5 makes such warnings on banking's payments and password ones the judge cannot clear, at no measured cost: in
no banking live run did the judge clear a correct payee or password, and replayed on every stored request no
decision changes. On Slack (0.3) the plan was the only barrier for 34 and 35 attacker requests.

## Negative results and boundaries

1. **A forged value in the source the user named** (adaptive cases A1, A2: the bill or the address file edited by
   the attacker). Nothing the guard sees tells the attack from its twin; the rules refuse both, and the 0.2 judge
   let both run in A2 (`docs/adaptive-tests.md`). It is refusal, not detection.
2. **The judge cleared a redirect.** The attacker's redirect of a recurring payment (banking task 0, goal 4) was
   cleared by the judge 9 times under the first template and twice under the second; only the plan stopped it.
   0.4 makes such a redirect a warning the judge cannot clear; in the 0.4 live runs none reached the judge.
3. **A link sent to a known member** passes A0 (the member is known) and A1 (messages are planned): 16 such attacker
   requests ran on Slack. Asked about every recorded request with such a link, the judge warned on all 352, the
   article's link as much as the attacker's; the check was left off (`results/v0.4/links-judge-pilot.json`).
4. **Static plans cost dynamic tasks.** Slack tasks whose request hands its actions to a page (a to-do list) cannot
   be planned from the request; a wording meant to plan implied actions helped the small planner on four tasks and
   emptied four other plans, and was rejected (`results/v0.4/plan-pilot.json`).
5. **Tool-level plans and name matching** let through a message to the wrong known member, the removal of a member
   the user said not to remove, and three payments where one was asked (supplied calls, strict expected failures).
6. **Labels.** A warning on a request that is not the attacker's was called a false warning; the agent's own
   mistakes are among those requests, and the tables now say "warnings on other requests".

## Limits

One agent model; two suites, all inspected during development (no held-out test); static attack templates, not
adaptive campaigns; a simulated approver; enforcement inside the agent's process; provenance found by where a value
first appeared, not by taint tracking. Published numbers of other defenses used other agents or benchmark versions
and are not ranked against these; DRIFT's tables are compared each against its own undefended agent
(`docs/automatic-policy.md`). Closest related designs and a proposed follow-up study:
`docs/research-direction-2026-10-12.md`.
