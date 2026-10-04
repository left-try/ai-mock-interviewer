"""Small LangGraph orchestration boundary for each interview transition."""

from typing import Any, TypedDict

from langgraph.graph import END, StateGraph


class InterviewGraphState(TypedDict, total=False):
    operation: str
    model: Any
    messages: list[Any]
    response_schema: Any
    result: Any


async def _request_model(state: InterviewGraphState) -> dict:
    result = await state["model"].ainvoke(
        state["messages"], response_schema=state.get("response_schema")
    )
    return {"result": result}


def build_graph():
    graph = StateGraph(InterviewGraphState)
    graph.add_node("request_structured_output", _request_model)
    graph.set_entry_point("request_structured_output")
    graph.add_edge("request_structured_output", END)
    return graph.compile()
