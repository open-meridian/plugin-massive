"""The pages on sample data, each printed as one HTML file: for looking at
them without a deployment.

    python -m massive.preview connection > preview/connection.html
    python -m massive.preview datasets > preview/datasets.html

The lake's half runs on the SDK's lake as a test stands it in, in place of a
sidecar, against the built-in exchange: SPY's close, live prices and chain
wanted and recorded, its trades a standing want streamed, and an option
series' trades a standing want too. Each page links the kit where the
dashboard serves it, /.meridian/ui/<version>/ (`make preview SHOTS=...`
checks each in a browser). `connection-no-key` and `datasets-no-key` are the
pages before a key is entered.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import sys
import tempfile
from pathlib import Path

from meridian.edge import RawStore
from meridian.plugin.v1 import operations_pb2 as ops
from meridian.receive import Heard
from meridian.roles.data.testing import Lake as Stand_in
from meridian.testing import PageClient

from .lake import Lake, Products
from .page import State, pages, use
from .settings import Config, config_from
from .source import MassiveSource
from .stream import Streamer
from .synthetic import SyntheticTransport, fridays
from .vendor import Massive

SPY = "INS-PREVIEW-SPY"
PAGES = {
    "connection": "/",
    "datasets": "/datasets",
    "connection-no-key": "/",
    "datasets-no-key": "/datasets",
}


def _want(
    want_id: str, key: str, data_type: str, *, standing: bool = False, day: str = "", **more: object
) -> Heard:  # type: ignore[type-arg]
    return Heard(
        row="ObservationsWanted",
        message=ops.ObservationsWantedEvent(
            want_id=want_id,
            dataset=f"massive:{key}",
            data_type=data_type,
            subjects=[ops.SubjectRef(entity_id=SPY)],
            business_date=day,
            standing=standing,
            **more,  # type: ignore[arg-type]
        ),
    )


async def worked(keyed: bool = True) -> State:
    """The plugin's state as sample data leaves it, on the stand-in lake."""
    stand_in = Stand_in("massive")
    stand_in.instruments.hold(SPY, ops.Identifier(scheme="symbol", value="SPY", source="massive"))
    store = RawStore(Path(tempfile.mkdtemp(prefix="massive-preview-")), kind="responses")
    config = config_from({"sample_data": True}) if keyed else Config()
    lake = Lake(stand_in, None, Products(None))
    state = State(config=config, lake=lake)

    async def nothing(*_: object) -> None:
        pass

    state.stocks = Streamer(lambda: None, nothing, nothing)
    state.options = Streamer(lambda: None, nothing, nothing)
    lake.streamer, lake.chains.streamer = state.stocks, state.options
    if not keyed:
        return state
    massive = Massive(store, None, transport=SyntheticTransport(), keyless=True)
    state.massive, state.source = massive, MassiveSource(massive, synthetic=True)
    lake.source = state.source
    lake.use_plan(config.timeliness("stocks"), config.timeliness("options"))
    stand_in.answer(
        "ReadCalendar",
        lambda _: ops.ReadCalendarResult(
            found=True,
            weekends=[ops.CalendarWeekend(weekdays=[ops.WEEKDAY_SATURDAY, ops.WEEKDAY_SUNDAY])],
        ),
    )
    expiry = fridays(dt.datetime.now().date())[0]
    for heard in (
        _want("want-close", "daily", "meridian.v1.Price", day="2026-10-08"),
        _want("want-live", "live", "meridian.v1.Price", standing=True),
        _want("want-trades", "trades", "meridian.v1.Trade", standing=True),
        _want("want-chain", "options", "meridian.v1.Quote", standing=True,
              expiration_from=expiry.isoformat(), expiration_to=expiry.isoformat()),
        _want("want-tape", "options", "meridian.v1.Trade", standing=True,
              expiration_from=expiry.isoformat(), expiration_to=expiry.isoformat()),
    ):  # fmt: skip
        await lake.on_want(heard)
    state.stocks.session.state, state.stocks.session.subscribed = "streaming", len(lake.streamed)
    state.options.session.state = "streaming"
    state.options.session.subscribed = len(lake.chains.streamed)
    return state


def main(argv: list[str]) -> int:
    page = argv[1] if len(argv) > 1 else "connection"
    if page not in PAGES:
        print(f"one of: {', '.join(PAGES)}", file=sys.stderr)
        return 2
    state = asyncio.run(worked(keyed=not page.endswith("-no-key")))
    use(state)
    answer = PageClient(pages).get(PAGES[page], "admin")
    if answer.status != 200:
        print(f"{page} answered {answer.status}", file=sys.stderr)
        return 1
    sys.stdout.write(answer.text)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
