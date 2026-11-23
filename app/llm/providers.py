"""统一的大模型调用层：对外只有 chat()，内部兼容 OpenAI 协议与 Anthropic 原生协议。

内部消息格式（agent 节点只认这个，不认厂商格式）：
    {"role": "system"|"user"|"assistant"|"tool", "content": str,
     "tool_calls": [{"id","name","arguments"}]   # 仅 assistant
     "tool_call_id": str, "name": str}           # 仅 tool
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from typing import Any

OPENAI_COMPAT_BASE = {
    "deepseek": "https://api.deepseek.com/v1",
    "qwen": "https://dashscope.aliyuncs.com/compatible-mode/v1",
}
KEY_ENV = {
    "anthropic": "ANTHROPIC_API_KEY",
    "deepseek": "DEEPSEEK_API_KEY",
    "qwen": "DASHSCOPE_API_KEY",
}
ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any] = field(default_factory=dict)


@dataclass
class ChatResult:
    text: str
    tool_calls: list[ToolCall]
    provider: str
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    latency_ms: int = 0


class LLMUnavailable(RuntimeError):
    """这一档的这家供应商不可用，调用方应降级到下一家。"""


def is_configured(provider: str) -> bool:
    return bool(os.getenv(KEY_ENV.get(provider, "")))


def _to_openai_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for m in messages:
        role = m["role"]
        if role == "tool":
            out.append(
                {"role": "tool", "tool_call_id": m.get("tool_call_id", ""), "content": m.get("content", "")}
            )
        elif role == "assistant" and m.get("tool_calls"):
            out.append(
                {
                    "role": "assistant",
                    "content": m.get("content") or None,
                    "tool_calls": [
                        {
                            "id": tc["id"],
                            "type": "function",
                            "function": {
                                "name": tc["name"],
                                "arguments": json.dumps(tc.get("arguments", {}), ensure_ascii=False),
                            },
                        }
                        for tc in m["tool_calls"]
                    ],
                }
            )
        else:
            out.append({"role": role, "content": m.get("content", "")})
    return out


def _to_anthropic(messages: list[dict[str, Any]], tools: list[dict] | None):
    system_parts: list[str] = []
    converted: list[dict[str, Any]] = []
    for m in messages:
        role = m["role"]
        if role == "system":
            system_parts.append(m.get("content", ""))
        elif role == "tool":
            converted.append(
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": m.get("tool_call_id", ""),
                            "content": m.get("content", ""),
                        }
                    ],
                }
            )
        elif role == "assistant" and m.get("tool_calls"):
            blocks: list[dict[str, Any]] = []
            if m.get("content"):
                blocks.append({"type": "text", "text": m["content"]})
            for tc in m["tool_calls"]:
                blocks.append(
                    {"type": "tool_use", "id": tc["id"], "name": tc["name"], "input": tc.get("arguments", {})}
                )
            converted.append({"role": "assistant", "content": blocks})
        else:
            converted.append({"role": role, "content": [{"type": "text", "text": m.get("content", "")}]})

    a_tools = None
    if tools:
        a_tools = [
            {
                "name": t["function"]["name"],
                "description": t["function"].get("description", ""),
                "input_schema": t["function"].get("parameters", {"type": "object", "properties": {}}),
            }
            for t in tools
        ]
    return system_parts, converted, a_tools


def chat(
    *,
    provider: str,
    model: str,
    messages: list[dict[str, Any]],
    tools: list[dict] | None = None,
    temperature: float = 0.0,
    max_tokens: int = 800,
    timeout_s: float = 60,
) -> ChatResult:
    started = time.time()
    if not is_configured(provider):
        raise LLMUnavailable(f"{provider} 未配置 {KEY_ENV.get(provider, 'API KEY')}")

    if provider == "anthropic":
        res = _chat_anthropic(model, messages, tools, temperature, max_tokens, timeout_s)
    elif provider in OPENAI_COMPAT_BASE:
        res = _chat_openai(provider, model, messages, tools, temperature, max_tokens, timeout_s)
    else:
        raise LLMUnavailable(f"未知供应商：{provider}")

    res.latency_ms = int((time.time() - started) * 1000)
    return res


def _chat_openai(provider, model, messages, tools, temperature, max_tokens, timeout_s) -> ChatResult:
    from openai import OpenAI

    client = OpenAI(
        api_key=os.environ[KEY_ENV[provider]],
        base_url=OPENAI_COMPAT_BASE[provider],
        timeout=timeout_s,
    )
    kwargs: dict[str, Any] = {
        "model": model,
        "messages": _to_openai_messages(messages),
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    if tools:
        kwargs["tools"] = tools

    try:
        resp = client.chat.completions.create(**kwargs)
    except Exception as exc:  # 网络/限流/额度统一按不可用处理，交给降级链
        raise LLMUnavailable(f"{provider} 调用失败：{type(exc).__name__}: {exc}") from exc

    choice = resp.choices[0]
    calls: list[ToolCall] = []
    for tc in (choice.message.tool_calls or []):
        raw = tc.function.arguments or "{}"
        try:
            args = json.loads(raw)
        except json.JSONDecodeError:
            args = {}
        calls.append(ToolCall(id=tc.id, name=tc.function.name, arguments=args))

    usage = resp.usage
    return ChatResult(
        text=choice.message.content or "",
        tool_calls=calls,
        provider=provider,
        model=model,
        input_tokens=getattr(usage, "prompt_tokens", 0) or 0,
        output_tokens=getattr(usage, "completion_tokens", 0) or 0,
    )


def _chat_anthropic(model, messages, tools, temperature, max_tokens, timeout_s) -> ChatResult:
    import httpx

    system_parts, converted, a_tools = _to_anthropic(messages, tools)
    body: dict[str, Any] = {
        "model": model,
        "max_tokens": max_tokens,
        "temperature": temperature,
        "messages": converted,
    }
    if system_parts:
        body["system"] = "\n\n".join(p for p in system_parts if p)
    if a_tools:
        body["tools"] = a_tools

    try:
        with httpx.Client(timeout=timeout_s) as client:
            resp = client.post(
                ANTHROPIC_URL,
                headers={
                    "x-api-key": os.environ[KEY_ENV["anthropic"]],
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
                json=body,
            )
    except Exception as exc:
        raise LLMUnavailable(f"anthropic 网络失败：{type(exc).__name__}: {exc}") from exc

    if resp.status_code >= 400:
        raise LLMUnavailable(f"anthropic 返回 {resp.status_code}: {resp.text[:200]}")

    data = resp.json()
    text_parts: list[str] = []
    calls: list[ToolCall] = []
    for block in data.get("content", []):
        if block.get("type") == "text":
            text_parts.append(block.get("text", ""))
        elif block.get("type") == "tool_use":
            calls.append(ToolCall(id=block.get("id", ""), name=block.get("name", ""), arguments=block.get("input", {})))

    usage = data.get("usage", {})
    return ChatResult(
        text="".join(text_parts),
        tool_calls=calls,
        provider="anthropic",
        model=model,
        input_tokens=usage.get("input_tokens", 0),
        output_tokens=usage.get("output_tokens", 0),
    )
