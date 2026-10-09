"""Agent pipelines under test.

"baseline" is the undefended agent, built exactly like AgentDojo's own
`AgentPipeline.from_config` (default system message, YAML tool outputs), so its
numbers are comparable with published ones. "guard-<user>" adds the approval guard
of docs/approval-policy.md, with one simulated user deciding every approval request.
"guard-<judge|hybrid>-follow-warnings" keeps that guard and user and takes the warning
from a model judge, alone or on top of the rules (docs/judge-design.md). These have a
policy written for banking only.
"guard-auto[-hybrid]-follow-warnings" use the automatic policy (docs/automatic-policy.md):
any suite with a tools file in policies/, with a plan made from the request (A1) and, in the
hybrid, the judge in its general wording. "guard-auto-hybrid-mini-follow-warnings" is the hybrid
with the agent's own model as the planner, so that every model in it is the agent's.
"defense-<name>" are AgentDojo's own defenses on the same undefended agent, for a comparison in
the same conditions (transformers_pi_detector is left out: it runs a local classifier model).
"""

import openai
from agentdojo.agent_pipeline.agent_pipeline import AgentPipeline, PipelineConfig
from agentdojo.agent_pipeline.llms.openai_llm import OpenAILLM
from agentdojo.task_suite.load_suites import get_suite

from action_guard.approval import ApproveAll, Approver, FollowWarnings, RejectAll
from action_guard.automatic import AutomaticPolicy, check_tools, load_tools
from action_guard.banking import BankingOracle, BankingPolicy, JudgedBankingPolicy
from action_guard.guard import Guard, guarded_pipeline
from action_guard.judge import GENERAL_KEY_HEADER, GENERAL_SYSTEM, Judge
from action_guard.planner import make_plan, tool_catalog
from action_guard.settings import BENCHMARK_VERSION, DEFAULT_MODEL, HYBRID_JUDGE_VOTES, JUDGE_MODEL, PLANNER_MODEL
from action_guard.usage import PRICES, UsageMeter

APPROVERS = {approver.name: approver for approver in (ApproveAll, RejectAll, FollowWarnings, BankingOracle)}
JUDGED = tuple(f"guard-{mode}-{FollowWarnings.name}" for mode in JudgedBankingPolicy.MODES)
AUTOMATIC = {  # config: (mode, planner model)
    f"guard-auto-{FollowWarnings.name}": ("rules", PLANNER_MODEL),
    f"guard-auto-hybrid-{FollowWarnings.name}": ("hybrid", PLANNER_MODEL),
    f"guard-auto-hybrid-mini-{FollowWarnings.name}": ("hybrid", DEFAULT_MODEL),
}
DEFENSES = {f"defense-{name}": name for name in ("tool_filter", "repeat_user_prompt", "spotlighting_with_delimiting")}
CONFIGS = ("baseline", *(f"guard-{name}" for name in APPROVERS), *JUDGED, *AUTOMATIC, *DEFENSES)
AUTOMATIC_SUITES = ("banking", "slack")  # suites with a checked tools file in policies/


def guarded_suites(config: str) -> tuple[str, ...]:
    """The suites a configuration's guard has a policy for; () without a guard (it runs on any suite)."""
    if not config.startswith("guard-"):
        return ()
    return AUTOMATIC_SUITES if config in AUTOMATIC else ("banking",)


def priced(model: str) -> str:
    if model not in PRICES:
        raise ValueError(f"No price for {model!r}; add it to PRICES in action_guard/usage.py")
    return model


def agent_llm(model: str, meter: UsageMeter) -> OpenAILLM:
    client = meter.wrap_client(openai.OpenAI(max_retries=6), role="agent")
    llm = OpenAILLM(client, priced(model))
    llm.name = model  # the attack addresses the model by name, looked up from the pipeline name
    return llm


def judged_policy(mode: str, meter: UsageMeter) -> JudgedBankingPolicy:
    """The banking policy with a model judge; its tokens and cost are recorded under the role "guard"."""
    client = meter.wrap_client(openai.OpenAI(max_retries=3), role="guard")
    votes = HYBRID_JUDGE_VOTES if mode == "hybrid" else 1
    return JudgedBankingPolicy(Judge(client, priced(JUDGE_MODEL), votes=votes), mode)


def automatic_policy(
    suite_name: str, mode: str, meter: UsageMeter, planner_model: str = PLANNER_MODEL
) -> AutomaticPolicy:
    """The automatic policy; the planner's cost is recorded under "planner", the judge's under "guard"."""
    tools = load_tools(suite_name)
    suite_tools = get_suite(BENCHMARK_VERSION, suite_name).tools
    check_tools(suite_name, tools, suite_tools)
    catalog = tool_catalog(suite_tools)
    planner_client = meter.wrap_client(openai.OpenAI(max_retries=3), role="planner")
    model = priced(planner_model)

    def planner(query: str):
        return make_plan(planner_client, model, query, catalog, tools)

    judge = None
    if mode == "hybrid":
        client = meter.wrap_client(openai.OpenAI(max_retries=3), role="guard")
        judge = Judge(
            client,
            priced(JUDGE_MODEL),
            votes=HYBRID_JUDGE_VOTES,
            system=GENERAL_SYSTEM,
            key_header=GENERAL_KEY_HEADER,
        )
    return AutomaticPolicy(suite_name, tools, planner, judge, mode)


def guarded_agent(model: str, meter: UsageMeter, approver: Approver, policy=None) -> AgentPipeline:
    """The agent with the approval guard; `approver` answers every approval request."""
    return guarded_pipeline(agent_llm(model, meter), Guard(policy or BankingPolicy(), approver), name=model)


def build_pipeline(config: str, model: str, meter: UsageMeter, suite: str = "banking") -> AgentPipeline:
    if config not in CONFIGS:
        raise ValueError(f"Unknown config {config!r}; choose from {CONFIGS}")
    if config == "baseline" or config in DEFENSES:
        llm = agent_llm(model, meter)
        defense = DEFENSES.get(config)
        return AgentPipeline.from_config(
            PipelineConfig(llm=llm, model_id=None, defense=defense, system_message_name=None, system_message=None)
        )
    if suite not in guarded_suites(config):
        raise ValueError(f"{config} has no policy for {suite}")
    if config in AUTOMATIC:
        mode, planner_model = AUTOMATIC[config]
        policy = automatic_policy(suite, mode, meter, planner_model)
        return guarded_agent(model, meter, FollowWarnings(), policy)
    if config in JUDGED:
        mode = config.removeprefix("guard-").split("-", 1)[0]
        return guarded_agent(model, meter, FollowWarnings(), judged_policy(mode, meter))
    return guarded_agent(model, meter, APPROVERS[config.removeprefix("guard-")]())
