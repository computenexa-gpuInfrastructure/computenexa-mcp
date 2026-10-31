"""What each tool returns as structured data (its `outputSchema`). Money is in US dollars; times are ISO 8601 UTC."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class KeyInfo(BaseModel):
    name: str | None = Field(description="The API key's name in the dashboard")
    prefix: str | None = Field(description="Its first characters, e.g. cnxk_a1b2c3d4 (never the whole key)")
    scope: Literal["read", "write"] | None = Field(description="read = look only; write = also launch and stop")


class Account(BaseModel):
    email: str
    team: str | None
    role: str | None = Field(description="Your role in the team: owner, admin or member")
    team_members: int | None
    balance_usd: float = Field(description="The team's prepaid balance")
    gpu_limit: int = Field(description="GPUs the team may run at the same time")
    api_key: KeyInfo | None = Field(description="The key this server uses; null if the platform doesn't report it")
    can_launch: bool | None = Field(description="Whether this server's key may launch, extend and stop machines")


class GpuInfo(BaseModel):
    slug: str = Field(description="What to launch with, e.g. rtx-4090")
    name: str
    memory_gb: int
    available: bool
    price_per_hour_usd: float | None = Field(description="Per GPU; null when not for sale right now")
    billing: Literal["minute", "hour"] = Field(description="minute = billed per minute; hour = per started hour")
    apps: list[str]
    regions: list[str]


class GpuList(BaseModel):
    region: str
    gpus: list[GpuInfo]


class ModelInfo(BaseModel):
    name: str
    label: str | None = None
    size_gb: float | None = None
    min_memory_gb: int | None = None
    licence: str | None = None
    gated: bool | None = None


class AppInfo(BaseModel):
    name: str = Field(description="The value for launch_machine's app")
    display_name: str
    summary: str
    needs_ssh_key: bool
    models: list[ModelInfo]
    model_required: bool
    recommended: list[tuple[int, str]] = Field(description="[min GPU memory in GB, model]; the first match wins")


class Software(BaseModel):
    image: str
    cuda: str | None = None
    ubuntu: str | None = None
    python: str | None = None
    headline: list[dict[str, Any]] = Field(default_factory=list, description="Main packages: [{name, version}]")
    tools: list[dict[str, Any]] = Field(default_factory=list)
    package_count: int = 0
    matching_packages: list[dict[str, Any]] = Field(default_factory=list,
                                                    description="Packages matching the `package` filter")


class AppList(BaseModel):
    apps: list[AppInfo]
    software: list[Software] = Field(default_factory=list, description="What's installed (when one app was asked for)")


class Machine(BaseModel):
    id: str = Field(description="e.g. cnx-7f3a9c")
    state: str = Field(description="requested | provisioning | running | stopping | terminated | failed")
    gpu: str
    gpu_count: int
    app: str
    label: str | None
    price_per_hour_usd: float = Field(description="For the whole machine (all its GPUs)")
    billing: str | None = None
    expires_at: str | None
    time_left_minutes: int | None
    charged_usd: float
    refunded_usd: float
    cost_so_far_usd: float = Field(description="What it costs if it ended now")
    keep_running_hours: int | None = Field(description="Renews this many hours at a time; null = off")
    next_renewal_at: str | None = None
    renew_failed: bool = False
    end_reason: str | None = None
    startup_progress: str | None = None
    launched_by: str | None = None


class MachineDetail(Machine):
    ssh_command: str | None = Field(description="While running, if the machine has an SSH key")
    browser_apps: list[str] = Field(default_factory=list,
                                    description="Apps to open from the dashboard's Open button while running")
    api_base_url: str | None = Field(default=None, description="Model API / Voice Agent machines: their API address")
    endpoint_url: str | None = Field(default=None, description="Your own server on the machine's port 8000")
    endpoint_name: str | None = None
    model: str | None = None
    project: str | None = None
    region: str | None = None


class MachineList(BaseModel):
    machines: list[Machine]


class Usage(BaseModel):
    days: int
    balance_usd: float
    spend_usd: float
    topups_usd: float
    sessions: int
    gpu_hours: float
    by_gpu: list[dict[str, Any]]
    by_app: list[dict[str, Any]]
    daily: list[dict[str, Any]]
    projects: list[dict[str, Any]]


class Receipt(BaseModel):
    machine: str
    label: str | None
    gpu: str
    gpu_count: int
    app: str
    model: str | None
    started_at: str | None
    ended_at: str | None
    used_minutes: float
    booked_minutes: int
    charged_usd: float
    refunded_usd: float
    net_usd: float = Field(description="What it cost in the end")
    project: str | None
    outcome: str


class ReceiptList(BaseModel):
    receipts: list[Receipt]
    total_net_usd: float


class Project(BaseModel):
    id: str
    name: str
    size_mb: float
    files: int
    in_use_by: str | None
    last_used_at: str | None


class ProjectList(BaseModel):
    projects: list[Project]


class Endpoint(BaseModel):
    id: str
    name: str
    url: str
    machine: str | None = Field(description="The machine it points at now, if any")
    key: str | None = Field(default=None, description="Only when just created, or from get_endpoint_key")


class EndpointList(BaseModel):
    endpoints: list[Endpoint]


class Quote(BaseModel):
    description: str
    price_per_hour_usd: float
    minutes: int
    total_usd: float = Field(description="Charged now; unused time is refunded if it stops early")
    renewal_usd: float | None = Field(default=None, description="Keep running: charged at each renewal")
    renewal_hours: int | None = None
    limit_usd: float = Field(description="This server's limit per call (CNX_MCP_MAX_LAUNCH_USD)")


class SpendResult(BaseModel):
    status: Literal["quote", "done"] = Field(description="quote = nothing happened yet; done = it was carried out")
    quote: Quote | None = None
    machine: MachineDetail | None = None


class StopResult(BaseModel):
    machine: MachineDetail
    refunded_usd: float


class EndpointKey(BaseModel):
    endpoint: str
    url: str
    key: str
