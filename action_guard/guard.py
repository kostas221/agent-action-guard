"""The guard inside an AgentDojo pipeline: every tool call passes through it before it runs.

AgentDojo executes every tool, including tools nested inside another call's arguments,
through `FunctionsRuntime.run_function`. GuardedRuntime overrides that one method, so
there is no path to a tool that skips the guard. For a call the policy marks as needing
approval it asks the approver, records the decision in the approval gate, and runs the
call only if the gate releases an approval for exactly that call.

Pipeline layout (the same as AgentDojo's undefended pipeline, plus the two guard parts):

    StartGuard -> SystemMessage -> InitQuery -> LLM -> loop[GuardedToolsExecutor -> LLM]
"""

import json
from collections.abc import Mapping, Sequence
from dataclasses import asdict

from agentdojo.agent_pipeline.agent_pipeline import AgentPipeline, PipelineConfig
from agentdojo.agent_pipeline.base_pipeline_element import BasePipelineElement
from agentdojo.agent_pipeline.basic_elements import InitQuery, SystemMessage
from agentdojo.agent_pipeline.tool_execution import ToolsExecutionLoop, ToolsExecutor
from agentdojo.functions_runtime import Env, FunctionReturnType, FunctionsRuntime
from agentdojo.logging import Logger, TraceLogger
from agentdojo.types import ChatMessage

from action_guard.approval import ApprovalGate, Approver

REJECTED = "The user rejected this action, so it was not executed."


class Guard:
    """The policy, the approver, and the approval gate of the task being run."""

    def __init__(self, policy, approver: Approver) -> None:
        self.policy = policy
        self.approver = approver
        self.gate = ApprovalGate()

    def start_task(self) -> None:
        self.gate = ApprovalGate()  # approvals never carry over to another task

    def check(self, tool: str, args: Mapping, query: str, env: Env, messages: Sequence[ChatMessage]) -> str | None:
        """None if the call may run now; otherwise the error the agent receives instead of the tool's output."""
        if not self.policy.needs_approval(tool):
            return None
        review = self.policy.review(tool, args, query, env, messages)
        request = self.gate.request(tool, args, review.summary, review.warnings)
        self.gate.decide(request.id, self.approver.decide(request))
        released = self.gate.consume(tool, args)
        self.save_to_trace()
        return None if released else REJECTED

    def records(self) -> list[dict]:
        """The task's approval requests as plain JSON data."""
        rows = []
        for request in self.gate.requests:
            row = asdict(request)
            row["status"] = request.status.value
            rows.append(json.loads(json.dumps(row, default=str)))
        return rows

    def save_to_trace(self) -> None:
        logger = Logger.get()
        if isinstance(logger, TraceLogger):
            logger.context["guard"] = {"approver": self.approver.name, "requests": self.records()}


class GuardedRuntime(FunctionsRuntime):
    """The task's tools, run only through the guard."""

    def __init__(self, runtime: FunctionsRuntime, guard: Guard, query: str, messages: Sequence[ChatMessage]) -> None:
        super().__init__()
        self.functions = runtime.functions
        self.guard = guard
        self.query = query
        self.messages = messages

    def run_function(
        self, env, function: str, kwargs: Mapping, raise_on_error: bool = False
    ) -> tuple[FunctionReturnType, str | None]:
        refusal = self.guard.check(function, kwargs, self.query, env, self.messages)
        if refusal is not None:
            if raise_on_error:  # a nested call: the outer call fails with this error
                raise PermissionError(refusal)
            return "", refusal
        return super().run_function(env, function, kwargs, raise_on_error)


class GuardedToolsExecutor(ToolsExecutor):
    """AgentDojo's ToolsExecutor, given a guarded runtime that knows the user's message and what the agent read."""

    def __init__(self, guard: Guard) -> None:
        super().__init__()
        self.guard = guard

    def query(
        self,
        query: str,
        runtime: FunctionsRuntime,
        env: Env,
        messages: Sequence[ChatMessage],
        extra_args: dict,
    ) -> tuple[str, FunctionsRuntime, Env, Sequence[ChatMessage], dict]:
        guarded = GuardedRuntime(runtime, self.guard, query, messages)
        query, _, env, messages, extra_args = super().query(query, guarded, env, messages, extra_args)
        return query, runtime, env, messages, extra_args


class StartGuard(BasePipelineElement):
    """First element of the pipeline: a fresh approval gate for every task."""

    def __init__(self, guard: Guard) -> None:
        self.guard = guard

    def query(
        self,
        query: str,
        runtime: FunctionsRuntime,
        env: Env,
        messages: Sequence[ChatMessage],
        extra_args: dict,
    ) -> tuple[str, FunctionsRuntime, Env, Sequence[ChatMessage], dict]:
        self.guard.start_task()
        return query, runtime, env, messages, extra_args


def guarded_pipeline(llm: BasePipelineElement, guard: Guard, name: str) -> AgentPipeline:
    """AgentDojo's undefended pipeline (same default system message) with the guard around tool execution."""
    config = PipelineConfig(llm=llm, model_id=None, defense=None, system_message_name=None, system_message=None)
    pipeline = AgentPipeline(
        [
            StartGuard(guard),
            SystemMessage(config.system_message),
            InitQuery(),
            llm,
            ToolsExecutionLoop([GuardedToolsExecutor(guard), llm]),
        ]
    )
    pipeline.name = name  # the attack addresses the model by name, looked up from the pipeline name
    return pipeline
