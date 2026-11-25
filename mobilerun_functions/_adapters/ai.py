"""OpenAI-compatible client settings for the platform AI gateway."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from ..errors import NotAvailableError

if TYPE_CHECKING:
    import openai

# The gateway's OpenAI-compatible surface sits under the API base URL (`/v1/ai`).
AI_PATH = "/ai"


def ai_base_url(api_base_url: str) -> str:
    return api_base_url.rstrip("/") + AI_PATH


@dataclass(frozen=True)
class AIAdapter:
    base_url: str
    api_key: str = field(repr=False)

    def openai(self) -> openai.AsyncOpenAI:
        try:
            from openai import AsyncOpenAI
        except ImportError:
            raise NotAvailableError("ai", "install mobilerun-functions[ai]") from None
        return AsyncOpenAI(base_url=self.base_url, api_key=self.api_key)
