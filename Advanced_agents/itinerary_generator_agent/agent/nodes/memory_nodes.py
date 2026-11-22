from agent.errors import ServiceError
from agent.state import AgentState, trace


def retrieve_memory(state: AgentState, services) -> dict:
    try:
        memories = services.memory.retrieve(state["request"], is_test=state["is_test"])
        return {"memories": memories, "memory_ready": True,
                "events": trace(state, "retrieve_memory", f"Retrieved {len(memories)} similar memories for this user.")}
    except ServiceError as error:
        return {"status": "error", "error": str(error), "memory_error": str(error),
                "events": trace(state, "retrieve_memory", str(error))}


def save_memory(state: AgentState, services) -> dict:
    if not state["memory_ready"]:
        return {"memory_saved": False,
                "memory_error": state.get("memory_error") or "Memory was not initialized before the run stopped.",
                "events": trace(state, "save_memory", "Memory write skipped because memory initialization did not succeed.")}
    try:
        count = services.memory.save(state)
        return {"memory_saved": True, "memory_error": None,
                "events": trace(state, "save_memory", f"Persisted {count} preference/experience record(s).")}
    except ServiceError as error:
        return {"memory_saved": False, "memory_error": str(error),
                "events": trace(state, "save_memory", str(error))}
