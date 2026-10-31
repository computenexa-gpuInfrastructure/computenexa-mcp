"""The ComputeNexa MCP server: an AI assistant (Claude, Cursor, VS Code…) finds, launches and stops GPUs with the
customer's own API key. It runs on their computer over stdio and calls the public API through the `computenexa` SDK.

Safety (docs/design/mcp-server.md in the platform repo):
- A read-only key gets the read tools only. A "launch and stop" key also gets the tools that spend money or change
  things; those are annotated so assistants ask the person first.
- Anything that spends money takes two calls: the first (confirm=false) returns a quote and does nothing; the second
  (confirm=true, with the quoted total) does it. A total above CNX_MCP_MAX_LAUNCH_USD (default $25) is refused.
- No tool returns the API key this server uses. Endpoint keys appear only from create_endpoint (once) and
  get_endpoint_key. Text people typed (labels, names) is cleaned before an assistant reads it.
"""

import functools
import os
import sys
from collections.abc import Callable
from typing import Annotated, Any, Literal, TypeVar

import computenexa
from computenexa import (
    APIConnectionError,
    AuthenticationError,
    Client,
    ComputeNexaError,
    GpuLimitReachedError,
    InsufficientCreditsError,
    InvalidRequestError,
    NoCapacityError,
    NotFoundError,
    PermissionDeniedError,
    ProjectInUseError,
    RateLimitError,
    ServerError,
)
from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import CallToolResult, TextContent, ToolAnnotations
from pydantic import BaseModel, Field

from . import models as m
from ._text import charge_cents, clean, dollars, duration, money, quoted, usd, when, whole_hours
from ._version import __version__

DEFAULT_MAX_LAUNCH_USD = 25.0
LIMIT_ENV = "CNX_MCP_MAX_LAUNCH_USD"

READ_TOOLS = ("whoami", "list_gpus", "list_apps", "list_machines", "get_machine", "get_usage", "list_receipts",
              "list_projects", "list_endpoints")
WRITE_TOOLS = ("launch_machine", "extend_machine", "set_keep_running", "stop_machine", "create_endpoint",
               "get_endpoint_key")

READ = ToolAnnotations(read_only_hint=True, destructive_hint=False, idempotent_hint=True, open_world_hint=True)
SPENDS = ToolAnnotations(read_only_hint=False, destructive_hint=False, idempotent_hint=False, open_world_hint=True)
CHANGES = ToolAnnotations(read_only_hint=False, destructive_hint=False, idempotent_hint=True, open_world_hint=True)
ENDS = ToolAnnotations(read_only_hint=False, destructive_hint=True, idempotent_hint=True, open_world_hint=True)
SECRET = ToolAnnotations(read_only_hint=True, destructive_hint=False, idempotent_hint=True, open_world_hint=True)

INSTRUCTIONS = """\
ComputeNexa rents private GPUs by the minute from the team's prepaid balance. Prices are in US dollars.
- Use list_gpus to find GPUs (live prices and stock), list_apps for what each app runs, list_machines and
  get_machine for machines and how to connect.
- Spending money takes two calls. launch_machine, extend_machine and set_keep_running first return a quote and do
  nothing. Show the quote to the person; only after they agree, call again with the same arguments, confirm=true
  and the quoted total. Never confirm on your own.
- stop_machine ends a machine at once: files outside a project are deleted, unused time is refunded.
- Never show an endpoint key unless the person asks for it."""

F = TypeVar("F", bound=Callable[..., Any])

_NEXT_STEP: list[tuple[type[ComputeNexaError], str]] = [
    (AuthenticationError, ("Check CNX_API_KEY in this server's settings, or create a new key in the ComputeNexa "
                           "dashboard under API keys.")),
    (PermissionDeniedError, ("This API key may only look, or this needs the dashboard. To launch and stop, create "
                             "a “Launch and stop” key under API keys and use it here.")),
    (InsufficientCreditsError, "Add credits in the ComputeNexa dashboard, or choose fewer hours."),
    (NoCapacityError, "Try another GPU or region: list_gpus shows what's in stock."),
    (GpuLimitReachedError, "Stop a machine first (list_machines), or ask a team owner to raise the GPU limit."),
    (ProjectInUseError, "Another machine is using that project: stop it first, or use another project."),
    (NotFoundError, "Check the id or name: list_machines, list_projects and list_endpoints show them."),
    (InvalidRequestError, "Fix the value the message names and try again."),
    (RateLimitError, "Too many requests: wait a minute and try again."),
    (APIConnectionError, "Couldn't reach ComputeNexa: check the internet connection and try again."),
    (ServerError, ("Something went wrong at ComputeNexa: try again in a minute. If it keeps happening, email "
                   "support@computenexa.com.")),
]


def explain(e: ComputeNexaError) -> str:
    """An API refusal as a tool error: its message, what to do next, and the request id for support."""
    step = next((s for cls, s in _NEXT_STEP if isinstance(e, cls)), "")
    text = (clean(e.message, 300) or "The request was refused.").rstrip(".") + "."
    if step:
        text += " " + step
    if e.request_id:
        text += f" (request id {clean(e.request_id, 64)})"
    return text


def result(text: str, data: BaseModel) -> CallToolResult:
    """Readable text for the assistant and the same facts as structured data."""
    return CallToolResult(content=[TextContent(type="text", text=text)],
                          structured_content=data.model_dump(mode="json"))


def max_launch_usd(environ: dict[str, str] | None = None) -> float:
    """The per-call spending limit from CNX_MCP_MAX_LAUNCH_USD (dollars), default $25."""
    raw = (environ if environ is not None else os.environ).get(LIMIT_ENV, "").strip()
    if not raw:
        return DEFAULT_MAX_LAUNCH_USD
    try:
        value = float(raw.removeprefix("$"))
    except ValueError:
        raise ValueError(f"{LIMIT_ENV} must be a number of US dollars, e.g. 25 (got {raw!r})") from None
    if not 0 < value <= 100_000:
        raise ValueError(f"{LIMIT_ENV} must be more than 0 and at most 100000 (got {raw!r})")
    return value


# ---------------------------------------------------------------- converting API objects


def _machine(i: computenexa.Instance) -> m.MachineDetail:
    raw = i.raw
    project = raw.get("project")
    return m.MachineDetail(
        id=i.id, state=i.state, gpu=i.gpu, gpu_count=i.gpu_count, app=i.app, label=clean(i.label),
        price_per_hour_usd=usd(i.price_per_hour_cents) or 0.0, billing=raw.get("billing_unit"),
        expires_at=when(i.expires_at),
        time_left_minutes=None if i.time_left_seconds is None else i.time_left_seconds // 60,
        charged_usd=usd(i.charged_cents) or 0.0, refunded_usd=usd(i.refunded_cents) or 0.0,
        cost_so_far_usd=usd(i.cost_so_far_cents) or 0.0, keep_running_hours=i.keep_running_hours,
        next_renewal_at=when(i.next_renewal_at), renew_failed=i.renew_failed, end_reason=i.end_reason,
        startup_progress=clean(i.startup_progress, 200), launched_by=clean(raw.get("launched_by"), 200),
        ssh_command=i.ssh_command,
        browser_apps=[clean(a.get("label") or a.get("app"), 40) or "" for a in raw.get("apps") or []],
        api_base_url=i.api_base_url, endpoint_url=i.endpoint_url, endpoint_name=clean(i.endpoint_name),
        model=clean(raw.get("model"), 200), project=clean(project.get("name")) if isinstance(project, dict) else None,
        region=raw.get("region"),
    )


def _summary(i: computenexa.Instance) -> m.Machine:
    d = _machine(i)
    return m.Machine(**{k: getattr(d, k) for k in m.Machine.model_fields})


def _machine_line(d: m.Machine) -> str:
    name = f" {quoted(d.label)}" if d.label else ""
    parts = [f"{d.id}{name}: {d.state}", f"{d.gpu_count} × {d.gpu}" if d.gpu_count > 1 else d.gpu, d.app,
             f"${d.price_per_hour_usd:.2f}/h"]
    if d.state == "running" and d.time_left_minutes is not None:
        parts.append(f"{duration(d.time_left_minutes)} left")
    if d.keep_running_hours:
        parts.append(f"keep running every {d.keep_running_hours} h" + (" (renewal FAILED: top up)"
                                                                         if d.renew_failed else ""))
    if d.startup_progress and d.state in ("requested", "provisioning"):
        parts.append(d.startup_progress)
    if d.end_reason:
        parts.append(d.end_reason)
    return " · ".join(parts)


def _machine_text(d: m.MachineDetail) -> str:
    lines = [_machine_line(d)]
    if d.ssh_command:
        lines.append(f"SSH: {d.ssh_command}")
    if d.browser_apps and d.state == "running":
        lines.append("In the browser (Open button in the dashboard): " + ", ".join(d.browser_apps))
    if d.api_base_url:
        lines.append(f"API: {d.api_base_url}")
    if d.endpoint_url:
        lines.append(f"Your server on port 8000: {d.endpoint_url}"
                     + (f" (Endpoint {quoted(d.endpoint_name)})" if d.endpoint_name else ""))
    if d.project:
        lines.append(f"Project {quoted(d.project)} is restored into ~/work and saved back automatically.")
    lines.append(f"Charged ${d.charged_usd:.2f}, refunded ${d.refunded_usd:.2f}, cost so far ${d.cost_so_far_usd:.2f}.")
    return "\n".join(lines)


# ---------------------------------------------------------------- the server


def build_server(client: Client | None, *, scope: str | None = None, limit_usd: float | None = DEFAULT_MAX_LAUNCH_USD,
                 setup_error: str | None = None) -> MCPServer:
    """The MCP server for one API key.

    `scope`: the key's scope as GET /v1/me reports it ("read" leaves out the tools that spend or change; None =
    unknown, so every tool is offered and the API refuses what the key may not do). `limit_usd`: the most one call may
    spend; None = the limit setting was invalid, so spending tools refuse with `setup_error`. `client=None`: no
    usable API key; every tool explains `setup_error`."""
    server = MCPServer("ComputeNexa", title="ComputeNexa", version=__version__, instructions=INSTRUCTIONS,
                       website_url="https://computenexa.com")

    def guard(fn: F) -> F:
        @functools.wraps(fn)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            if client is None:
                raise ToolError(setup_error or "No API key: set CNX_API_KEY in this server's settings.")
            try:
                return fn(*args, **kwargs)
            except ComputeNexaError as e:
                raise ToolError(explain(e)) from None
            except ValueError as e:  # the SDK's own argument checks
                raise ToolError(str(e)) from None
        return wrapper  # type: ignore[return-value]

    def c() -> Client:
        assert client is not None
        return client

    def check_limit(total_cents: int, what: str) -> float:
        if limit_usd is None:
            raise ToolError(setup_error or f"{LIMIT_ENV} is invalid: fix it in this server's settings.")
        if total_cents > round(limit_usd * 100):
            raise ToolError(f"{what} costs {money(total_cents)}, above this server's limit of ${limit_usd:,.2f} per "
                            f"call ({LIMIT_ENV}). Choose fewer hours, or raise {LIMIT_ENV} in the MCP settings.")
        return limit_usd

    def check_approved(total_cents: int, expected_usd: float | None, field: str) -> None:
        if expected_usd is None:
            raise ToolError(f"confirm=true needs {field}: the total from the quote the person agreed to. Call with "
                            "confirm=false first to get the quote.")
        if total_cents > round(expected_usd * 100):
            raise ToolError(f"The price changed: it's now {money(total_cents)}, more than the ${expected_usd:,.2f} "
                            "the person agreed to. Nothing was charged. Show them the new quote (confirm=false) "
                            "before trying again.")

    # ------------------------------------------------------------ read tools

    @server.tool(annotations=READ, title="Who am I")
    @guard
    def whoami() -> Annotated[CallToolResult, m.Account]:
        """Who this server's API key acts as: email, team and role, the team's balance and GPU limit, and whether
        the key may launch and stop machines."""
        a = c().account()
        key = a.raw.get("api_key")
        info = m.KeyInfo(name=clean(key.get("name")), prefix=key.get("prefix"), scope=key.get("scope")) \
            if isinstance(key, dict) else None
        out = m.Account(email=a.email, team=clean(a.organization.name), role=a.organization.role or None,
                        team_members=a.organization.members, balance_usd=usd(a.balance_cents) or 0.0,
                        gpu_limit=a.max_concurrent_gpus, api_key=info,
                        can_launch=None if info is None else info.scope == "write")
        text = (f"{out.email} in team {quoted(out.team)} ({out.role}). Balance {money(a.balance_cents)}. "
                f"Up to {out.gpu_limit} GPU(s) at a time.")
        if info is not None:
            text += f" API key {quoted(info.name)} ({info.prefix}…): " + \
                ("launch and stop." if info.scope == "write" else "look only.")
        return result(text, out)

    @server.tool(annotations=READ, title="List GPUs")
    @guard
    def list_gpus(
        app: Annotated[str | None, Field(description="Only GPUs that run this app (see list_apps), e.g. chat")] = None,
        region: Annotated[Literal["any", "us", "ca"], Field(description="any = anywhere; us; ca = Canada")] = "any",
        min_memory_gb: Annotated[int | None, Field(ge=1, le=2000, description="At least this much GPU memory")] = None,
        include_sold_out: Annotated[bool, Field(description="Also list GPUs not for sale right now")] = False,
    ) -> Annotated[CallToolResult, m.GpuList]:
        """GPUs with live prices (US dollars per GPU per hour) and stock, cheapest first."""
        cat = c().catalog(None if region == "any" else region)
        gpus = [g for g in cat.gpus
                if (include_sold_out or (g.available and g.price_per_hour_cents is not None))
                and (app is None or app in g.apps) and (min_memory_gb is None or g.vram_gb >= min_memory_gb)]
        gpus.sort(key=lambda g: (g.price_per_hour_cents is None, g.price_per_hour_cents or 0, g.slug))
        out = m.GpuList(region=region, gpus=[
            m.GpuInfo(slug=g.slug, name=g.name, memory_gb=g.vram_gb, available=g.available,
                      price_per_hour_usd=usd(g.price_per_hour_cents),
                      billing="hour" if g.billing_unit == "hour" else "minute", apps=g.apps, regions=g.regions)
            for g in gpus])
        if not out.gpus:
            return result("No GPUs match right now. Try without filters, another region, or include_sold_out=true.",
                          out)
        lines = [f"{g.slug} · {g.name} · {g.memory_gb} GB · "
                 + (f"${g.price_per_hour_usd:.2f}/h" if g.price_per_hour_usd is not None else "not for sale now")
                 + (" (billed per started hour)" if g.billing == "hour" else "")
                 + f" · apps: {', '.join(g.apps) or '—'}"
                 + (f" · regions: {', '.join(g.regions)}" if g.regions else "")
                 for g in out.gpus]
        return result("\n".join(lines), out)

    @server.tool(annotations=READ, title="List apps")
    @guard
    def list_apps(
        app: Annotated[str | None, Field(description="One app, to also see what's installed in it")] = None,
        package: Annotated[str | None, Field(max_length=60, description="With app: find this package's version, "
                                                                       "e.g. torch")] = None,
    ) -> Annotated[CallToolResult, m.AppList]:
        """The apps a GPU can start with (basic, datascience, chat, imagegen, modelapi, voice): what each is for, the
        models it offers and which model we recommend per GPU memory. Give `app` for the software installed."""
        cat = c().catalog()
        picked = [a for a in cat.apps.values() if app is None or a.name == app]
        if app is not None and not picked:
            raise ToolError(f"No app {quoted(app, 40)}. Apps: {', '.join(cat.apps) or '—'}.")
        out = m.AppList(apps=[
            m.AppInfo(name=a.name, display_name=clean(a.display_name) or a.name, summary=clean(a.summary, 300) or "",
                      needs_ssh_key=a.needs_ssh_key, model_required=a.model_required, recommended=a.recommended,
                      models=[m.ModelInfo(name=str(x.get("name")), label=clean(x.get("label")),
                                          size_gb=x.get("size_gb"), min_memory_gb=x.get("min_vram_gb"),
                                          licence=clean(x.get("licence")), gated=x.get("gated"))
                              for x in a.models])
            for a in picked])
        lines = [f"{a.name} ({a.display_name}): {a.summary}" + (f" Models: {', '.join(x.name for x in a.models)}."
                                                               if a.models else "") for a in out.apps]
        if app is not None:
            contents = c().image_contents(app)
            for img in contents.get("images") or []:
                man = img.get("manifest") or {}
                packages = man.get("packages") or []
                want = (package or "").lower().strip()
                sw = m.Software(
                    image=str(img.get("slug")), cuda=img.get("cuda_version") or man.get("cuda"),
                    ubuntu=man.get("ubuntu"), python=man.get("python"), headline=list(man.get("headline") or []),
                    tools=list(man.get("tools") or []), package_count=len(packages),
                    matching_packages=[p for p in packages if want and want in str(p.get("name", "")).lower()][:50])
                out.software.append(sw)
                head = ", ".join(f"{h.get('name')} {h.get('version')}" for h in sw.headline)
                lines.append(f"Image {sw.image}: Ubuntu {sw.ubuntu or '?'}, CUDA {sw.cuda or '?'}, "
                             f"Python {sw.python or '?'}" + (f"; {head}" if head else "")
                             + f"; {sw.package_count} Python packages.")
                if want:
                    found = ", ".join(f"{p.get('name')} {p.get('version')}" for p in sw.matching_packages)
                    lines.append(f"  Matching {quoted(package, 60)}: {found or 'none'}")
        return result("\n".join(lines), out)

    @server.tool(annotations=READ, title="List machines")
    @guard
    def list_machines(
        active_only: Annotated[bool, Field(description="Only starting or running machines")] = True,
        whole_team: Annotated[bool, Field(description="The whole team's machines (owners and admins)")] = False,
        limit: Annotated[int, Field(ge=1, le=100)] = 20,
    ) -> Annotated[CallToolResult, m.MachineList]:
        """Your machines, newest first: state, GPU, app, price, time left and cost so far."""
        found = c().list_instances(active=True if active_only else None, everyone=whole_team, limit=limit)
        out = m.MachineList(machines=[_summary(i) for i in found])
        if not out.machines:
            return result("No machines are running." if active_only else "No machines yet.", out)
        return result("\n".join(_machine_line(d) for d in out.machines), out)

    @server.tool(annotations=READ, title="Get a machine")
    @guard
    def get_machine(
        machine_id: Annotated[str, Field(min_length=3, max_length=40, description="e.g. cnx-7f3a9c")],
    ) -> Annotated[CallToolResult, m.MachineDetail]:
        """One machine: its state, how to connect (SSH command, browser apps, API or endpoint address), time left,
        keep-running status and costs. Never its keys."""
        d = _machine(c().get_instance(machine_id.strip()))
        return result(_machine_text(d), d)

    @server.tool(annotations=READ, title="Get usage")
    @guard
    def get_usage(
        days: Annotated[int, Field(ge=1, le=365, description="The last N days")] = 30,
    ) -> Annotated[CallToolResult, m.Usage]:
        """Spending and GPU-hours over the last N days, by GPU, app, day and project."""
        u = c().usage(days)
        out = m.Usage(days=u.days, balance_usd=usd(u.balance_cents) or 0.0, spend_usd=usd(u.spend_cents) or 0.0,
                      topups_usd=usd(u.topups_cents) or 0.0, sessions=u.sessions, gpu_hours=u.gpu_hours,
                      by_gpu=dollars(u.by_gpu), by_app=dollars(u.by_image), daily=dollars(u.daily),
                      projects=dollars(u.projects))
        return result(f"Last {out.days} days: spent ${out.spend_usd:,.2f} on {out.sessions} session(s), "
                      f"{out.gpu_hours:g} GPU-hours. Added ${out.topups_usd:,.2f}. Balance now "
                      f"${out.balance_usd:,.2f}.", out)

    @server.tool(annotations=READ, title="List receipts")
    @guard
    def list_receipts(
        days: Annotated[int, Field(ge=1, le=365, description="Sessions that ended in the last N days")] = 30,
        limit: Annotated[int, Field(ge=1, le=200)] = 50,
    ) -> Annotated[CallToolResult, m.ReceiptList]:
        """Finished sessions and what each cost in the end (charged minus refunded)."""
        found = c().receipts(days)[:limit]
        out = m.ReceiptList(total_net_usd=usd(sum(r.net_cents for r in found)) or 0.0, receipts=[
            m.Receipt(machine=r.instance, label=clean(r.label), gpu=r.gpu, gpu_count=r.gpu_count, app=r.app,
                      model=clean(r.model, 200), started_at=when(r.started_at), ended_at=when(r.ended_at),
                      used_minutes=r.used_minutes, booked_minutes=r.booked_minutes,
                      charged_usd=usd(r.charged_cents) or 0.0, refunded_usd=usd(r.refunded_cents) or 0.0,
                      net_usd=usd(r.net_cents) or 0.0, project=clean(r.project), outcome=r.outcome)
            for r in found])
        if not out.receipts:
            return result(f"No finished sessions in the last {days} days.", out)
        lines = [f"{r.machine} · {r.gpu} · {r.app} · {duration(r.used_minutes)} used · ${r.net_usd:.2f}"
                 for r in out.receipts]
        return result("\n".join(lines) + f"\nTotal ${out.total_net_usd:,.2f}.", out)

    @server.tool(annotations=READ, title="List projects")
    @guard
    def list_projects() -> Annotated[CallToolResult, m.ProjectList]:
        """Saved projects (files restored into ~/work on machines launched with them) and their size."""
        out = m.ProjectList(projects=[
            m.Project(id=p.id, name=clean(p.name) or "", size_mb=round(p.bytes_used / 1_000_000, 1), files=p.files,
                      in_use_by=p.in_use_by, last_used_at=when(p.last_used_at))
            for p in c().list_projects()])
        if not out.projects:
            return result("No projects yet. Create one in the dashboard, or with `cnx projects add NAME`.", out)
        return result("\n".join(f"{quoted(p.name)} ({p.id}) · {p.size_mb:g} MB, {p.files} files"
                                + (f" · in use by {p.in_use_by}" if p.in_use_by else "") for p in out.projects), out)

    @server.tool(annotations=READ, title="List endpoints")
    @guard
    def list_endpoints() -> Annotated[CallToolResult, m.EndpointList]:
        """Endpoints: stable HTTPS addresses for your own server on a machine's port 8000, and the machine each
        points at now. Never their keys."""
        out = m.EndpointList(endpoints=[m.Endpoint(id=e.id, name=clean(e.name) or "", url=e.url, machine=e.instance)
                                        for e in c().list_endpoints()])
        if not out.endpoints:
            return result("No endpoints yet.", out)
        return result("\n".join(f"{quoted(e.name)} ({e.id}) · {e.url} · "
                                + (f"→ {e.machine}" if e.machine else "no machine") for e in out.endpoints), out)

    if scope == "read":
        return server

    # ------------------------------------------------------------ tools that spend or change

    def find_project(ref: str) -> str:
        for p in c().list_projects():
            if p.id == ref or p.name.lower() == ref.strip().lower():
                return p.id
        raise ToolError(f"No project {quoted(ref)}. list_projects shows them; create one in the dashboard.")

    def find_endpoint(ref: str) -> computenexa.Endpoint:
        for e in c().list_endpoints():
            if ref in (e.id, e.handle) or e.name == ref.strip():
                return e
        raise ToolError(f"No endpoint {quoted(ref)}. list_endpoints shows them.")

    def launch_quote(gpu: str, app: str, hours: float, gpu_count: int, region: str,
                     keep_running_hours: int | None) -> tuple[m.Quote, int]:
        cat = c().catalog(None if region == "any" else region)
        try:
            g = cat.gpu(gpu)
        except KeyError:
            raise ToolError(f"No GPU {quoted(gpu, 40)}. list_gpus shows the GPUs for sale.") from None
        if not g.available or g.price_per_hour_cents is None:
            raise ToolError(f"{g.name} isn't for sale right now" + (f" in {region}" if region != "any" else "")
                            + ". list_gpus shows what's in stock.")
        if app not in g.apps:
            raise ToolError(f"{g.name} doesn't run the {quoted(app, 40)} app. It runs: {', '.join(g.apps) or '—'}.")
        minutes = round(hours * 60)
        if minutes < 1:
            raise ToolError("Book at least 1 minute.")
        if g.billing_unit == "hour":
            minutes = whole_hours(minutes)
        price = g.price_per_hour_cents * gpu_count
        total = charge_cents(price, minutes)
        renewal = charge_cents(price, keep_running_hours * 60) if keep_running_hours else None
        what = f"{gpu_count} × {g.name}" if gpu_count > 1 else g.name
        limit = check_limit(total, f"{what} for {duration(minutes)}")
        if renewal is not None:
            check_limit(renewal, f"Each keep-running renewal of {keep_running_hours} h")
        desc = (f"{what} with the {app} app for {duration(minutes)}"
                + (" (billed per started hour)" if g.billing_unit == "hour" else "") + f": {money(total)} charged now "
                f"({money(price)}/h); unused time is refunded if it stops early.")
        if renewal is not None:
            desc += (f" Keep running: then {money(renewal)} every {keep_running_hours} h from the team's balance "
                     "until it's stopped.")
        return m.Quote(description=desc, price_per_hour_usd=usd(price) or 0.0, minutes=minutes,
                       total_usd=usd(total) or 0.0, renewal_usd=usd(renewal), renewal_hours=keep_running_hours,
                       limit_usd=limit), total

    @server.tool(annotations=SPENDS, title="Launch a machine")
    @guard
    def launch_machine(
        gpu: Annotated[str, Field(min_length=2, max_length=40, description="GPU slug from list_gpus, e.g. rtx-a6000")],
        app: Annotated[str, Field(min_length=2, max_length=40, description="App from list_apps, e.g. datascience")],
        hours: Annotated[float, Field(gt=0, le=720, description="How long to book; unused time is refunded")],
        gpu_count: Annotated[int, Field(ge=1, le=8)] = 1,
        region: Annotated[Literal["any", "us", "ca"], Field(description="Where it must run")] = "any",
        model: Annotated[str | None, Field(max_length=200, description="For apps with models; left out = our "
                                                                       "recommendation for this GPU")] = None,
        project: Annotated[str | None, Field(max_length=80, description="Project name or id to restore into "
                                                                        "~/work and save back")] = None,
        endpoint: Annotated[str | None, Field(max_length=80, description="Endpoint name or id this machine "
                                                                         "serves")] = None,
        label: Annotated[str | None, Field(max_length=60, description="A name for the machine")] = None,
        keep_running_hours: Annotated[int | None, Field(ge=1, le=720, description="Renew this many hours at a "
                                                                                  "time until stopped")] = None,
        confirm: Annotated[bool, Field(description="false = only quote (default); true = launch, after the person "
                                                   "agreed to the quote")] = False,
        expected_total_usd: Annotated[float | None, Field(ge=0, description="With confirm=true: the quote's "
                                                                            "total_usd the person agreed to")] = None,
    ) -> Annotated[CallToolResult, m.SpendResult]:
        """Launch a GPU machine. Costs money: call first with confirm=false to get a quote (nothing happens), show it
        to the person, and only after they agree call again with the same arguments, confirm=true and
        expected_total_usd. The booked time is charged up front from the team's balance."""
        quote, total = launch_quote(gpu.strip(), app.strip(), hours, gpu_count, region, keep_running_hours)
        if not confirm:
            return result(f"Quote (nothing launched yet): {quote.description} To launch, call launch_machine again "
                          f"with confirm=true and expected_total_usd={quote.total_usd}.",
                          m.SpendResult(status="quote", quote=quote))
        check_approved(total, expected_total_usd, "expected_total_usd")
        launched = c().launch(gpu.strip(), app=app.strip(), minutes=quote.minutes, gpu_count=gpu_count, region=region,
                              label=clean(label, 60), model=model,
                              project=find_project(project) if project else None,
                              endpoint=find_endpoint(endpoint).id if endpoint else None,
                              keep_running=keep_running_hours)
        d = _machine(launched)
        return result(f"Launched {d.id} ({d.state}): ${d.charged_usd:.2f} charged. It takes a few minutes to start; "
                      "get_machine shows its progress and, once running, how to connect.",
                      m.SpendResult(status="done", quote=quote, machine=d))

    def extension(machine_id: str, hours: float) -> tuple[computenexa.Instance, m.Quote, int]:
        i = c().get_instance(machine_id.strip())
        if i.state not in ("requested", "provisioning", "running"):
            raise ToolError(f"{i.id} has ended ({i.state}): launch a new machine instead.")
        minutes = round(hours * 60)
        if minutes < 1:
            raise ToolError("Add at least 1 minute.")
        if i.raw.get("billing_unit") == "hour":
            minutes = whole_hours(minutes)
        total = charge_cents(i.price_per_hour_cents, minutes)
        limit = check_limit(total, f"{duration(minutes)} more on {i.id}")
        desc = (f"{duration(minutes)} more on {i.id} ({i.gpu}): {money(total)} charged now "
                f"({money(i.price_per_hour_cents)}/h).")
        return i, m.Quote(description=desc, price_per_hour_usd=usd(i.price_per_hour_cents) or 0.0, minutes=minutes,
                          total_usd=usd(total) or 0.0, limit_usd=limit), total

    @server.tool(annotations=SPENDS, title="Extend a machine")
    @guard
    def extend_machine(
        machine_id: Annotated[str, Field(min_length=3, max_length=40)],
        hours: Annotated[float, Field(gt=0, le=720, description="Time to add")],
        confirm: Annotated[bool, Field(description="false = only quote; true = extend, after the person "
                                                   "agreed")] = False,
        expected_total_usd: Annotated[float | None, Field(ge=0, description="With confirm=true: the quoted "
                                                                            "total_usd")] = None,
    ) -> Annotated[CallToolResult, m.SpendResult]:
        """Add time to a machine at the price it launched with. Costs money: quote first (confirm=false), then
        confirm=true with expected_total_usd once the person agrees."""
        i, quote, total = extension(machine_id, hours)
        if not confirm:
            return result(f"Quote (nothing changed yet): {quote.description} To extend, call again with "
                          f"confirm=true and expected_total_usd={quote.total_usd}.",
                          m.SpendResult(status="quote", quote=quote))
        check_approved(total, expected_total_usd, "expected_total_usd")
        d = _machine(c().extend_instance(i.id, minutes=quote.minutes))
        return result(f"Extended {d.id} by {duration(quote.minutes)}: it now runs until {d.expires_at or '—'}.",
                      m.SpendResult(status="done", quote=quote, machine=d))

    @server.tool(annotations=SPENDS, title="Keep a machine running")
    @guard
    def set_keep_running(
        machine_id: Annotated[str, Field(min_length=3, max_length=40)],
        hours: Annotated[int | None, Field(ge=1, le=720, description="Renew this many hours at a time; null = turn "
                                                                     "off (it stops when its paid time ends)")],
        confirm: Annotated[bool, Field(description="Turning on: false = only quote; true = turn on, after the "
                                                   "person agreed")] = False,
        expected_renewal_usd: Annotated[float | None, Field(ge=0, description="With confirm=true: the quoted "
                                                                              "renewal_usd")] = None,
    ) -> Annotated[CallToolResult, m.SpendResult]:
        """Keep running: the machine renews itself `hours` at a time from the team's balance, at its launch price,
        until it's stopped. Turning it on spends money at every renewal: quote first, then confirm=true with
        expected_renewal_usd. Turning it off (hours=null) needs no confirmation."""
        i = c().get_instance(machine_id.strip())
        if hours is None:
            d = _machine(c().set_keep_running(i.id, None))
            return result(f"Keep running is off for {d.id}: it stops at {d.expires_at or 'the end of its paid time'}.",
                          m.SpendResult(status="done", machine=d))
        if i.state not in ("requested", "provisioning", "running"):
            raise ToolError(f"{i.id} has ended ({i.state}).")
        renewal = charge_cents(i.price_per_hour_cents, hours * 60)
        limit = check_limit(renewal, f"Each renewal of {hours} h on {i.id}")
        quote = m.Quote(description=f"Keep {i.id} ({i.gpu}) running: {money(renewal)} every {hours} h from the team's "
                                    "balance until it's stopped. If the balance runs short it stops at the end of the "
                                    "paid time.",
                        price_per_hour_usd=usd(i.price_per_hour_cents) or 0.0, minutes=hours * 60, total_usd=0.0,
                        renewal_usd=usd(renewal), renewal_hours=hours, limit_usd=limit)
        if not confirm:
            return result(f"Quote (nothing changed yet): {quote.description} To turn it on, call again with "
                          f"confirm=true and expected_renewal_usd={quote.renewal_usd}.",
                          m.SpendResult(status="quote", quote=quote))
        check_approved(renewal, expected_renewal_usd, "expected_renewal_usd")
        d = _machine(c().set_keep_running(i.id, hours))
        return result(f"Keep running is on for {d.id}: {money(renewal)} every {hours} h"
                      + (f", next at {d.next_renewal_at}" if d.next_renewal_at else "") + ".",
                      m.SpendResult(status="done", quote=quote, machine=d))

    @server.tool(annotations=ENDS, title="Stop a machine")
    @guard
    def stop_machine(
        machine_id: Annotated[str, Field(min_length=3, max_length=40)],
    ) -> Annotated[CallToolResult, m.StopResult]:
        """End a machine now: stopped if running, cancelled with a full refund if still starting. Files outside a
        project are deleted; a project is saved first. Unused time is refunded. Can't be undone."""
        before = c().get_instance(machine_id.strip())
        if before.state in ("terminated", "failed"):
            d = _machine(before)
            return result(f"{d.id} had already ended ({d.state}).", m.StopResult(machine=d, refunded_usd=0.0))
        after = c().stop_instance(before.id)
        d = _machine(after)
        refunded = max(0, after.refunded_cents - before.refunded_cents)
        text = f"{d.id} is {d.state}. " + (f"Refunded {money(refunded)}." if refunded
                                           else "Unused time is refunded once it has stopped.")
        text += (f" Project {quoted(d.project)} is saved before it ends." if d.project
                 else " Files on it are deleted.")
        return result(text, m.StopResult(machine=d, refunded_usd=usd(refunded) or 0.0))

    @server.tool(annotations=SPENDS, title="Create an endpoint")
    @guard
    def create_endpoint(
        name: Annotated[str, Field(min_length=1, max_length=60, description="e.g. the customer it serves")],
    ) -> Annotated[CallToolResult, m.Endpoint]:
        """A new Endpoint (owners and admins): a stable HTTPS address and key for your own server on port 8000, e.g.
        one per customer you serve. Launch machines for it with launch_machine(endpoint=...). Free. Its key is shown
        once here; get_endpoint_key shows it again."""
        e = c().create_endpoint(clean(name, 60) or name)
        out = m.Endpoint(id=e.id, name=clean(e.name) or "", url=e.url, machine=e.instance, key=e.key)
        return result(f"Created endpoint {quoted(out.name)} at {out.url}. Its key is in this result: share it only "
                      "with whoever calls the endpoint.", out)

    @server.tool(annotations=SECRET, title="Get an endpoint's key")
    @guard
    def get_endpoint_key(
        endpoint: Annotated[str, Field(min_length=1, max_length=80, description="Endpoint name or id")],
    ) -> Annotated[CallToolResult, m.EndpointKey]:
        """An Endpoint's key (owners and admins). Only when the person asks for it: it gives access to their
        server."""
        e = find_endpoint(endpoint)
        key = c().endpoint_key(e.id)
        return result(f"Key for endpoint {quoted(e.name)} ({e.url}) is in this result.",
                      m.EndpointKey(endpoint=clean(e.name) or "", url=e.url, key=key))

    return server


# ---------------------------------------------------------------- start-up


def detect_scope(client: Client) -> tuple[str | None, str | None]:
    """(the key's scope from GET /v1/me, a setup error). Scope None = unknown (an older platform, or the API couldn't
    be reached): every tool is offered and the API refuses what the key may not do."""
    try:
        raw = client.account().raw
    except AuthenticationError as e:
        return None, explain(e)
    except ComputeNexaError:
        return None, None
    key = raw.get("api_key")
    scope = key.get("scope") if isinstance(key, dict) else None
    return (scope if scope in ("read", "write") else None), None


def create(environ: dict[str, str] | None = None, http_client: Any = None) -> MCPServer:
    """The server from the environment: CNX_API_KEY, optional CNX_API_URL and CNX_MCP_MAX_LAUNCH_USD."""
    env = environ if environ is not None else dict(os.environ)
    try:
        limit: float | None = max_launch_usd(env)
        limit_error = None
    except ValueError as e:
        limit, limit_error = None, str(e)
    key = env.get("CNX_API_KEY", "").strip()
    if not key:
        return build_server(None, setup_error="No API key: set CNX_API_KEY in this server's settings to a ComputeNexa "
                                              "API key (dashboard → API keys).")
    try:
        client = Client(key, base_url=env.get("CNX_API_URL") or None, http_client=http_client)
    except ComputeNexaError as e:
        return build_server(None, setup_error=f"{e.message} Check CNX_API_KEY in this server's settings.")
    scope, error = detect_scope(client)
    if error is not None:
        return build_server(None, setup_error=error)
    return build_server(client, scope=scope, limit_usd=limit, setup_error=limit_error)


def main() -> None:
    if len(sys.argv) > 1 and sys.argv[1] in ("--version", "-V"):
        print(f"computenexa-mcp {__version__}")
        return
    if len(sys.argv) > 1 and sys.argv[1] in ("--help", "-h"):
        print("computenexa-mcp: the ComputeNexa MCP server (stdio).\n\n"
              "Environment: CNX_API_KEY (required), CNX_MCP_MAX_LAUNCH_USD (default 25), CNX_API_URL (optional).\n"
              "Add it to your assistant's MCP settings: https://app.computenexa.com/help/mcp")
        return
    create().run("stdio")
