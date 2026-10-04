"""Agent pipelines under test.

Phase 1 has only the undefended baseline, built exactly like AgentDojo's own
`AgentPipeline.from_config` (default system message, YAML tool outputs), so its
numbers are comparable with published ones. Guards are added here in Phase 2.
"""

import openai
from agentdojo.agent_pipeline.agent_pipeline import AgentPipeline, PipelineConfig
from agentdojo.agent_pipeline.llms.openai_llm import OpenAILLM

from action_guard.usage import PRICES, UsageMeter

CONFIGS = ("baseline",)


def build_pipeline(config: str, model: str, meter: UsageMeter) -> AgentPipeline:
    if config not in CONFIGS:
        raise ValueError(f"Unknown config {config!r}; choose from {CONFIGS}")
    if model not in PRICES:
        raise ValueError(f"No price for {model!r}; add it to PRICES in action_guard/usage.py")

    client = meter.wrap_client(openai.OpenAI(max_retries=6), role="agent")
    llm = OpenAILLM(client, model)
    llm.name = model  # the attack addresses the model by name, looked up from the pipeline name
    return AgentPipeline.from_config(
        PipelineConfig(llm=llm, model_id=None, defense=None, system_message_name=None, system_message=None)
    )
