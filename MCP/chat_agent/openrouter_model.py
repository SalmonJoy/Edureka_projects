"""Adapt OpenRouter chat completions to the same small model interface."""

import json
from typing import Any

import httpx

from chat_agent.config import Settings
from chat_agent.model import ModelError


def to_openrouter_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """OpenRouter needs JSON argument strings and matching tool-call IDs."""
    converted = []
    for message in messages:
        item = {"role": message["role"], "content": message.get("content", "")}
        if message["role"] == "assistant":
            if message.get("tool_calls"):
                item["tool_calls"] = [
                    {
                        "id": call["id"],
                        "type": "function",
                        "function": {
                            "name": call["function"]["name"],
                            "arguments": json.dumps(call["function"]["arguments"]),
                        },
                    }
                    for call in message["tool_calls"]
                ]
            # Some reasoning models require these details on follow-up calls.
            if message.get("reasoning_details"):
                item["reasoning_details"] = message["reasoning_details"]
        elif message["role"] == "tool":
            item["tool_call_id"] = message["tool_call_id"]
        converted.append(item)
    return converted


class OpenRouterModel:
    def __init__(self, settings: Settings):
        self.model = settings.openrouter_model
        self.client = httpx.AsyncClient(
            base_url=settings.openrouter_host.rstrip("/") + "/",
            headers={"Authorization": f"Bearer {settings.openrouter_api_key}"},
            timeout=120.0,
        )

    async def __aenter__(self):
        await self.client.__aenter__()
        return self

    async def __aexit__(self, *args):
        return await self.client.__aexit__(*args)

    async def chat(
        self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]
    ) -> dict[str, Any]:
        try:
            response = await self.client.post(
                "chat/completions",
                json={
                    "model": self.model,
                    "messages": to_openrouter_messages(messages),
                    "tools": tools,
                    "stream": False,
                    "temperature": 0,
                    "max_tokens": 4096,
                },
            )
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise ModelError(
                f"OpenRouter returned HTTP {exc.response.status_code}. "
                "Check OPEN_ROUTER_API_KEY, model availability, and account limits."
            ) from None
        except httpx.TimeoutException:
            raise ModelError("OpenRouter timed out after 120 seconds.") from None
        except httpx.RequestError:
            raise ModelError("Could not reach OpenRouter. Check the connection.") from None

        try:
            message = response.json()["choices"][0]["message"]
            if not isinstance(message, dict) or message.get("role") != "assistant":
                raise ValueError("Expected an assistant message")
            # Tool-only responses may have null content. The agent uses text.
            message["content"] = message.get("content") or ""
            if not isinstance(message["content"], str):
                raise ValueError("Expected text content")
            for call in message.get("tool_calls") or []:
                if not isinstance(call["id"], str) or not call["id"]:
                    raise ValueError("Missing tool-call ID")
                function = call["function"]
                function["arguments"] = json.loads(function["arguments"])
                if not isinstance(function["name"], str) or not isinstance(
                    function["arguments"], dict
                ):
                    raise ValueError("Invalid tool call")
        except (ValueError, KeyError, IndexError, TypeError):
            raise ModelError("OpenRouter returned an invalid chat response.") from None
        return message
