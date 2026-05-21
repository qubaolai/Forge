"""可观测性配置: 链路追踪."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel


class TracingConfig(BaseModel):
    model_config = {"extra": "forbid"}

    enabled: bool = False
    exporter: Literal["none", "otel", "langfuse"] = "none"
    service_name: str = "forge"
    otel_endpoint: str = ""
    langfuse_public_key: str = ""
    langfuse_secret_key: str = ""
    langfuse_host: str = "https://cloud.langfuse.com"


class ObservabilityConfig(BaseModel):
    model_config = {"extra": "forbid"}

    tracing: TracingConfig = TracingConfig()
