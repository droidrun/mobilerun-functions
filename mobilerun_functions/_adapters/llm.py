"""OpenAI-compatible client settings for the platform LLM gateway."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from ..errors import NotAvailableError

if TYPE_CHECKING:
    import openai


@dataclass(frozen=True)
class LLMAdapter:
    base_url: str
    api_key: str = field(repr=False)

    def openai(self) -> openai.AsyncOpenAI:
        try:
            from openai import AsyncOpenAI
        except ImportError:
            raise NotAvailableError("llm", "install mobilerun-functions[llm]") from None
        return AsyncOpenAI(base_url=self.base_url, api_key=self.api_key)
