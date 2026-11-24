"""Capture connection choices before importing user code."""

from __future__ import annotations

import os
from dataclasses import dataclass, field

from .device import API_BASE_URL_ENV, API_KEY_ENV, _cloud_connection
from .errors import NotAvailableError

PLATFORM_ENV = "MOBILERUN_FUNCTIONS_ENV"
CLOUD_BASE_URL = "https://api.mobilerun.ai/v1"


@dataclass(frozen=True)
class Connection:
    cloud: bool
    base_url: str | None
    api_key: str | None = field(repr=False)

    @classmethod
    def capture(cls, force_cloud: bool) -> Connection:
        cloud = force_cloud or os.environ.get(PLATFORM_ENV) == "cloud"
        base_url, api_key = _cloud_connection()
        use_default_base = force_cloud and not base_url
        if use_default_base:
            base_url = CLOUD_BASE_URL
        return cls(cloud=cloud, base_url=base_url, api_key=api_key)

    def require_cloud(self, member: str) -> tuple[str, str]:
        if not self.api_key:
            raise NotAvailableError(member, f"set {API_KEY_ENV}")
        base_url = self.base_url
        local_resource = not self.cloud and not base_url
        if local_resource:
            base_url = CLOUD_BASE_URL
        if not base_url:
            raise NotAvailableError(member, f"set {API_BASE_URL_ENV}")
        return base_url, self.api_key
