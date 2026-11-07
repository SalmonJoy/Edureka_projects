"""Optional LangSmith setup shared by the terminal and HTTP workflows."""

import logging
import os
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from urllib.parse import quote, urlsplit
from uuid import NAMESPACE_URL, uuid5

import httpx
import requests
from dotenv import dotenv_values
from langchain_core.tracers.langchain import LangChainTracer
from langsmith import Client, RunTree, tracing_context, utils


logger = logging.getLogger(__name__)
_lock = Lock()
_initialized = False
_client = None
_project = None
_project_id = None


def _redactor(secrets):
    secrets = tuple(str(value) for value in secrets if value)
    hidden = {"authorization", "x-api-key", "api_key", "password", "client_kwargs",
              "reasoning_content"}

    def redact(value):
        if isinstance(value, dict):
            return {key: "[REDACTED]" if str(key).lower() in hidden else redact(item)
                    for key, item in value.items()}
        if isinstance(value, (tuple, list)):
            return type(value)(redact(item) for item in value)
        if isinstance(value, str):
            for secret in secrets:
                value = value.replace(secret, "[REDACTED]")
        return value

    return redact


class _SafeClient(Client):
    """Also redact serialized model reprs, which can contain client headers."""

    def __init__(self, *, redact, **kwargs):
        self._redact_trace = redact
        super().__init__(**kwargs)

    def create_run(self, *args, **kwargs):
        return super().create_run(*self._redact_trace(args), **self._redact_trace(kwargs))

    def update_run(self, *args, **kwargs):
        return super().update_run(*self._redact_trace(args), **self._redact_trace(kwargs))


def configure_tracing(env_file: Path) -> bool:
    """Configure once per process; restart to pick up changed .env settings.

    Explicit callbacks keep the SDK's client and workspace consistent for
    every invocation. No SDK globals or application database files are replaced.
    """
    global _initialized, _client, _project, _project_id
    with _lock:
        if _initialized:
            return _client is not None
        _initialized = True
        # Read the flag before suppressing inherited automatic SDK tracing.
        inherited = dict(os.environ)
        os.environ["LANGSMITH_TRACING"] = "false"
        os.environ["LANGSMITH_TRACING_V2"] = "false"
        utils.get_env_var.cache_clear()
        utils.get_tracer_project.cache_clear()
        try:
            values = dotenv_values(env_file) if env_file.is_file() else {}
        except OSError:
            logger.warning("LangSmith tracing configuration could not be read. Tracing is disabled; "
                           "leave workflows remain available.")
            return False

        def setting(name, default=""):
            return str((values[name] or "") if name in values
                       else inherited.get(name, default)).strip()

        enabled = setting("LANGSMITH_TRACING", "false").lower() == "true"
        if not enabled:
            return False
        try:
            key = setting("LANGSMITH_API_KEY")
            endpoint = setting("LANGSMITH_ENDPOINT", "https://api.smith.langchain.com").rstrip("/")
            agent = setting("LANGSMITH_AGENT_ID")
            environment = setting("LANGSMITH_AGENT_ENVIRONMENT").upper()
            workspace = setting("LANGSMITH_WORKSPACE_ID")
            parts = urlsplit(endpoint)
            if (not key or not agent or environment not in {"LOCAL", "DEVELOPMENT", "STAGING", "PRODUCTION"}
                    or parts.scheme != "https" or not parts.hostname or parts.username
                    or parts.password or parts.query or parts.fragment):
                raise ValueError("Invalid tracing configuration")
            headers = {"X-Api-Key": key}
            if workspace:
                headers["X-Tenant-Id"] = workspace
            with httpx.Client(timeout=10, trust_env=False) as http:
                response = http.get(endpoint + "/api/v1/agents/" + quote(agent, safe=""), headers=headers)
                response.raise_for_status()
                data = response.json()
            selected = next(item for item in data["environments"]
                            if item["environment"].upper() == environment)
            project = selected["tracer_session_name"]
            if not isinstance(project, str) or not project.strip():
                raise ValueError("Missing tracing destination")
            secrets = [value for name, value in {**os.environ, **values}.items()
                       if any(part in name.upper() for part in ("KEY", "PASSWORD", "TOKEN", "URI"))]
            redact = _redactor(secrets)
            session = requests.Session()
            if workspace:
                session.headers["X-Tenant-Id"] = workspace
            # Keep generated trace links in the same region as ingestion.
            web_url = {
                "api.smith.langchain.com": "https://smith.langchain.com",
                "eu.api.smith.langchain.com": "https://eu.smith.langchain.com",
                "apac.api.smith.langchain.com": "https://apac.smith.langchain.com",
                "aws.api.smith.langchain.com": "https://aws.smith.langchain.com",
            }.get(parts.hostname)
            try:
                client = _SafeClient(
                    redact=redact, api_url=endpoint, api_key=key, session=session,
                    workspace_id=workspace or None,
                    web_url=web_url,
                    timeout_ms=10000, anonymizer=redact, hide_metadata=redact,
                )
            except Exception:
                session.close()
                raise
            _client, _project = client, project
            _project_id = selected.get("tracer_session_id")
            # Explicit callbacks propagate to graph nodes and nested Ollama calls.
            # Automatic environment tracing stays off to avoid a second default
            # SDK client or accidentally using an inherited project destination.
            return True
        except Exception:
            logger.warning("LangSmith tracing setup failed. Check the shared .env, agent/environment, "
                           "permissions, and connectivity. Tracing is disabled; leave workflows remain available.")
            return False


def tracing_config() -> dict:
    """Return a fresh callback for each workflow operation, or no callback."""
    if _client is None:
        return {}
    return {"callbacks": [LangChainTracer(client=_client, project_name=_project)]}


@asynccontextmanager
async def workflow_trace(graph, config: dict, state: dict | None = None):
    """One persistent parent for a workflow, including all interrupt resumes.

    Only IDs, timestamps and the destination enter SQLite. GET requests never
    enter this context. A paused parent stays pending until the workflow ends.
    """
    if _client is None:
        yield
        return
    snapshot = None
    if state is None:
        snapshot = await graph.aget_state(config)
        state = dict(snapshot.values)
    if snapshot is not None and not snapshot.next:
        # Completed-workflow recovery may update its transcript. It must not
        # create another trace or reopen the finished parent.
        config["callbacks"] = []
        with tracing_context(enabled=False):
            yield
        return
    saved = state.get("trace_context")
    try:
        parent = RunTree(
            **(saved or {"id": uuid5(NAMESPACE_URL, "edureka-leave:" + config["configurable"]["thread_id"])}),
            name="Leave request", run_type="chain", ls_client=_client,
            **({} if saved else {"project_name": _project}),
            inputs={"request": state.get("original_request", "")},
            extra={"metadata": {"thread_id": config["configurable"]["thread_id"],
                                "conversation_id": state.get("conversation_id")}},
        )
        if not saved:
            parent.post()
            state["trace_context"] = {
                "id": str(parent.id), "trace_id": str(parent.trace_id),
                "dotted_order": parent.dotted_order, "start_time": parent.start_time.isoformat(),
                "project_name": parent.session_name,
            }
    except Exception:
        logger.warning("LangSmith workflow tracing is unavailable; the workflow will continue untraced.")
        config["callbacks"] = []
        with tracing_context(enabled=False):
            yield
        return
    config["callbacks"] = [LangChainTracer(client=_client, project_name=parent.session_name)]
    with tracing_context(parent=parent, client=_client, project_name=parent.session_name, enabled=True):
        if snapshot is not None and not saved:
            await graph.aupdate_state(config, {"trace_context": state["trace_context"]})
        try:
            yield
        finally:
            try:
                latest = await graph.aget_state(config)
                outputs = {"status": latest.values.get("status"),
                           "confirmed": latest.values.get("confirmed", False),
                           "message": latest.values.get("final_message")}
                if latest.values.get("application"):
                    outputs["application_id"] = latest.values["application"]["application_id"]
                # Both RunTree.patch() and Client.update_run(end_time=None)
                # implicitly end runs. Leave paused parents untouched.
                if not latest.next:
                    _client.update_run(
                        parent.id, trace_id=parent.trace_id, dotted_order=parent.dotted_order,
                        outputs=outputs, error=latest.values.get("error"),
                        end_time=datetime.now(timezone.utc),
                    )
            except Exception:
                logger.warning("LangSmith trace status could not be updated; no workflow action was retried.")


def flush_traces():
    """Wait for queued trace writes, useful before ending a short-lived runner."""
    from langchain_core.tracers.langchain import wait_for_all_tracers

    wait_for_all_tracers()
    if _client is not None:
        _client.flush()
