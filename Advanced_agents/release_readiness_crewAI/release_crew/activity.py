"""Scoped tool hooks work for both native function calling and ReAct agents."""

from release_crew.context import current_session
from release_crew.evidence import ROLES


def record_tool_call(context) -> None:
    session = current_session()
    role = getattr(context.agent, "role", "Unknown agent")
    tool = context.tool_name
    if role == "Release manager" and "delegate" in tool.lower():
        coworker = str(context.tool_input.get("coworker", "")).strip().casefold()
        for domain, specialist in ROLES.items():
            if coworker == specialist.casefold():
                session.delegated_domains.add(domain)
                session.event(role, "delegated_review", f"Delegated review to {specialist}.")
                return
    session.event(role, "tool_called", tool)
