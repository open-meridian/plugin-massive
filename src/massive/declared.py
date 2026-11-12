"""What this version declares beside its role, `data` (W8.1, W4.1): its
catalogue and its storage, built from code and used twice -- `__main__`
registers with them, and `meridian plugin upload` reads them from the built
image (declaration.py puts them together with the settings, the pages and
the documentation).

**The catalogue: seven datasets**, Massive's two feed families, each kind
one dataset per timeliness (rulings 382, 551 and 660), as the retail pair
declares theirs:

- `daily`: closes and daily bars, from Massive's daily aggregates,
  unadjusted, with their VWAP and trade count; New York's day, ending at the
  16:00 close; pulled for a read and pushed once a session's close is final.
  One dataset whatever the plan: a close is final at the close, and an
  end-of-day plan serves it as the others do.
- `live` and `live_delayed`: the last trade's price and the national best
  bid and offer as prices, and from the real-time plan the quote with its
  sizes; pulled for a read, a standing want's prices kept current once a
  minute and, real time, its quotes streamed. A delayed plan includes no
  quote, so `live_delayed` holds prices alone, pulled.
- `trades` and `trades_delayed`: each trade with its attributes and SIP
  sequence number, streamed for the listings under a standing want and
  pulled for a range, the gap after a reconnect or a restart caught up.
- `options` and `options_delayed`: an underlying's option chain -- each
  series' terms (`meridian.v1.ListedSeries`), its open interest and volume
  (`meridian.v1.Statistic`), its trades, streamed for a standing want, and
  on the real-time plan its best bid and offer as a quote -- apart from the
  equity datasets, so an admin licenses OPRA's data apart from the SIPs'.

Each delayed dataset declares its delay, 15 minutes (`declared_delay_ns`):
its staleness and edge age are judged against it.

**Who originated it, who carries it** (the dictionary's
`DatasetDeclaration.vendor` and `.aggregator`): Massive carries data it
takes from the exchanges' consolidated feeds, so each dataset names the
source behind it as its vendor -- the US equity SIPs (CTA and UTP) for the
stock datasets, OPRA for the option chains -- and Massive as its
aggregator (rulings 387 and 865). When the SDK carries the terms'
`underlying_source` (ruling 896), each also names it there.

**The terms and pricing Massive states** (contract 30, ruling 1032;
`terms_and_pricing`), in plain words for each dataset: its source (the US
equity SIPs, or OPRA) and Massive's plans -- individual plans for personal,
non-business use, business plans on Massive's business terms, real time
under the exchanges' agreements -- never a licence: the licence the
deployment's admin sets on Data sources is the one enforced, and the plugin
says nothing of whether a deployment meets anyone's terms. A summary, not
legal advice; docs/terms.md says the same.

**How a gap is recovered** (contract 30, `gap_recovery`): `daily`, the
trades and the chains by `backfill` -- Massive serves each again, and a
want asks for it; `live`, streamed, `stream_only`; `live_delayed`, pulled
for the latest alone, `none`.

**Storage**: one kind of raw record, Massive's answers and stream frames as
received (never the key), a month by default.
"""

from __future__ import annotations

from meridian import DatasetDeclaration, RecordKind

from .chains import CADENCE_SECONDS as CHAIN_CADENCE_SECONDS
from .chains import DATA_TYPES as CHAIN_DATA_TYPES
from .chains import OPTIONS, OPTIONS_DELAYED
from .convert import DAY_END_MINUTE
from .lake import (
    DAILY,
    LIVE,
    LIVE_CADENCE_SECONDS,
    LIVE_DELAYED,
    TRADES,
    TRADES_DELAYED,
    US_EQUITY_CALENDAR,
)

#: Who originated each family's data, and who carries it (proposed names).
EQUITY_SOURCE = "US equity SIPs (CTA, UTP)"
OPTIONS_SOURCE = "OPRA"
AGGREGATOR = "Massive"
#: How far behind real time a delayed plan serves: 15 minutes.
DELAY_NS = 15 * 60 * 1_000_000_000
#: How far back Massive's history reaches is its plan's: none is stated.
HISTORY_DAYS = 0
DAY_TIME_ZONE = "America/New_York"

#: Its one kind of raw record, Massive's answers as received.
KIND = "responses"
LABEL = "Raw responses"
WINDOW_DAYS = 30
RESPONSES_KIND = RecordKind(KIND, LABEL, window_days=WINDOW_DAYS)

NOT_AFFILIATED = (
    "Massive is a trademark of its owner. This plugin is not affiliated with or endorsed by "
    "Massive.com, Inc."
)


#: Massive's plans, as docs/terms.md summarises them: every dataset's terms
#: end with these words.
MASSIVE_TERMS = (
    "Massive's individual plans (Basic, Starter, Developer, Advanced) are for personal, "
    "non-business use; a business use needs a business plan, under Massive's business terms "
    "and order form, which allow internal business use, display to your own authorised "
    "users and derived works containing none of the data, bar redistributing the data "
    "itself, and ask that the source's attribution and delay label travel with it. "
    "Real-time data needs the exchanges' agreements, signed in your Massive dashboard; a "
    "real-time plan for individuals asks that you be a non-professional. Prices are "
    "Massive's, per plan, at massive.com. " + NOT_AFFILIATED
)
#: Each source, before Massive's words.
EQUITY_TERMS = "The US equity SIPs' (CTA's and UTP's) consolidated data, carried by Massive. "
OPTIONS_TERMS = "OPRA's consolidated options data, carried by Massive. "
DELAYED_TERMS = "Delayed 15 minutes. "
#: How a gap in each is recovered: Massive serves all but the latest prices
#: again; the live quotes are streamed.
GAP_RECOVERY = {
    DAILY: "backfill",
    LIVE: "stream_only",
    LIVE_DELAYED: "none",
    TRADES: "backfill",
    TRADES_DELAYED: "backfill",
    OPTIONS: "backfill",
    OPTIONS_DELAYED: "backfill",
}


def _stock(
    key: str,
    data_types: list[str],
    modes: list[str],
    *,
    cadence: int = 0,
    delayed: bool = False,
    calendar: bool = False,
) -> DatasetDeclaration:
    return DatasetDeclaration(
        key=key,
        vendor=EQUITY_SOURCE,
        aggregator=AGGREGATOR,
        data_types=data_types,
        modes=modes,
        cadence=cadence,
        history=HISTORY_DAYS,
        terms_and_pricing=EQUITY_TERMS + (DELAYED_TERMS if delayed else "") + MASSIVE_TERMS,
        gap_recovery=GAP_RECOVERY[key],
        day_time_zone=DAY_TIME_ZONE,
        day_end_minute=DAY_END_MINUTE,
        calendar_id=US_EQUITY_CALENDAR if calendar else "",
        declared_delay_ns=DELAY_NS if delayed else 0,
    )


def _chain(key: str, delayed: bool) -> DatasetDeclaration:
    return DatasetDeclaration(
        key=key,
        vendor=OPTIONS_SOURCE,
        aggregator=AGGREGATOR,
        data_types=[*CHAIN_DATA_TYPES[key], "meridian.v1.Trade.source_sequence"],
        modes=["pull", "stream"],
        cadence=CHAIN_CADENCE_SECONDS,
        terms_and_pricing=OPTIONS_TERMS + (DELAYED_TERMS if delayed else "") + MASSIVE_TERMS,
        gap_recovery=GAP_RECOVERY[key],
        day_time_zone=DAY_TIME_ZONE,
        day_end_minute=DAY_END_MINUTE,
        calendar_id=US_EQUITY_CALENDAR,
        declared_delay_ns=DELAY_NS if delayed else 0,
    )


TRADE_TYPES = ["meridian.v1.Trade", "meridian.v1.Trade.source_sequence"]

CATALOGUE = (
    _stock(
        DAILY,
        [
            "meridian.v1.Price",
            "meridian.v1.Bar",
            "meridian.v1.Bar.vwap",
            "meridian.v1.Bar.trade_count",
        ],
        ["pull", "push"],
        cadence=86_400,
        calendar=True,
    ),  # fmt: skip
    _stock(
        LIVE,
        ["meridian.v1.Price", "meridian.v1.Quote"],
        ["pull", "stream"],
        cadence=LIVE_CADENCE_SECONDS,
    ),
    _stock(
        LIVE_DELAYED,
        ["meridian.v1.Price"],
        ["pull"],
        cadence=LIVE_CADENCE_SECONDS,
        delayed=True,
    ),
    _stock(TRADES, TRADE_TYPES, ["pull", "stream"]),
    _stock(
        TRADES_DELAYED,
        TRADE_TYPES,
        ["pull", "stream"],
        delayed=True,
    ),  # fmt: skip
    _chain(OPTIONS, delayed=False),
    _chain(OPTIONS_DELAYED, delayed=True),
)
