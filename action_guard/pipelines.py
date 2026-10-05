"""Agent pipelines under test.

"baseline" is the undefended agent, built exactly like AgentDojo's own
`AgentPipeline.from_config` (default system message, YAML tool outputs), so its
numbers are comparable with published ones. "guard-<user>" adds the approval guard
of docs/approval-policy.md, with one simulated user deciding every approval request.
The guard has a policy for banking only.
"""

import openai
from agentdojo.agent_pipeline.agent_pipeline import AgentPipeline, PipelineConfig
from agentdojo.agent_pipeline.llms.openai_llm import OpenAILLM

from action_guard.approval import ApproveAll, Approver, FollowWarnings, RejectAll
from action_guard.banking import BankingOracle, BankingPolicy
from action_guard.guard import Guard, guarded_pipeline
from action_guard.usage import PRICES, UsageMeter

APPROVERS = {approver.name: approver for approver in (ApproveAll, RejectAll, FollowWarnings, BankingOracle)}
CONFIGS = ("baseline", *(f"guard-{name}" for name in APPROVERS))
GUARDED_SUITES = ("banking",)


def agent_llm(model: str, meter: UsageMeter) -> OpenAILLM:
    if model not in PRICES:
        raise ValueError(f"No price for {model!r}; add it to PRICES in action_guard/usage.py")
    client = meter.wrap_client(openai.OpenAI(max_retries=6), role="agent")
    llm = OpenAILLM(client, model)
    llm.name = model  # the attack addresses the model by name, looked up from the pipeline name
    return llm


def guarded_agent(model: str, meter: UsageMeter, approver: Approver) -> AgentPipeline:
    """The agent with the banking approval guard; `approver` answers every approval request."""
    return guarded_pipeline(agent_llm(model, meter), Guard(BankingPolicy(), approver), name=model)


def build_pipeline(config: str, model: str, meter: UsageMeter) -> AgentPipeline:
    if config not in CONFIGS:
        raise ValueError(f"Unknown config {config!r}; choose from {CONFIGS}")
    if config == "baseline":
        llm = agent_llm(model, meter)
        return AgentPipeline.from_config(
            PipelineConfig(llm=llm, model_id=None, defense=None, system_message_name=None, system_message=None)
        )
    return guarded_agent(model, meter, APPROVERS[config.removeprefix("guard-")]())
