"""The Massive plugin: Massive's US stock and option market data in an Open
Meridian deployment's lake, as a data plugin.

It runs as the SDK runs a plugin (open-meridian 0.35, `meridian.run`): its
data role's class (`Market`, the SDK's listings feed with Massive's own,
lake.py) registered on the sidecar it is launched beside with its whole
declaration (declaration.py) -- its catalogue, its storage, its settings,
its pages (page.py) and its documentation -- and the pages served. On each delivery of
its settings it makes its Massive client again with the key (or, with sample
data, the built-in exchange), sets each asset class's dataset and pace from
its plan, and asks whether Massive answers the key; it reads which of its
datasets a deployment admin has licensed (contract v20) and reports the data
role's figures; then it hears what the lake wants of it and records against
each want or declines it per subject (lake.py, chains.py), and keeps its
timetable: standing wants kept current, days still forming read again, a
final close pushed, the chains due read, the reference tables read daily.
While a standing want stands, it streams Massive's trades and, on a
real-time plan, quotes for the listings wanted, and the option trades of the
series wanted (stream.py), each stream kept open by the SDK's supervisor,
reconnecting when it breaks and catching the gap up from the trade history.

It holds nothing it cannot lose: which listing each subject is, and every
raw answer, are in the storage the deployment grants it (decisions/028); a
restart reads them back and hears the lake again. The key arrives as a
secret setting, never logged, shown or sent anywhere but Massive.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import meridian
from meridian import edge
from meridian.edge import RawStore, storage_root
from meridian.roles import Latency

from .declaration import DECLARATION, PORT
from .declared import KIND
from .lake import Lake, Products
from .page import State, figures, pages, use
from .settings import END_OF_DAY, REAL_TIME, Credentials, config_from
from .source import MassiveSource
from .stream import MassiveFeed, Streamer
from .synthetic import SyntheticTransport, exchange, synthetic_connect
from .vendor import EOD_PER_MINUTE, PAID_PER_MINUTE, STREAMS, Massive

log = logging.getLogger("massive")

#: How often the timetable is looked at.
TICK_SECONDS = 15
#: How often the datasets' licences are read again.
LICENCES_SECONDS = 60


def source_for(state: State, store: RawStore) -> tuple[Massive | None, MassiveSource | None]:
    """The client and the source the settings give: sample data, the key's,
    or none while there is no key; each asset class paced by its plan."""
    config = state.config
    if config.sample_data:
        massive = Massive(store, None, transport=SyntheticTransport(), keyless=True)
        source = MassiveSource(massive, synthetic=True)
    elif config.credentials is not None:
        massive = Massive(store, config.credentials)
        source = MassiveSource(massive)
    else:
        return None, None
    for asset in ("stocks", "options"):
        plan = config.timeliness(asset)
        massive.pace(asset, EOD_PER_MINUTE if plan == END_OF_DAY else PAID_PER_MINUTE)
    return massive, source


def feed_for(state: State, store: RawStore, cluster: str) -> MassiveFeed | None:
    """A session on one of Massive's clusters, as the settings give it: the
    plan's timeliness, its channels (a delayed plan streams no quote), or
    none on an end-of-day plan or with no key."""
    config = state.config
    plan = config.timeliness(cluster)
    if plan == END_OF_DAY:
        return None
    channels = ("T", "Q") if cluster == "stocks" and plan == REAL_TIME else ("T",)
    if config.sample_data:
        return MassiveFeed(
            cluster, "wss://synthetic.invalid", Credentials("sample"), channels, store,
            connect=synthetic_connect(exchange()),
        )  # fmt: skip
    if config.credentials is None:
        return None
    base = STREAMS[REAL_TIME if plan == REAL_TIME else "delayed"]
    return MassiveFeed(cluster, base, config.credentials, channels, store)


async def read_licences(role: Any, state: State) -> None:
    """Which of its own declared datasets a deployment admin has licensed
    (contract v20, W10.9), for the Datasets page."""
    answered = await role.list_datasets()
    state.licensed = frozenset(licence.dataset for licence in answered.licences)


def streams(state: State, store: RawStore, lake: Lake) -> tuple[Streamer, Streamer]:
    """The two streams, each kept open by the SDK's supervisor: stocks into
    the listings feed, a reconnect's gap caught up from the trade history;
    options into the chains."""
    stocks = Streamer(
        lambda: feed_for(state, store, "stocks"),
        lake.heard,
        lake.flush,
        caught_up=lake.caught_up,
        latency=lake.latency,
        session="stocks",
    )
    options = Streamer(
        lambda: feed_for(state, store, "options"),
        lake.chains.heard,
        lake.chains.flush,
        latency=lake.latency,
        session="options",
    )
    lake.streamer, lake.chains.streamer = stocks, options
    return stocks, options


class Market(Lake):
    """The data role's class: Massive's listings and option chains in the
    lake (lake.py, chains.py); the settings applied as they are delivered;
    and in `run` its two streams kept open, its timetable kept, its
    licences read and its latency reported -- each restarted by
    `meridian.run` after a backoff should it stop."""

    def __init__(self) -> None:
        granted = edge.storage_dir()
        super().__init__(
            None,
            Products(granted / "products.json" if granted else None),
            series_path=granted / "series.json" if granted else None,
        )
        self.store = RawStore(storage_root("massive-raw-"), kind=KIND)
        # The sessions' latency (contract v27) and each live row's age at
        # the edge (contract v28), reported every ten seconds through this
        # class (SDK 0.35).
        self.latency = Latency()
        self.state = State(lake=self, latency=self.latency)
        self.state.stocks, self.state.options = streams(self.state, self.store, self)

    async def on_settings(self, delivered: Any) -> None:
        """One delivery of the settings, the first before anything is heard:
        the client made again, the plans set, the streams restarted, and the
        raw answers past their window moved. The key's value is never
        logged: only whether it is set."""
        state = self.state
        state.config = config_from(delivered.values, delivered.missing)
        config = state.config
        log.info(
            "settings delivered: sample data %s, a key %s, stocks %s, options %s",
            config.sample_data, "set" if config.token_set else "not set",
            config.timeliness("stocks"), config.timeliness("options"),
        )  # fmt: skip
        state.massive, state.source = source_for(state, self.store)
        self.source = state.source
        self.use_plan(config.timeliness("stocks"), config.timeliness("options"))
        if config.sample_data:
            # The built-in exchange's listings, as a source's own list.
            await self.seed_symbols(sorted(exchange()["daily"]))
        await self.health()
        self.restream()
        self.chains.restream()
        for streamer in (state.stocks, state.options):
            if streamer is not None:
                streamer.restart()
        try:
            await self.store.tend(self.plugin, settings=delivered)
        except (meridian.MeridianError, OSError, ValueError) as failed:
            log.warning("the raw answers were not tended this time: %s", failed)

    async def health(self) -> None:
        """Say how the source is: a missing key or a refused one is the
        plugin's health, never a ticket."""
        state = self.state
        if state.config.sample_data:
            await self.say(True, "sample data: the built-in exchange")
            return
        source = state.source
        if source is None:
            await self.say(False, "no Massive API key: enter it in Settings")
            return
        try:
            answer = await asyncio.to_thread(source.probe, True)
        except OSError as failed:
            await self.say(False, f"Massive did not answer: {failed}")
            return
        if not answer.answered:
            await self.say(False, f"Massive refused the key: {answer.detail}")
            return
        await self.say(True, "answering")

    async def say(self, healthy: bool, detail: str) -> None:
        """The plugin's health, with the data role's figures as they stand."""
        await self.plugin.report(healthy=healthy, detail=detail[:200], figures=figures(self.state))

    async def on_start(self) -> None:
        self.latency.role = self

    async def run(self) -> None:
        state = self.state
        assert state.stocks is not None and state.options is not None
        await asyncio.gather(
            state.stocks.run(),
            state.options.run(),
            self._timetable(),
            self._licences(),
            self.latency.run(),
        )

    async def _timetable(self) -> None:
        while True:
            try:
                await self.tick()
            except meridian.MeridianError as failed:
                log.warning("the timetable stopped this time: %s", failed)
            await asyncio.sleep(TICK_SECONDS)

    async def _licences(self) -> None:
        """The licences, read on start and every minute."""
        while True:
            try:
                await read_licences(self, self.state)
            except meridian.MeridianError as failed:
                log.warning("the datasets' licences were not read this time: %s", failed)
            await asyncio.sleep(LICENCES_SECONDS)


def main() -> None:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    market = Market()
    use(market.state)
    meridian.run(
        DECLARATION,
        roles=[market],
        pages=pages,
        port=PORT,
        on_settings=market.on_settings,
        figures=lambda: figures(market.state),
    )


if __name__ == "__main__":
    main()
