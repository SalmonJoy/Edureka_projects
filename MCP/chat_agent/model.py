"""Only this module knows how to call the Ollama model."""

from typing import Any

import httpx

from chat_agent.config import Settings


class ModelError(RuntimeError):
    """An expected model failure that can be shown in the terminal."""


class OllamaModel:
    def __init__(self, settings: Settings):
        self.model = settings.ollama_model
        self.client = httpx.AsyncClient(
            base_url=settings.ollama_host.rstrip("/") + "/",
            headers={"Authorization": f"Bearer {settings.ollama_api_key}"},
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
            # Send the raw MCP schemas unchanged. Some Ollama SDK versions
            # narrow JSON schemas and drop nested $ref/properties fields.
            response = await self.client.post(
                "api/chat",
                json={
                    "model": self.model,
                    "messages": messages,
                    "tools": tools,
                    "stream": False,
                    "options": {"temperature": 0},
                },
            )
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 402:
                raise ModelError(
                    "Ollama returned HTTP 402 (Payment Required). "
                    "Check your Ollama cloud account's usage or billing."
                ) from None
            raise ModelError(
                f"Ollama returned HTTP {exc.response.status_code}. "
                "Check OLLAMA_API_KEY, OLLAMA_HOST, and OLLAMA_MODEL."
            ) from None
        except httpx.TimeoutException:
            raise ModelError("Ollama timed out after 120 seconds.") from None
        except (httpx.RequestError, ConnectionError):
            raise ModelError("Could not reach Ollama. Check the host and connection.") from None

        try:
            message = response.json()["message"]
            if not isinstance(message, dict) or message.get("role") != "assistant":
                raise ValueError("Expected an assistant message")
            for call in message.get("tool_calls") or []:
                function = call["function"]
                if not isinstance(function["name"], str) or not isinstance(
                    function["arguments"], dict
                ):
                    raise ValueError("Invalid tool call")
            if not isinstance(message.get("content", ""), str):
                raise ValueError("Expected text content")
        except (ValueError, KeyError, TypeError):
            raise ModelError("Ollama returned an invalid chat response.") from None

        # Keep the complete assistant message for the next model request,
        # including tool calls and any thinking field. Only final text is shown.
        return message
