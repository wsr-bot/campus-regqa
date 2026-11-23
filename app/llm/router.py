"""档位路由与降级链。调用方只说「我要 strong 还是 fast」，不关心背后是谁。"""

from __future__ import annotations

import logging
from typing import Any

from app.config import get_settings
from app.llm.providers import ChatResult, LLMUnavailable, chat, is_configured

log = logging.getLogger(__name__)


def _chain(tier: str) -> list[dict[str, Any]]:
    return get_settings().get(f"llm.tiers.{tier}.chain")


def complete(
    tier: str,
    messages: list[dict[str, Any]],
    tools: list[dict] | None = None,
    temperature: float = 0.0,
    max_tokens: int | None = None,
) -> ChatResult:
    """按配置顺序尝试，第一家成功即返回；全部失败抛 LLMUnavailable。"""
    cfg = get_settings()
    limit = max_tokens or int(cfg.get("budget.max_output_tokens", 800))

    errors: list[str] = []
    for item in _chain(tier):
        provider = item["provider"]
        model = item["model"]

        if not is_configured(provider):
            errors.append(f"{provider}: 未配置密钥，跳过")
            continue

        try:
            result = chat(
                provider=provider,
                model=model,
                messages=messages,
                tools=tools,
                temperature=temperature,
                max_tokens=limit,
                timeout_s=float(item.get("timeout_s", 60)),
            )
            log.info("llm ok tier=%s provider=%s model=%s ms=%s", tier, provider, model, result.latency_ms)
            return result
        except LLMUnavailable as exc:
            errors.append(f"{provider}/{model}: {exc}")
            log.warning("llm down tier=%s provider=%s err=%s", tier, provider, exc)
            continue

    raise LLMUnavailable("所有模型均不可用：" + " | ".join(errors))


def judge(messages: list[dict[str, Any]], max_tokens: int = 600) -> ChatResult:
    """离线裁判，走 judge 配置（必须与生成不同供应商）。"""
    cfg = get_settings()
    provider = cfg.get("llm.judge.provider")
    model = cfg.get("llm.judge.model")
    temperature = float(cfg.get("llm.judge.temperature", 0.0))
    if not is_configured(provider):
        raise LLMUnavailable(f"judge 供应商 {provider} 未配置密钥")
    return chat(
        provider=provider,
        model=model,
        messages=messages,
        tools=None,
        temperature=temperature,
        max_tokens=max_tokens,
    )
