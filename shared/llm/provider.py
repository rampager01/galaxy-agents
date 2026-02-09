"""LLM abstraction supporting Anthropic, OpenAI, and Ollama."""

import json
import logging

import httpx

log = logging.getLogger("sentinel.llm")


class LLMProvider:
    """Unified interface for LLM calls with optional tool use."""

    def __init__(self, config):
        self.config = config
        self.provider = config.llm_provider

    async def complete(
        self,
        system: str,
        messages: list[dict],
        max_tokens: int = 2000,
        tools: list[dict] | None = None,
        model: str | None = None,
    ) -> dict:
        """Send a completion request to the configured LLM provider.

        Args:
            system: System prompt.
            messages: List of message dicts (role, content).
            max_tokens: Max tokens for the response.
            tools: Optional list of tool definitions (Anthropic format).
            model: Override model name.

        Returns:
            Dict with keys: content (str), tool_calls (list), stop_reason (str)
        """
        if self.provider == "anthropic":
            return await self._anthropic_complete(system, messages, max_tokens, tools, model)
        elif self.provider == "openai":
            return await self._openai_complete(system, messages, max_tokens, tools, model)
        elif self.provider == "ollama":
            return await self._ollama_complete(system, messages, max_tokens, model)
        else:
            raise ValueError(f"Unknown LLM provider: {self.provider}")

    async def _anthropic_complete(self, system, messages, max_tokens, tools, model):
        import anthropic

        client = anthropic.AsyncAnthropic(api_key=self.config.anthropic_api_key)
        model = model or "claude-haiku-4-5-20251001"

        kwargs = {
            "model": model,
            "max_tokens": max_tokens,
            "system": [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
            "messages": messages,
        }
        if tools:
            kwargs["tools"] = tools

        response = await client.messages.create(**kwargs)

        content = ""
        tool_calls = []
        for block in response.content:
            if block.type == "text":
                content += block.text
            elif block.type == "tool_use":
                tool_calls.append({
                    "id": block.id,
                    "name": block.name,
                    "input": block.input,
                })

        return {
            "content": content,
            "tool_calls": tool_calls,
            "stop_reason": response.stop_reason,
        }

    async def _openai_complete(self, system, messages, max_tokens, tools, model):
        model = model or "gpt-4o-mini"
        headers = {
            "Authorization": f"Bearer {self.config.openai_api_key}",
            "Content-Type": "application/json",
        }
        oai_messages = [{"role": "system", "content": system}] + messages

        payload = {
            "model": model,
            "messages": oai_messages,
            "max_tokens": max_tokens,
        }
        if tools:
            # Convert Anthropic tool format to OpenAI
            payload["tools"] = [
                {
                    "type": "function",
                    "function": {
                        "name": t["name"],
                        "description": t.get("description", ""),
                        "parameters": t.get("input_schema", {}),
                    },
                }
                for t in tools
            ]

        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(
                "https://api.openai.com/v1/chat/completions",
                headers=headers,
                json=payload,
            )
            resp.raise_for_status()
            data = resp.json()

        choice = data["choices"][0]
        msg = choice["message"]
        tool_calls = []
        if msg.get("tool_calls"):
            for tc in msg["tool_calls"]:
                tool_calls.append({
                    "id": tc["id"],
                    "name": tc["function"]["name"],
                    "input": json.loads(tc["function"]["arguments"]),
                })

        return {
            "content": msg.get("content", ""),
            "tool_calls": tool_calls,
            "stop_reason": choice.get("finish_reason", ""),
        }

    async def _ollama_complete(self, system, messages, max_tokens, model):
        model = model or "llama3.2"
        ollama_messages = [{"role": "system", "content": system}] + messages

        async with httpx.AsyncClient(timeout=120) as client:
            resp = await client.post(
                f"{self.config.ollama_url}/api/chat",
                json={
                    "model": model,
                    "messages": ollama_messages,
                    "stream": False,
                    "options": {"num_predict": max_tokens},
                },
            )
            resp.raise_for_status()
            data = resp.json()

        return {
            "content": data.get("message", {}).get("content", ""),
            "tool_calls": [],
            "stop_reason": "end_turn",
        }
