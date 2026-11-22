"""CrewAI's native OpenAI-compatible client, pointed explicitly at OpenRouter."""

import json
from functools import lru_cache

import tiktoken
from crewai.llms.providers.openai.completion import OpenAICompletion


@lru_cache(maxsize=4)
def local_encoding(model: str):
    try:
        return tiktoken.encoding_for_model(model.rsplit("/", 1)[-1])
    except KeyError:
        return tiktoken.get_encoding("o200k_base")


class OpenRouterLLM(OpenAICompletion):
    def get_input_tokens_count(self, messages, tools=None, response_model=None) -> int:
        # OpenRouter has no OpenAI token-count endpoint. Estimate locally for
        # context limits; billed usage is still determined by the actual provider.
        payload = {"messages": messages, "tools": tools}
        if response_model is not None:
            payload["output_schema"] = response_model.model_json_schema()
        text = json.dumps(payload, ensure_ascii=False, default=str)
        return len(local_encoding(self.model).encode(text, disallowed_special=())) + 32
