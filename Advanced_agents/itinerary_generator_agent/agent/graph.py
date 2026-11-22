"""Read this file first to see the complete planner/executor/replanner loop."""

from functools import partial

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph

from agent.nodes.context import load_context
from agent.nodes.evaluator import reflect
from agent.nodes.executor import execute_step, pause_before_step
from agent.nodes.memory_nodes import retrieve_memory, save_memory
from agent.nodes.planner import planner
from agent.nodes.replanner import replanner
from agent.state import AgentState

TERMINAL = {"complete", "incomplete", "error"}


def build_graph(services, checkpointer=None):
    graph = StateGraph(AgentState)
    for name, function in [
        ("catalog", load_context), ("retrieve_memory", retrieve_memory),
        ("planner", planner), ("reflection", reflect),
        ("executor", execute_step), ("replanner", replanner), ("save_memory", save_memory),
    ]:
        graph.add_node(name, partial(function, services=services))
    graph.add_node("pause", pause_before_step)
    graph.add_edge(START, "catalog")

    def next_unless_stopped(next_node):
        return lambda state: "save_memory" if state["status"] in TERMINAL else next_node

    for source, target in [("catalog", "retrieve_memory"), ("retrieve_memory", "planner"), ("planner", "reflection"), ("replanner", "reflection")]:
        graph.add_conditional_edges(source, next_unless_stopped(target), [target, "save_memory"])

    def after_reflection(state):
        if state["status"] in TERMINAL:
            return "save_memory"
        return "pause" if state["reflection"]["passed"] else "replanner"

    def after_execution(state):
        if state["status"] in TERMINAL:
            return "save_memory"
        if state["observations"][-1]["outcome"] == "unexpected":
            return "replanner"
        return "pause" if state["pending"] else "reflection"

    graph.add_conditional_edges("reflection", after_reflection, ["save_memory", "pause", "replanner"])
    graph.add_edge("pause", "executor")
    graph.add_conditional_edges("executor", after_execution, ["save_memory", "replanner", "pause", "reflection"])
    graph.add_edge("save_memory", END)
    return graph.compile(checkpointer=checkpointer if checkpointer is not None else InMemorySaver())
