"""Exactly four Agent objects: a manager and three non-delegating specialists."""

from crewai import Agent

from release_crew.config import Settings
from release_crew.errors import ReviewError
from release_crew.evidence import EvidenceSession, ROLES
from release_crew.llm import OpenRouterLLM
from release_crew.tools import specialist_tool


def make_llm(settings: Settings):
    if not settings.openrouter_api_key:
        raise ReviewError("Set OPEN_ROUTER_API_KEY in the shared .env before running a review.")
    model = settings.model.removeprefix("openrouter/")
    return OpenRouterLLM(
        model=model, base_url=settings.openrouter_url, api_key=settings.openrouter_api_key,
        temperature=0, timeout=settings.model_timeout, max_tokens=4000,
        context_window_size=131072, reasoning_effort="low",
    )


def create_agents(session: EvidenceSession, settings: Settings, llm=None):
    model = llm if llm is not None else make_llm(settings)
    manager = Agent(
        role="Release manager", goal="Delegate to all three specialists and consolidate an evidence-backed release recommendation aligned with deployment policy.",
        backstory="You supervise QA, security, and documentation reviews. You do not replace specialists or invent evidence.",
        allow_delegation=True, llm=model, max_iter=settings.max_iterations,
        max_retry_limit=1, verbose=False,
    )
    goals = {
        "qa": "Assess current test outcomes and bug severity against the selected environment's gates.",
        "security": "Assess vulnerabilities and incomplete scan evidence without confusing scan execution with remediation.",
        "documentation": "Assess required-document completeness and release-note approval under the selected policy.",
    }
    workers = {
        domain: Agent(
            role=role, goal=goals[domain],
            backstory=f"You are the {role} for the fictional Shopflow application. Use your evidence tool, cite exact references, and report to the Release manager.",
            tools=[specialist_tool(session, domain)], allow_delegation=False,
            llm=model, max_iter=settings.max_iterations, max_retry_limit=1, verbose=False,
        )
        for domain, role in ROLES.items()
    }
    return manager, workers
