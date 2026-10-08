"""Run OpenAI, Anthropic, Agents SDK, and LangGraph agents as governed workflow steps."""

from __future__ import annotations

from typing import Any, NotRequired, TypedDict

from temporalio import workflow

from agentworkflows.workflows import WorkflowGateway, input_schema


class BriefingState(TypedDict):
    topic: str
    answer: NotRequired[str]


@input_schema(
    {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "description": "Runs one topic through the OpenAI, Anthropic, Agents SDK and LangGraph agents.",
        "properties": {
            "topic": {
                "type": "string",
                "description": "A question or subject, in a sentence.",
                "examples": ["How should our team evaluate AI agents?"],
                "minLength": 1,
                "pattern": "\\S",
            }
        },
        "required": ["topic"],
        "additionalProperties": False,
    }
)
@workflow.defn
class FrameworkWorkflow:
    @workflow.run
    async def run(self, request: dict[str, Any] | str) -> dict[str, Any]:
        # Console and API starts send {"topic": ...}; direct Temporal starts may pass the topic string.
        topic = request if isinstance(request, str) else request["topic"]
        gateway = WorkflowGateway()
        results = {}
        for name in ("openai", "anthropic", "agents-sdk", "langgraph"):
            results[name] = await gateway.agent(name, {"topic": topic})
        return {"run_id": workflow.info().run_id, "agents": results}


@workflow.defn
class CodeWorkflow:
    @workflow.run
    async def run(self, task: str) -> str:
        return await WorkflowGateway().container("coder", {"task": task})


async def openai_agent(context: Any, arguments: dict[str, Any]) -> str:
    sources = await context.tool("team.search", {"query": arguments["topic"]})
    reply = await context.openai().chat.completions.create(
        model="demo-openai",
        max_tokens=128,
        messages=[{"role": "user", "content": f"Summarize: {sources}"}],
    )
    return reply.choices[0].message.content or ""


async def anthropic_agent(context: Any, arguments: dict[str, Any]) -> str:
    reply = await context.anthropic().messages.create(
        model="demo-anthropic",
        max_tokens=128,
        messages=[{"role": "user", "content": arguments["topic"]}],
    )
    return reply.content[0].text


async def agents_sdk_agent(context: Any, arguments: dict[str, Any]) -> str:
    from agents import Agent, ModelSettings, RunConfig, Runner, function_tool

    @function_tool
    async def search(query: str) -> str:
        """Search the team's approved sources."""
        return str(await context.tool("team.search", {"query": query}))

    agent = Agent(
        name="Briefing",
        instructions="Give a concise team briefing.",
        model=context.agents_model("demo-openai"),
        model_settings=ModelSettings(max_tokens=128),
        tools=[search],
    )
    result = await Runner.run(agent, arguments["topic"], max_turns=3, run_config=RunConfig(tracing_disabled=True))
    return str(result.final_output)


async def langgraph_agent(context: Any, arguments: dict[str, Any]) -> str:
    from langgraph.graph import END, START, StateGraph

    async def summarize(state: BriefingState) -> dict:
        reply = await context.openai().chat.completions.create(
            model="demo-openai",
            max_tokens=128,
            messages=[{"role": "user", "content": state["topic"]}],
        )
        return {"answer": reply.choices[0].message.content}

    graph = StateGraph(BriefingState)
    graph.add_node("summarize", summarize)
    graph.add_edge(START, "summarize")
    graph.add_edge("summarize", END)
    result = await graph.compile().ainvoke(arguments)
    return str(result["answer"])


AGENTS = {
    "openai": openai_agent,
    "anthropic": anthropic_agent,
    "agents-sdk": agents_sdk_agent,
    "langgraph": langgraph_agent,
}


async def main() -> None:
    import json
    import os
    import sys
    from datetime import timedelta
    from uuid import uuid4

    from temporalio.client import Client

    client = await Client.connect(os.getenv("TEMPORAL_ADDRESS", "localhost:7233"))
    result = await client.execute_workflow(
        FrameworkWorkflow.run,
        " ".join(sys.argv[1:]) or "Evaluate team agents",
        id=f"{os.getenv('AGENTWORKFLOWS_TEAM', 'demo')}/default/{uuid4()}",
        task_queue=os.getenv("TEMPORAL_TASK_QUEUE", f"{os.getenv('AGENTWORKFLOWS_TEAM', 'demo')}-workflows"),
        execution_timeout=timedelta(minutes=5),
    )
    print(json.dumps(result))


if __name__ == "__main__":
    import asyncio

    asyncio.run(main())
