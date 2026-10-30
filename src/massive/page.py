"""The plugin's pages, under Manage alone: the data role's common pages
(spec/plugin-pages-follow-their-role), Connection and Datasets, in its
order, each a read tool on the deployment's MCP surface answering what the
page shows under the role's names (`read_connection`, `read_datasets`).
Each record is the role's (`meridian.roles.data`), extended with Massive's
fields; each template is the role's, with Massive's nuance at its blocks
only. A data plugin shows no market data: prices, trades and quotes are the
reading roles' to read, under their entitlement.

- **Connection** (`/`): one group at a time under the role's tab row --
  "Key" (the mode, the key set or not, never shown, what Massive answers it,
  each asset class's plan as the settings say it), "Limits and use" (the
  pace kept by plan, the calls, the last error), "Stream" (each cluster's
  session) and "Sessions" once measured.
- **Datasets** (`/datasets`): "Served" -- the datasets as the catalogue
  declares them, licensed or not, the source behind each: a browser is drawn
  those the plans serve now, one of each kind's two timelinesses, the tool
  answering all seven; "Wants", the latest few drawn (the tool answers every
  one); "Subjects".

Each page fits one screen at 375, 582 and 1280 pixels wide: one-line rows,
the details a click away.
"""

from __future__ import annotations

import datetime as dt
import urllib.parse
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

import meridian
from meridian.plugin.v1 import operations_pb2 as ops
from meridian.roles import Latency, data

from .chains import OPTIONS, OPTIONS_DELAYED
from .convert import NEW_YORK
from .declared import CATALOGUE, EQUITY_SOURCE, OPTIONS_SOURCE
from .lake import DAILY, LIVE, LIVE_DELAYED, PRICE, QUOTE, TRADES, TRADES_DELAYED, Lake
from .records import ConnectionRead, DatasetsRead, Served, SessionRead, Want
from .settings import END_OF_DAY, REAL_TIME, SAID, Config
from .source import MassiveSource
from .stream import Streamer
from .vendor import EOD_PER_MINUTE, NS, PAID_PER_MINUTE, STREAMS, Massive, Usage

TITLE = "Massive"
CONNECTION = "/"
DATASETS = "/datasets"
#: The kit the pages are built on: 0.12.0's "Facts" and "Next step".
KIT = "0.12.0"
#: The standing wants a browser is shown, the latest; the tool answers all.
SHOWN_WANTS = 3
TABS = ("Key", "Limits and use", "Stream")

pages = meridian.Pages(TITLE, templates=Path(__file__).parent / "templates", kit=KIT)


@dataclass
class State:
    """What the pages read: the settings, the lake's half, the source and
    the streams."""

    config: Config = field(default_factory=Config)
    lake: Lake | None = None
    source: MassiveSource | None = None
    massive: Massive | None = None
    stocks: Streamer | None = None
    options: Streamer | None = None
    #: The datasets a deployment admin has licensed (contract v20), as read.
    licensed: frozenset[str] = frozenset()
    #: The sessions' latency (contract v27) and each live row's age at the
    #: edge (contract v28), reported every ten seconds.
    latency: Latency | None = None


_state = State()


def current() -> State:
    return _state


def use(state: State) -> None:
    """The state the pages read from now on."""
    global _state
    _state = state


def _when(at_ns: int) -> str:
    if not at_ns:
        return ""
    moment = dt.datetime.fromtimestamp(at_ns / NS, dt.UTC)
    return moment.isoformat(timespec="seconds").replace("+00:00", "Z")


def shown(at: str) -> str:
    """A moment as the page shows it: New York's time, to the minute."""
    if not at:
        return ""
    moment = dt.datetime.fromisoformat(at.replace("Z", "+00:00")).astimezone(NEW_YORK)
    return moment.strftime("%b %d %H:%M ET")


pages.environment.filters["shown"] = shown


def host(state: State) -> str:
    """The host Massive is asked at, or none: sample data asks none, and there
    is no client while there is no key."""
    if state.config.sample_data or state.massive is None:
        return ""
    return urllib.parse.urlparse(state.massive.base).hostname or ""


def _stream(cluster: str, streamer: Streamer | None, plan: str, sample: bool) -> SessionRead:
    hostname = (
        ""
        if sample or plan == END_OF_DAY
        else urllib.parse.urlparse(STREAMS[REAL_TIME if plan == REAL_TIME else "delayed"]).hostname
        or ""
    )
    if streamer is None or plan == END_OF_DAY:
        return SessionRead(cluster, hostname, "does not stream on an end-of-day plan"
                           if plan == END_OF_DAY else "idle")  # fmt: skip
    held = streamer.session
    return SessionRead(
        cluster=cluster,
        host=hostname,
        state=held.state,
        subscribed=held.subscribed,
        opened=held.opened,
        reconnects=held.reconnects,
        messages=held.messages,
        since=_when(held.since_ns),
        last_message_at=_when(held.last_message_at_ns),
        last_error=held.last_error,
    )


def _pace(plan: str) -> int:
    return EOD_PER_MINUTE if plan == END_OF_DAY else PAID_PER_MINUTE


def connection_read(state: State) -> ConnectionRead:
    config = state.config
    usage = state.massive.usage if state.massive is not None else Usage()
    known = state.source.known if state.source is not None else None
    stocks, options = config.timeliness("stocks"), config.timeliness("options")
    if config.sample_data:
        mode, detail = "Sample data", "Recorded responses; Massive is not called."
    elif config.credentials is None:
        mode, detail = "No key", "Enter the API key in Settings."
    elif known is None:
        mode, detail = "Live", "Not asked yet: Massive is asked when the key is saved."
    elif not known.answered:
        mode, detail = "Live", f"Massive refused the key: {known.detail}"
    else:
        mode, detail = "Live", "Massive answers the key."
    answered = config.sample_data or bool(known and known.answered)
    entitled = (
        f"Stocks: {SAID[stocks].lower()}; options: {SAID[options].lower()}, as the plans "
        "are set in Settings"
        if answered
        else ""
    )
    streams = [
        _stream("stocks", state.stocks, stocks, config.sample_data),
        _stream("options", state.options, options, config.sample_data),
    ]
    open_sessions = sum(1 for s in streams if s.state == "streaming")
    errors = [s.last_error for s in streams if s.last_error and s.state != "streaming"]
    lake = state.lake
    source = state.source
    return ConnectionRead(
        mode=mode,
        credentials=[data.Credential("API key", config.token_set)],
        entitled_at_vendor=entitled,
        limit=(
            f"Stocks {_pace(stocks)} calls a minute, options {_pace(options)}: Massive's free "
            f"plans allow {EOD_PER_MINUTE} a minute, its paid plans state no limit"
        ),
        calls=usage.calls,
        rate_limited=usage.rate_limited,
        last_call_at=_when(usage.last_call_at_ns),
        last_error=usage.last_error or (lake.last_error if lake else "") or next(iter(errors), ""),
        sessions=None if stocks == END_OF_DAY and options == END_OF_DAY else open_sessions,
        session_latency=state.latency.session_latency() if state.latency is not None else [],
        host=host(state),
        detail=detail,
        stocks_plan=SAID[stocks],
        options_plan=SAID[options],
        conditions=sum(len(t) for t in source.conditions.table.values()) if source else 0,
        exchanges=len(source.exchanges) if source else 0,
        last_status=usage.last_status,
        streams=streams,
    )


SERVES = {
    DAILY: "closes and daily bars",
    LIVE: "last, bid and ask; quotes",
    LIVE_DELAYED: "last, bid and ask, delayed 15 minutes",
    TRADES: "trades",
    TRADES_DELAYED: "trades, delayed 15 minutes",
    OPTIONS: "option chains: series, quotes, open interest, volume, trades",
    OPTIONS_DELAYED: "option chains, delayed 15 minutes: no quotes",
}
#: Each kind's datasets, real time and delayed, and its asset class.
KINDS = {
    LIVE: ("stocks", LIVE, LIVE_DELAYED),
    LIVE_DELAYED: ("stocks", LIVE, LIVE_DELAYED),
    TRADES: ("stocks", TRADES, TRADES_DELAYED),
    TRADES_DELAYED: ("stocks", TRADES, TRADES_DELAYED),
    OPTIONS: ("options", OPTIONS, OPTIONS_DELAYED),
    OPTIONS_DELAYED: ("options", OPTIONS, OPTIONS_DELAYED),
}


def _terms(declared: Any) -> str:
    licence = declared.licence_default
    if licence is None or not licence.kept:
        kept = "served, not kept"
    else:
        kept = f"kept {licence.retention_days} days" if licence.retention_days else "kept"
    return kept + ("; one person's" if licence is not None and licence.personal_use else "")


def _mode(mode: object) -> str:
    if isinstance(mode, int):
        return str(ops.ObservationMode.Name(mode)).removeprefix("OBSERVATION_MODE_").lower()
    return str(mode)


def at_vendor(state: State, key: str, connection: ConnectionRead) -> str:
    """Whether the plans serve a dataset, in a word or two."""
    config = state.config
    if key in KINDS:
        asset, real_time, delayed = KINDS[key]
        plan = config.timeliness(asset)
        in_force = real_time if plan == REAL_TIME else delayed
        if plan == END_OF_DAY and not (asset == "options" and key == delayed):
            return "No: the plan is end of day"
        if key != in_force:
            return "No: the plan is " + SAID[plan].lower()
    if connection.mode == "Sample data":
        return "Yes"
    if connection.mode == "No key" or not connection.entitled_at_vendor:
        return "Not asked yet"
    if key == OPTIONS_DELAYED and config.timeliness("options") == END_OF_DAY:
        return "Series only: the plan is end of day"
    return "Yes, as the plan is set"


def datasets_read(state: State) -> DatasetsRead:
    lake = state.lake
    connection = connection_read(state)
    served: list[Served] = []
    for declared in CATALOGUE:
        kind = KINDS.get(declared.key)
        counted = kind[1] if kind else declared.key
        in_force = lake is not None and lake.in_force(counted) == declared.key
        counts = lake.counts[counted] if lake is not None and in_force else None
        dataset = lake.named(declared.key) if lake is not None else declared.key
        served.append(
            Served(
                dataset=dataset,
                data=SERVES.get(declared.key, declared.key),
                arrives=", ".join(_mode(m) for m in declared.modes),
                day="New York's, to 16:00",
                default_terms=_terms(declared),
                entitled_at_vendor=at_vendor(state, declared.key, connection),
                recorded=counts.recorded if counts else 0,
                declined=counts.declined if counts else 0,
                last_recorded=_when(counts.last_recorded_at_ns) if counts else "",
                licensed=dataset in state.licensed,
                key=declared.key,
                source=OPTIONS_SOURCE if kind and kind[0] == "options" else EQUITY_SOURCE,
                restated=counts.restated if counts else 0,
                unchanged=counts.unchanged if counts else 0,
                unconverted=counts.unconverted if counts else 0,
            )
        )
    wants: list[Want] = []
    if lake is not None:
        streamed = set(lake.streamed)
        for standing in reversed(list(lake.standing.values())):
            listings = [lake.products.symbols.get(s, s) for s in standing.subjects]
            on_stream = bool(listings) and all(s in streamed for s in listings)
            answered = lake.by_want.get(standing.want_id, [0, 0])
            if standing.data_type == PRICE and standing.dataset == LIVE:
                said = f"Kept current, {shown(_when(standing.last_at_ns))}"
            else:
                said = "Streamed" if on_stream else "Not streamed"
            wants.append(
                Want(
                    dataset=lake.dataset(standing.dataset),
                    asked=_asks(standing.data_type, standing.kinds),
                    subjects=len(standing.subjects),
                    recorded=answered[0],
                    declined=answered[1],
                    state=said,
                    want_id=standing.want_id,
                    listings=listings,
                    streamed=on_stream,
                )
            )
        chained: dict[str, tuple[list[str], bool]] = {}
        for held in lake.chains.underlyings.values():
            for want_id in sorted({*held.standing, *held.trades, *held.series}):
                symbols, streams = chained.get(want_id, ([], False))
                chained[want_id] = ([*symbols, held.symbol], streams or want_id in held.trades)
        for want_id, (symbols, streams) in chained.items():
            read = [
                at
                for held in lake.chains.underlyings.values()
                if want_id in held.standing
                for at in held.read_at.values()
            ]
            answered = lake.by_want.get(want_id, [0, 0])
            wants.append(
                Want(
                    dataset=lake.chains.dataset(),
                    asked="option trades" if streams else "option chains",
                    subjects=len(symbols),
                    recorded=answered[0],
                    declined=answered[1],
                    state="Streamed"
                    if streams
                    else f"Kept current, {shown(_when(max(read, default=0)))}",
                    want_id=want_id,
                    listings=sorted(symbols),
                    streamed=streams,
                )
            )
    return DatasetsRead(
        served=served,
        wants=wants,
        reads_instruments=lake.reads_instruments if lake is not None else True,
        listings=len(lake.products.symbols) if lake is not None else 0,
        forming=len(lake.forming) if lake is not None else 0,
        streamed=len(lake.streamed) if lake is not None else 0,
        series_streamed=len(lake.chains.streamed) if lake is not None else 0,
        chains=len(lake.chains.underlyings) if lake is not None else 0,
        backfills_a_restart=True,
        calendar_error=lake.chains.calendar_error if lake is not None else "",
    )


def _asks(data_type: str, kinds: tuple[str, ...]) -> str:
    if data_type == PRICE:
        return ", ".join(kinds) or "prices"
    return "quotes" if data_type == QUOTE else "trades"


def status(read: ConnectionRead) -> dict[str, str]:
    """The head's status, the role's Connection words."""
    if read.mode == "Sample data":
        return {"state": "ok", "label": "Sample data"}
    if read.mode == "No key":
        return {"state": "warn", "label": "No key"}
    if not read.entitled_at_vendor:
        return {"state": "error", "label": "Not answering"}
    return {"state": "warn" if read.last_error else "ok", "label": "Answering"}


def figures(state: State) -> list[meridian.Figure]:
    """The data role's figures, in its order (`meridian.roles.data`); Massive
    reports none of its own."""
    read = connection_read(state)
    word = status(read)["label"]
    connection: Any = word
    if word == "Not answering":
        connection = meridian.Figure("", word, state="error", why=read.detail[:200])
    elif word == "Answering" and read.last_error:
        connection = meridian.Figure("", word, state="warn", why=read.last_error[:200])
    served = datasets_read(state).served
    ok = [s for s in served if s.entitled_at_vendor.startswith(("Yes", "Series"))]
    entitled: Any = None
    if read.mode != "No key":
        entitled = f"{len(ok)} of {len(served)} datasets"
    streams = [s for s in read.streams if s.state == "streaming"]
    if read.sessions is None:
        streaming: Any = "Does not stream"
    elif streams:
        streaming = f"{sum(s.subscribed for s in streams)} streamed"
    else:
        broken = next((s for s in read.streams if s.state in ("waiting", "refused")), None)
        streaming = (
            meridian.Figure(
                "", broken.state.capitalize(), state="warn", why=broken.last_error[:200]
            )
            if broken is not None
            else "Off"
        )
    last = max((s.last_recorded for s in served), default="")
    used = state.massive.usage if state.massive is not None else Usage()
    now = state.massive.clock_ns() if state.massive is not None else 0
    spent = used.last_minute(now) if now else 0
    pace = _pace(state.config.timeliness("stocks"))
    return data.figures(
        connection=connection,
        entitled_at_vendor=entitled,
        stream=streaming,
        last_recorded=dt.datetime.fromisoformat(last.replace("Z", "+00:00")) if last else None,
        rate_use=meridian.Figure(
            "",
            f"{spent} of {pace} a minute",
            state="warn" if spent * 5 >= pace * 4 else None,
            why=f"{used.rate_limited} answered 429 and waited out" if used.rate_limited else "",
        ),
    )


pages.environment.globals["connection_status"] = lambda read: status(read)


@pages.page(
    CONNECTION,
    "Connection",
    levels="admin",
    roles="data",
    answers=ConnectionRead,
    name="read_connection",
    description=(
        "How this plugin reaches Massive, as every data plugin's Connection answers it: the "
        "mode (Live, Sample data or No key), the API key set or not (never shown), what the "
        "plans serve as Settings say them, the pace kept and the calls made, the last call "
        "and error, the streams open. Then Massive's own: the host, each asset class's "
        "plan, the trade conditions and exchanges read, the last status, and each stream's "
        "session. No price."
    ),
)
async def connection(request: meridian.Request) -> meridian.Response:
    read = connection_read(_state)
    tabs = [*TABS, *(["Sessions"] if read.session_latency else [])]
    asked = request.query.get("tab", "").replace("-", " ").lower()
    tab = next((t for t in tabs if t.lower() == asked), tabs[0])
    return pages.answer("connection.html", read, tabs=tabs, tab=tab)


@pages.page(
    DATASETS,
    "Datasets",
    levels="admin",
    roles="data",
    answers=DatasetsRead,
    name="read_datasets",
    description=(
        "The datasets the plugin serves the lake, as every data plugin's Datasets answers "
        "them: each one as its catalogue declares it, whether a deployment admin has "
        "licensed it, whether the plans serve it, and what it recorded and declined since "
        "the plugin started; the standing wants it keeps current; whether it may read an "
        "instrument's record. Then Massive's own: the source behind each dataset, the "
        "listings known and streamed, the option chains kept and series streamed, and that "
        "a restart's gap is caught up. No prices, trades or quotes."
    ),
)
async def datasets(request: meridian.Request) -> meridian.Response:
    read = datasets_read(_state)
    if request.tool_name:
        return pages.answer("datasets.html", read, wants=len(read.wants), served=len(read.served))
    # A browser draws the datasets the plans serve now -- one of each kind's
    # two timelinesses -- and the latest few wants, so the page fits one
    # screen at 375 pixels; the tool answers every one.
    shown = [s for s in read.served if not s.entitled_at_vendor.startswith("No:")]
    drawn = replace(read, served=shown, wants=read.wants[:SHOWN_WANTS])
    return pages.answer("datasets.html", drawn, wants=len(read.wants), served=len(read.served))


def serve(plugin: meridian.Plugin, state: State, port: int) -> Any:
    """Serve the pages on loopback beside the sidecar, reading `state`."""
    use(state)
    return pages.serve(plugin, port)
