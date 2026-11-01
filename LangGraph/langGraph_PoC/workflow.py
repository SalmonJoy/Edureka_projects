"""The eight graph nodes, deterministic date arithmetic, and graph wiring."""

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from langGraph_PoC.clients import SubmissionUnknownError, WorkflowError
from langGraph_PoC.conversation_memory import conversation_context
from langGraph_PoC.models import ExtractedRequest, LeaveDetails, LeaveState


def count_weekdays(start: date, end: date) -> int:
    """Count Monday-Friday dates inclusively, without a holiday calendar."""
    if end < start:
        return 0
    weeks, remainder = divmod((end - start).days + 1, 7)
    return weeks * 5 + sum((start.weekday() + offset) % 7 < 5 for offset in range(remainder))


def end_date_for_days(start: date, days: int) -> date:
    """Find the date of the nth weekday on or after start."""
    if days <= 0:
        raise ValueError("Requested days must be positive")
    cursor = start
    if cursor.weekday() >= 5:
        cursor += timedelta(days=7 - cursor.weekday())
    weeks, remainder = divmod(days - 1, 5)
    cursor += timedelta(days=weeks * 7)
    for _ in range(remainder):
        cursor += timedelta(days=1)
        if cursor.weekday() >= 5:
            cursor += timedelta(days=7 - cursor.weekday())
    return cursor


def initial_state(request: str, *, today: date | None = None) -> LeaveState:
    if not request.strip():
        raise ValueError("Please enter a nonblank leave request.")
    today = today or datetime.now(ZoneInfo("Asia/Kolkata")).date()
    return {
        "original_request": request.strip(), "current_date": today.isoformat(),
        "messages": [],
        "clarification_history": [], "clarification_question": None,
        "confirmed": False, "status": "understanding", "error": None,
    }


def normalize_request(extracted: ExtractedRequest) -> tuple[LeaveDetails | None, str | None]:
    """Resolve counts in Python and ask for facts the LLM could not extract."""
    start, end = extracted.start_date, extracted.end_date
    problems = []
    if start is None:
        problems.append("What is the leave start date?")
    if extracted.stated_days is not None and extracted.stated_days <= 0:
        problems.append("How many working days do you want to request? Use a positive whole number.")
    if start is not None and end is None and extracted.stated_days is not None and extracted.stated_days > 0:
        try:
            end = end_date_for_days(start, extracted.stated_days)
        except (ValueError, OverflowError):
            problems.append("Please provide a valid end date and working-day count.")
    if end is None and not any("end date" in problem for problem in problems):
        problems.append("What is the end date, or how many working days do you need?")
    if start is not None and end is not None:
        if end < start:
            problems.append("The end date is before the start date. What are the correct dates?")
        elif extracted.stated_days is not None and extracted.stated_days > 0:
            actual = count_weekdays(start, end)
            if actual != extracted.stated_days:
                problems.append(
                    f"Those dates contain {actual} working days, but you requested "
                    f"{extracted.stated_days}. Which dates or day count should I use?"
                )
    if not extracted.reason or not extracted.reason.strip():
        problems.append("What is the reason for your leave?")
    if extracted.clarification_question and extracted.clarification_question.strip():
        problems.append(extracted.clarification_question.strip())
    if problems:
        return None, " ".join(problems)
    return LeaveDetails(
        start_date=start, end_date=end,
        leave_type=(extracted.leave_type or "annual").strip() or "annual",
        reason=extracted.reason.strip(),
    ), None


def failure(message: str, *, status: str = "error") -> dict:
    return {"status": status, "error": message, "final_message": message}


class LeaveWorkflow:
    """Dependencies are injected so tests can exercise the real graph without services."""

    def __init__(self, clients):
        self.clients = clients

    async def understand_request(self, state: LeaveState) -> dict:
        """Node 1: extraction; self-loop checkpoints a clarification before interrupting."""
        history = list(state.get("clarification_history", []))
        question = state.get("clarification_question")
        if question:
            # No LLM call or side effect occurs before this saved interrupt.
            answer = interrupt({
                "kind": "clarification", "question": question,
                "known_details": state.get("extracted_request", {}),
            })
            if not isinstance(answer, str) or not answer.strip():
                return {"status": "clarifying", "clarification_question": question}
            history.append({"question": question, "answer": answer.strip()})

        today = date.fromisoformat(state["current_date"])
        next_monday = today + timedelta(days=(7 - today.weekday()) % 7 or 7)
        try:
            extracted = await self.clients.extract_request({
                "original_request": state["original_request"],
                "conversation_history": conversation_context(state.get("messages", [])),
                "clarification_history": history,
                "current_date": today.isoformat(),
                "current_weekday": today.strftime("%A"),
                "tomorrow": (today + timedelta(days=1)).isoformat(),
                "next_monday": next_monday.isoformat(),
                "timezone": "Asia/Kolkata",
            })
        except WorkflowError as error:
            return failure(str(error))

        leave, question = normalize_request(extracted)
        update = {
            "extracted_request": extracted.model_dump(mode="json"),
            "clarification_history": history, "clarification_question": question,
        }
        if question:
            return {**update, "status": "clarifying"}
        return {
            **update, "leave": leave.model_dump(mode="json"),
            "requested_days": count_weekdays(leave.start_date, leave.end_date),
            "status": "ready",
        }

    async def fetch_employee(self, state: LeaveState) -> dict:
        """Node 2: writes only the employee branch's state fields."""
        try:
            return {"employee": await self.clients.get_employee(), "employee_error": None}
        except WorkflowError as error:
            return {"employee": None, "employee_error": str(error)}

    async def fetch_policies(self, state: LeaveState) -> dict:
        """Node 3: runs in parallel with Node 2 and writes different fields."""
        query = state["original_request"]
        for item in state.get("clarification_history", []):
            query += "\nClarification: " + item["answer"]
        try:
            return {"policy_chunks": await self.clients.get_policies(query), "policy_error": None}
        except WorkflowError as error:
            return {"policy_chunks": None, "policy_error": str(error)}

    async def check_eligibility(self, state: LeaveState) -> dict:
        """Node 4: policy assessment with deterministic balance/date guards."""
        errors = [state.get("employee_error"), state.get("policy_error")]
        if any(errors):
            return failure(" ".join(error for error in errors if error))
        leave = LeaveDetails.model_validate(state["leave"])
        today = date.fromisoformat(state["current_date"])
        requested_days = count_weekdays(leave.start_date, leave.end_date)
        # Notice excludes today and includes the requested start date.
        notice = count_weekdays(today + timedelta(days=1), leave.start_date)
        try:
            verdict = await self.clients.assess_eligibility({
                "current_date": state["current_date"], "timezone": "Asia/Kolkata",
                "employee": state["employee"], "leave": state["leave"],
                "requested_days": requested_days, "working_days_notice": notice,
                "notice_convention": "Count weekdays after today through start_date, inclusively.",
                "policy_chunks": state["policy_chunks"],
            })
        except WorkflowError as error:
            return failure(str(error))

        guards = []
        if leave.start_date < today:
            guards.append("The leave start date is in the past.")
        if requested_days <= 0:
            guards.append("The requested dates contain no working days.")
        balance = state["employee"]["leave_balance"]
        if requested_days > balance:
            guards.append(f"Requested {requested_days} working days, but only {balance} days are available.")
        known_ids = {chunk["chunk_id"] for chunk in state["policy_chunks"]}
        cited_ids = set(verdict.supporting_chunk_ids)
        supported = verdict.policy_supported and bool(cited_ids) and cited_ids <= known_ids
        if not supported:
            guards.append("The retrieved policy evidence does not support eligibility for this request.")
        eligible = verdict.eligible and supported and not guards
        reasons = guards + list(verdict.reasons)
        decision = verdict.model_dump(mode="json")
        decision.update({
            "eligible": eligible, "policy_supported": supported,
            "reasons": reasons,
            "supporting_chunk_ids": [item for item in verdict.supporting_chunk_ids if item in known_ids],
        })
        return {
            "eligibility": decision, "requested_days": requested_days,
            "status": "awaiting_confirmation" if eligible else "ineligible",
        }

    async def human_review(self, state: LeaveState) -> dict:
        """Node 5: no submission side effects; input() belongs in the CLI."""
        answer = interrupt({
            "kind": "confirmation", "question": "Submit this leave application?",
            "employee": state["employee"], "leave": state["leave"],
            "requested_days": state["requested_days"],
            "leave_balance": state["employee"]["leave_balance"],
            "eligibility": state["eligibility"],
            "validation_error": state.get("confirmation_error"),
        })
        if type(answer) is not bool:
            return {"status": "awaiting_confirmation", "confirmation_error": "Confirmation must be true or false."}
        if not answer:
            return {
                "confirmed": False, "status": "cancelled", "confirmation_error": None,
                "final_message": "Leave application cancelled.",
            }
        return {"confirmed": True, "status": "confirmed", "confirmation_error": None}

    async def explain_ineligibility(self, state: LeaveState) -> dict:
        """Node 6: LLM explanation with a factual fallback."""
        try:
            message = await self.clients.explain_rejection({
                "leave": state["leave"], "requested_days": state["requested_days"],
                "leave_balance": state["employee"]["leave_balance"],
                "eligibility": state["eligibility"], "policy_chunks": state["policy_chunks"],
            })
        except WorkflowError:
            message = "This leave request is not eligible: " + " ".join(state["eligibility"]["reasons"])
        return {"final_message": message}

    async def apply_leave(self, state: LeaveState) -> dict:
        """Node 7: the only node that writes an application through the API."""
        if state.get("confirmed") is not True or not state.get("eligibility", {}).get("eligible"):
            return failure("Leave submission requires eligibility and explicit human confirmation.")
        # A cached receipt should never be posted a second time.
        if state.get("application"):
            return {"status": "submitted"}
        try:
            application = await self.clients.submit_leave(state["employee"], state["leave"])
        except SubmissionUnknownError as error:
            return failure(str(error), status="submission_unknown")
        except WorkflowError as error:
            return failure(str(error))
        return {"application": application, "status": "submitted"}

    async def report_success(self, state: LeaveState) -> dict:
        """Node 8: failures here cannot replay the preceding submission node."""
        application = state["application"]
        leave = application["leave"]
        fallback = (
            f"Leave application {application['application_id']} submitted for "
            f"{leave['start_date']} to {leave['end_date']}."
        )
        try:
            message = await self.clients.success_message(application)
            if not all(value in message for value in (
                application["application_id"], leave["start_date"], leave["end_date"],
            )) or "submitted" not in message.lower() or "approved" in message.lower():
                message = fallback
        except WorkflowError:
            message = fallback
        return {"final_message": message}


def build_graph(clients, *, checkpointer=None):
    nodes = LeaveWorkflow(clients)
    graph = StateGraph(LeaveState)
    graph.add_node("understand_request", nodes.understand_request)
    graph.add_node("fetch_employee", nodes.fetch_employee)
    graph.add_node("fetch_policies", nodes.fetch_policies)
    graph.add_node("check_eligibility", nodes.check_eligibility)
    graph.add_node("human_review", nodes.human_review)
    graph.add_node("explain_ineligibility", nodes.explain_ineligibility)
    graph.add_node("apply_leave", nodes.apply_leave)
    graph.add_node("report_success", nodes.report_success)
    graph.add_edge(START, "understand_request")

    def after_understanding(state):
        if state["status"] in ("error", "cancelled"):
            return END
        if state["status"] == "clarifying":
            return "understand_request"
        return ["fetch_employee", "fetch_policies"]

    graph.add_conditional_edges(
        "understand_request", after_understanding,
        ["understand_request", "fetch_employee", "fetch_policies", END],
    )
    # A single joint edge is a barrier: Node 4 runs once, after BOTH branches.
    graph.add_edge(["fetch_employee", "fetch_policies"], "check_eligibility")
    graph.add_conditional_edges(
        "check_eligibility",
        lambda state: END if state["status"] == "error" else (
            "human_review" if state["eligibility"]["eligible"] else "explain_ineligibility"
        ),
        ["human_review", "explain_ineligibility", END],
    )
    graph.add_conditional_edges(
        "human_review",
        lambda state: "apply_leave" if state["status"] == "confirmed" else (
            "human_review" if state["status"] == "awaiting_confirmation" else END
        ),
        ["apply_leave", "human_review", END],
    )
    graph.add_edge("explain_ineligibility", END)
    graph.add_conditional_edges(
        "apply_leave", lambda state: "report_success" if state["status"] == "submitted" else END,
        ["report_success", END],
    )
    graph.add_edge("report_success", END)
    return graph.compile(checkpointer=checkpointer if checkpointer is not None else InMemorySaver())
