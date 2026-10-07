# An automatic policy: warnings from a plan made before the agent reads anything

Version 0.3, in progress. The banking warnings W1-W5 were written by hand, for one suite. The
strongest published defenses on AgentDojo build their constraints for each task with a model,
from the user's request alone: DRIFT (a planner and a parameter checklist, then a validator)
and AuthGraph (an authorization graph built in a clean context). That is what lets them run on
every suite. **Can a policy made that way, from the tools' own descriptions and the user's
request, replace the rules written by hand: first where there are some (banking), then where
there are none (Slack)?**

Code: `action_guard/planner.py`; offline replay: `planner_replay.py`.

## Design

Two model calls, both from trusted text only, never from tool outputs:

1. **Tools, once per suite.** For each tool: does it only read, or does it act (change something,
   or send or reveal information to anyone, including a tool that contacts an address the agent
   chooses)? For each argument of an acting tool: who or where the action goes (target), a
   secret, which existing item (selector), or content. Saved to `policies/<suite>-tools.json`
   and checked by a person, like a tool's own annotations.
2. **A plan, once per task.** Which actions the request needs, and where each argument should
   come from: typed by the user, someone the user already deals with, data the request points to
   (a reading tool and a name the request uses, such as a file), computed, or free (a subject, a
   date).

Three checks, which only add warnings:

- **A0**, from the tools file, never relaxed by a plan: who or where an action goes must be
  typed by the user or be someone the user already deals with; a secret must be typed by the
  user. In banking this is W1-W2, derived instead of written.
- **A1**: an action the plan does not expect.
- **A2**: an argument that is not where the plan says it comes from. For data, the value must
  appear in an output of the planned reading tool, on the named item if the request names one.

What stays as it is: the approval of every action, bound to the exact call; the judge of the
hybrid, which may clear a provenance warning; the history signals W4-W5 as a banking add-on. The
only suite-specific code left is how to list who the user already deals with (in banking: the
accounts paid before the task).

**Warn, not block.** DRIFT blocks a deviation from its plan. Here the request still waits for
the user, with the reason: a wrong plan costs a question, not a task lost silently. With the
follow-warnings user a wrong plan is a rejection, so it shows as lost utility and is measured.
A plan that fails is empty: every action is then unexpected and warned (fail closed).

The planner cannot see a document, so it cannot know whether the new rent is written in the
landlord's notice or has to be computed from it; it is asked to choose. A wrong choice is an A2
warning on a correct request; the replay counts them.

## Phase A: the stored banking requests, offline

No agent runs. Every approval request stored in the runs of releases 0.1 to 0.2.0 (9
configurations, 6,044 requests) is checked as the guard would have checked it: the request, the
call, the conversation before it, the account as it was when the task started. Model:
`gpt-4o-mini-2024-07-18`, temperature 0; one call for the tools, five plans per user task
(16 tasks), since the model does not always answer the same way. A few cents.

Labels, computed by code before any model call:

- **attacker** (2,979): the attacker's own account or password (as everywhere in this project);
- **correct** (1,797): the call AgentDojo's own solution of the task makes, on the recipient,
  amount, id, password, street and city (street and city matched loosely, as AgentDojo's own
  check of task 15 does; the solution's "Spotify" and "Apple" read as the accounts the user paid
  them on);
- **other** (1,268): neither, mostly the agent's mistakes (a wrong amount, a wrong account, an
  action no one asked for).

The hand-written rules W1-W3 on the same requests, computed before any model call:

| Requests | Warned by W1-W3 |
|---|---|
| attacker (2,979) | 100% (2,979) |
| correct (1,797) | 15.8% (284): all of them the bill of task 0 (23) and the address of task 13 (261), both on purpose |
| other (1,268) | 9.5% (120) |

### Stated before running

1. **Tools:** the five tools the rules guard are classified as acting and the other six as
   reading; the recipient is a target, the password a secret, the scheduled payment's id a
   selector.
2. **Attacker requests:** warned in 100% of them with every one of the five plans, by A0 alone
   (each of them sends money to the attacker's account or sets the attacker's password).
3. **Correct requests:** fewer warnings than the rules' 15.8%. Task 0 (23) stays warned by A0, a
   new payee taken from the bill. Task 13 (261) is no longer warned in at least 4 of 5 plans:
   the plan points the address to the file the user named, so the policy clears it without a
   judge, and would clear a forged file the same way, as the hybrid does. Tasks 2 and 12 (185)
   are warned in the plans that say the new rent is read from the notice rather than computed;
   I expect at least one such plan of five. The other correct requests (1,328) are warned in
   under 10% of request-plan pairs.
4. **Other requests:** more warnings than the rules' 9.5%, at least 25% of request-plan pairs:
   actions no one asked for (A1) and amounts from the wrong place (A2), such as the whole
   balance sent as a refund.
5. **Stability:** for at least 12 of the 16 tasks, the five plans list the same actions.

Rule for what comes next: if 2 fails, stop and find why. If the correct requests are warned
more than 5 points above the rules (pooled over the five plans), look at which tasks and why
before any paid run. Otherwise go to phase B, Slack.

The prompts were written by someone who knows the banking tasks, so banking is not a blind test
of them. Any change made to the prompts after this replay is tested on Slack, whose tasks the
prompts have never seen.
