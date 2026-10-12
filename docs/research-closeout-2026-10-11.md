# Agent Action Guard: research-only closeout

Decision recorded on 11 October 2026 against commit `17e0884`.
Status: active scope and proposed work; the corrections below are not implemented by this document.

The user has chosen to finish Agent Action Guard as a research project and then continue Reliable Refund Agent,
with a longer-term goal of a product for a real e-shop. This plan supersedes the implementation obligations and
release gates in `roadmap-2026-10-10.md`. That broader document remains architectural background, not a checklist
that must be completed before moving on. The assessment remains evidence about the inspected snapshot.

## Objective and research question

Deliver a reproducible, bounded study of exact-call approval and warning policies in AgentDojo: what attacks
they prevent, what legitimate work they obstruct, how a model judge changes that tradeoff, and where the policies
fail when challenged directly. Preserve the existing banking/Slack evidence and make its limitations inspectable.

The release is not a generally secure or production-ready authorization service. Research quality is judged by
correct experiments, reproducibility and defensible claims; known semantic failures may be valuable findings.
There is no requirement to reach zero attacks, prove utility equivalence or obtain a numerical "10/10".

## Required closeout work

Complete these five bounded items. No new policy architecture, benchmark suite or paid experiment is implied.

### C1. Repair the approval-contract defect

Validate loaded automatic-policy declarations at runtime. Reject malformed/missing effect and argument-role
entries, invalid types and incompatible tool coverage instead of interpreting them as approval-free reads.
The reproduced `effect="act"` path must no longer silently execute a mutation. Check the parsed configuration,
not only a classifier's output schema. Retain the exact-call, single-use approval contract.

Use focused regression tests against the guarded simulated dispatcher. Replay valid stored requests and identify
any changed decisions. Broad provenance, content and task-grant improvements are not part of this repair.

Done: malformed declarations fail safely before dispatch, existing valid behavior is accounted for, and the
advertised approval contract agrees with the supported runtime. A new contract-breaking defect must be repaired
or the affected configuration/claim withdrawn before release.

### C2. Make evaluation labels describe what was actually measured

Correct `planner_replay.is_correct` or rename its current narrow comparison so it cannot present the C1
personal-data leak as a safely correct action. Separate task correctness, authorization, attacker involvement
and actual effects; allow legitimate alternative solutions where the evaluator supports them.

Do not call every warning on a non-attacker request a false positive: an ordinary agent mistake can deserve a
warning. Use a descriptive name unless correctness has been independently adjudicated. Preserve official
AgentDojo scores alongside executed-effect diagnostics and API/error counts.

Retain historical results and scoring definitions. Any relabelled/recomputed report receives a distinct scoring
version and an explanation; do not imply that newly rescored traces are new model runs.

Done: gold fixtures expose the known misleading labels, published metric names match their definitions, and
historical versus corrected measurements are distinguishable.

### C3. Prevent invalid final reports

Share cohort validation across report, compare and bootstrap: expected cells, compatible manifests, unique runs,
paired coverage and missing/interrupted/corrupt records. Partial progress may be displayed as provisional but
cannot replace final artifacts. Include separate run and evaluation identities and deterministic report inputs.

Keep the existing task-level uncertainty analysis and explain what it conditions on. Repeated runs of a small
task set are not independent evidence of generalization. An interval containing zero does not establish equal
utility; zero observed attack successes do not establish zero risk.

Done: invalid cohorts cannot silently publish final tables, prior final artifacts survive unsuccessful reporting,
and all released tables can be reconstructed offline from the supplied, appropriately sanitized evidence.

### C4. Publish the failure and legitimate-twin matrix

Use existing local fixtures, stored plans and replay tooling to document the known amount/address/content gaps,
known-recipient abuse, forged expected sources and dynamic-plan limitations. Cover each released applicable
configuration; mark unsupported combinations and missing evidence explicitly. Pair malicious cases with legitimate
counterparts so a reject-all policy cannot appear useful merely by blocking attacks.

Separate three kinds of evidence: a supplied tool call accepted by the guard, a full-agent attack that elicited
that call, and a theoretical/unmeasured concern. Include policy/judge limitations as negative results. Oracle
approval is a privileged diagnostic using benchmark attacker values, not a deployable policy or a bound on
real-user performance. Simulated approvers are not evidence of how real users behave.

An unresolved semantic case can remain an explicit expected failure. Every such case needs a reproducible fixture,
an explanation of its consequence and a corresponding limitation on claims. Do not delete a failing fixture or
turn it into a passing test merely by weakening its expected security behavior.

Done: a reader can reproduce the known boundary between successful enforcement, unsafe accepted calls and useful
legitimate behavior without paid calls. Gaps in replay coverage remain labelled rather than replaced by guesses.

### C5. Release a coherent research artifact and stop

Update README, version/status descriptions, threat model, results, demo and reproduction instructions. Resolve the
README/package/current-experiment mismatch. Provide a compact capability matrix, release/checksum identities and
an explicit list of limitations. Demonstrate both a blocked attack and a documented boundary of the defense.

Run appropriate free tests/lint and the existing prerun workflow when its triggers apply. Verify reproduction from
a clean setup; distinguish offline trace replay from rerunning stochastic APIs. Record commands and outcomes.
The previous snapshot's 225 passing tests and six expected failures are historical, not proof that these planned
changes passed. Do not alter raw historical evidence to make it agree with changed code.

If behavior changes, old live results remain results of the old configuration. Replay can establish changed
decisions on stored calls, but cannot establish the counterfactual full-agent trajectory. Publish that distinction
or obtain an explicitly approved new paired evaluation; do not attach old success rates to an untested candidate.

Done: the five closeout criteria are checked, artifacts reproduce, and every performance/security claim is
supported at its stated scope. At that point, continue the refund project. Publication or a GitHub release follows
the applicable repository/user authorization; this document does not itself publish anything.

## Optional work and explicit stopping rule

One bounded extension may be selected only if it answers a specific remaining research question: an already
planned second attack template, or a small frozen adaptive pilot. It needs a declared matrix, budget, stopping
rule and separate results. It is not required to finish this narrow research release. No paid run is authorized
by this scope decision, and no paid command is supplied here.

Do not require three agent models, a new dynamic benchmark, a powered three-percentage-point noninferiority
study, a human-participant study, an external red-team campaign or formal verification for this release. Those
are reasonable future investigations when the intended claim and resources justify them.

Stop expanding after C1-C5 are complete. A newly found approval-contract or evaluation defect reopens the affected
item; ordinary negative security findings become documented limitations. If a wider claim needs more evidence,
narrow the claim instead of automatically launching a larger study. This is completion of a research project,
not certification for real financial execution.

## Relationship to the previous roadmap

| Earlier packages | Treatment now |
|---|---|
| W00, focused W01, R01 | Keep baseline/claim boundaries, runtime validation and trustworthy measurement. |
| R02 | Keep bounded offline probes and existing evidence; adaptive expansion is optional. |
| R03 | Keep fair comparisons, uncertainty and data-split honesty; no mandatory broad model-transfer claim. |
| R04 | Keep artifact/replay reproducibility; defer powered human studies and broad independent campaigns. |
| W02-W05 | Defer new grant/provenance/replanning architecture; document existing semantic failures. |
| W06-W09 | Defer generic isolated execution, durable service infrastructure, production identity/UI and formal review. |
| W10 | Keep ordinary repository hygiene; defer deployment, operational service and staged production rollout. |

## Where production engineering belongs next

The existing `reliable-refund-agent/docs/plan.md` and `docs/product-contract.md` already define a narrower domain
with backend authorization, a database, immutable proposals, customer confirmation, staff approval, concurrency
tests and recovery. Reuse lessons and small tested components where they fit; do not require a generic production
Guard service first, or import the AgentDojo runtime as the refund product's security boundary.

The inspected refund plan defines v1 as a virtual shop with simulated money in PostgreSQL. The user's goal of a
real e-shop is a subsequent integration milestone, not evidence that the current v1 already handles real payments.
Do not change the other project's frozen scope here. Before real use, validate the workflow with the actual
merchant, choose the real connector, use its sandbox, and verify its refund lifecycle, authorization and retry
semantics. A committed local demo receipt cannot prove that an external refund completed.

Current primary references for that distinction: [Stripe refund lifecycle](https://docs.stripe.com/refunds) and
[Shopify refundCreate](https://shopify.dev/docs/api/admin-graphql/latest/mutations/refundcreate). These document
provider behavior; they do not establish customer demand or commercial viability for the proposed product.

The strategic recommendation is to finish the bounded research artifact and invest subsequent production effort
in the concrete refund workflow. Revisit a generic Guard product only if real integrations or an identified user
need justify it, rather than because its research assessment had a low production-readiness score.
