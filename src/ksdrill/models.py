# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Ghalbouni Consulting SASU
"""Pydantic models for every on-disk format the drill reads or writes."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class Ports(BaseModel):
    model_config = ConfigDict(extra="forbid")
    agent: int = 8701
    admin: int = 8702


class Budgets(BaseModel):
    model_config = ConfigDict(extra="forbid")
    S0: int = 50
    S1: int = 1000
    S2: int = 100
    S3: int = 200
    S4: int = 200
    S5: int = 200
    S6: int = 300
    total: int = 2000


class VelocityConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool = True
    max_payments_in_window: int = 8
    window_s: float = 10


class OllamaConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    url: str = "http://127.0.0.1:11434"
    model: str = "llama3.2"
    timeout_s: float = 20


class DrillConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    bind_host: str = "127.0.0.1"
    ports: Ports = Field(default_factory=Ports)
    budgets_ms: Budgets = Field(default_factory=Budgets)
    token_ttl_s: int = 300
    stop_at_step: int = 3
    retention_days: int = 183
    dispatcher_interval_ms: int = 250
    email_dispatch_delay_ms: int = 8000
    step_delay_ms: int = 600
    stop_window_ms: int = 5000
    grace_period_ms: int = 500
    agent_id: str = "ap-agent-01"
    probe_token_layer: bool = False
    velocity: VelocityConfig = Field(default_factory=VelocityConfig)
    ollama: OllamaConfig = Field(default_factory=OllamaConfig)


class ToolSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    actions: list[str]
    ttl_s: int = 300
    outbound: bool = False
    channel: str | None = None
    anssi_category: Literal["autonomous", "human_approval", "prohibited"]


class ToolsFile(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tools: list[ToolSpec]


class PaymentCap(BaseModel):
    model_config = ConfigDict(extra="forbid")
    max_amount_eur: float = 5000
    currency: str = "EUR"


class PolicyCaps(BaseModel):
    model_config = ConfigDict(extra="forbid")
    payments: PaymentCap = Field(default_factory=PaymentCap)


class PolicyFile(BaseModel):
    model_config = ConfigDict(extra="forbid")
    agent_id: str
    deny_by_default: bool = True
    allow: dict[str, list[str]]
    caps: PolicyCaps = Field(default_factory=PolicyCaps)
    mandate_ref: str = "scenario:supplier_payments"


class Owners(BaseModel):
    model_config = ConfigDict(extra="forbid")
    accountable: str
    business: str
    technical: str
    control: str
    stop_owner: str


class DeploymentRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")
    deployment_id: str
    purpose: str
    status: str
    owners: Owners
    authorised_users: list[str]
    stop_mechanism: str
    fallback: str
    log_locations: list[str]
    credentials: str


class Clause(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    requirement: str
    source: str
    url: str
    ref: str
    page: int | str | None = None
    quote: str
    quote_kind: Literal["verbatim", "finding"] = "verbatim"
    verified: bool
    verified_on: str
    note: str | None = None
    demo_step: str | None = None
    card_field: str | None = None


class ClauseMapMeta(BaseModel):
    model_config = ConfigDict(extra="allow")
    verified_on: str
    tally: int
    verified: int
    unverified: int
    note: str | None = None


class ClauseMapFile(BaseModel):
    model_config = ConfigDict(extra="forbid")
    meta: ClauseMapMeta
    clauses: list[Clause]


class ScenarioStep(BaseModel):
    model_config = ConfigDict(extra="forbid")
    index: int
    tool: str
    action: str
    instruction_ref: str
    args: dict[str, Any]


class Scenario(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    kind: str
    title: str
    steps: list[ScenarioStep]


class StepResult(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")
    id: str
    name: str
    duration_ms: float
    pass_: bool = Field(alias="pass")
    predicates: dict[str, Any]
    clauses: list[str]
    budget_ms: int | None = None


class BypassResultModel(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")
    id: str
    name: str
    expected: str
    observed: str
    pass_: bool = Field(alias="pass")
    log_seq: int | None = None
    clauses: list[str] = Field(default_factory=list)


class StopReportModel(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")
    run_id: str
    operator: str
    reason: str
    trigger: Literal["operator", "supervisor"]
    already_safe: bool = False
    steps: list[StepResult]
    time_to_safe_state_ms: float
    time_to_latch_ms: float
    budget_ms: int
    pass_: bool = Field(alias="pass")
    detection_time_ms: float | None = None
    llm_backend_down: bool | None = None
    within_budget: bool = True
