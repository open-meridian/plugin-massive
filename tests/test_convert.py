"""Massive's values, converted at the edge (convert.py, conditions.py): every
number exact, an option's ticker its OSI symbol, a contract's terms from its
style, CFI code and deliverables, a trade's row the same from the stream and
the history, a condition's rules Massive's own."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from typing import Any

from massive import convert
from massive.conditions import Conditions, characteristics_of
from massive.vendor import Fetched


def fetched(value: Any, key: str = "raw/1") -> Fetched:
    return Fetched(value, key)


def test_an_option_ticker_is_its_osi_symbol() -> None:
    assert convert.osi_of("O:SPY261016C00575000") == "SPY   261016C00575000"
    assert convert.osi_of("O:SPXW261016P05000000") == "SPXW  261016P05000000"
    assert convert.ticker_of("SPY   261016C00575000") == "O:SPY261016C00575000"
    assert convert.osi_of("SPY261016C00575000") is None
    assert convert.osi_of("O:SPY261016X00575000") is None


def test_a_daily_bar_dated_by_its_new_york_day() -> None:
    start = 1791432000000  # 2026-10-08 00:00 New York
    (bar,) = convert.daily(
        fetched(
            {
                "results": [
                    {
                        "t": start,
                        "o": Decimal("1.5"),
                        "h": Decimal("2"),
                        "l": Decimal("1"),
                        "c": Decimal("1.75"),
                        "v": 1200,
                        "vw": Decimal("1.6"),
                        "n": 31,
                    }
                ]
            }
        ),
        "SPY",
    )
    assert bar.business_date == dt.date(2026, 10, 8) and bar.row_key == "SPY:1d:2026-10-08"
    assert bar.close == Decimal("1.75") and bar.vwap == Decimal("1.6") and bar.trade_count == 31
    assert bar.raw_key == "raw/1#0" and bar.complete


def test_a_price_past_eighteen_places_does_not_convert() -> None:
    (bar,) = convert.daily(
        fetched(
            {
                "results": [
                    {
                        "t": 1791432000000,
                        "o": 1,
                        "h": 1,
                        "l": 1,
                        "c": Decimal("1.1234567890123456789"),
                        "v": 1,
                    }
                ]
            }
        ),
        "SPY",
    )
    assert bar.close is None and [u.code for u in bar.unconverted] == ["1.1234567890123456789"]


def test_a_trade_is_one_row_from_the_stream_and_the_history() -> None:
    sip_ns = 1791576000123456789
    (streamed,) = convert.streamed(
        {
            "ev": "T",
            "sym": "SPY",
            "x": 12,
            "p": Decimal("673.8"),
            "s": 100,
            "c": [37],
            "t": sip_ns // 1_000_000,
            "pt": sip_ns // 1_000_000 - 1,
            "q": 4101,
        },
        "stream#0",
    )
    (history,) = convert.history_trades(
        fetched(
            {
                "results": [
                    {
                        "sip_timestamp": sip_ns,
                        "participant_timestamp": sip_ns - 10**6,
                        "exchange": 12,
                        "price": Decimal("673.8"),
                        "size": 100,
                        "conditions": [37],
                        "sequence_number": 4101,
                    }
                ]
            }
        ),
        "SPY",
    )
    assert streamed.row_key == history.row_key == "SPY:t:2026-10-09:4101"
    assert streamed.at_ns == history.at_ns and streamed.conditions == history.conditions == (37,)
    assert streamed.participant_ns == history.participant_ns


def test_a_fractional_size_is_read_from_its_text() -> None:
    (trade,) = convert.streamed(
        {"ev": "T", "sym": "SPY", "x": 4, "p": Decimal("1"), "s": 0, "ds": "0.25", "t": 1, "q": 1},
        "k",
    )
    assert trade.quantity == Decimal("0.25")


def test_a_quote_with_no_ask_is_one_sided() -> None:
    (quote,) = convert.streamed(
        {"ev": "Q", "sym": "BRK.B", "bp": Decimal("490.58"), "bs": 100, "t": 1, "q": 9}, "k"
    )
    assert quote.bid == Decimal("490.58") and quote.ask is None and quote.ask_size is None
    assert quote.row_key == "BRK.B:q:1000000:9"


def test_a_status_converts_to_nothing() -> None:
    assert convert.streamed({"ev": "status", "status": "connected"}, "k") == []
    assert convert.streamed({"ev": "AM", "sym": "SPY"}, "k") == []


def test_the_snapshot_states_last_bid_and_ask() -> None:
    said = {
        "tickers": [
            {
                "ticker": "SPY",
                "lastTrade": {"p": Decimal("673.88"), "s": 100, "t": 5, "x": 12},
                "lastQuote": {
                    "p": Decimal("673.87"),
                    "s": 300,
                    "P": Decimal("673.89"),
                    "S": 200,
                    "t": 4,
                },
            }
        ]
    }
    live = {q.kind: q for q in convert.snapshot_live(fetched(said))}
    assert live["last"].venue_code == "12" and live["last"].at_ns == 5
    assert live["bid"].amount == Decimal("673.87") and live["ask"].venue_code == ""
    (quote,) = convert.snapshot_quotes(fetched(said))
    assert quote.bid_size == 300 and quote.ask_size == 200


def test_contracts_terms_from_style_cfi_and_deliverables() -> None:
    said = {
        "results": [
            {
                "ticker": "O:SPY261016C00575000",
                "underlying_ticker": "SPY",
                "cfi": "OCASPS",
                "contract_type": "call",
                "exercise_style": "american",
                "expiration_date": "2026-10-16",
                "strike_price": 575,
                "shares_per_contract": 100,
            },
            {
                "ticker": "O:SPX261016P05000000",
                "underlying_ticker": "SPX",
                "cfi": "OPEICS",
                "contract_type": "put",
                "exercise_style": "european",
                "expiration_date": "2026-10-16",
                "strike_price": 5000,
                "shares_per_contract": 100,
            },
            {
                "ticker": "O:SPY1261016C00575000",
                "underlying_ticker": "SPY",
                "cfi": "OCASPS",
                "contract_type": "call",
                "exercise_style": "american",
                "expiration_date": "2026-10-16",
                "strike_price": 575,
                "shares_per_contract": 100,
                "additional_underlyings": [{"amount": 100, "type": "equity", "underlying": "SPY"}],
            },
            {
                "ticker": "O:XYZ261016C00010000",
                "underlying_ticker": "XYZ",
                "cfi": "OCXXXX",
                "contract_type": "call",
                "exercise_style": "american",
                "expiration_date": "2026-10-16",
                "strike_price": 10,
                "shares_per_contract": 100,
            },
            {
                "ticker": "O:XYZ261016C00011000",
                "underlying_ticker": "XYZ",
                "cfi": "OCBSPS",
                "contract_type": "call",
                "exercise_style": "bermudan",
                "expiration_date": "2026-10-16",
                "strike_price": 11,
                "shares_per_contract": 100,
            },
        ]
    }
    out = list(convert.contracts(fetched(said)))
    spy, spx, adjusted = out[:3]
    assert isinstance(spy, convert.Contract) and spy.standard
    assert spy.settlement_method == "SETTLEMENT_METHOD_PHYSICAL"
    assert spy.exercise_style == "EXERCISE_STYLE_AMERICAN"
    assert isinstance(spx, convert.Contract) and not spx.standard
    assert spx.settlement_method == "SETTLEMENT_METHOD_CASH"
    assert spx.exercise_style == "EXERCISE_STYLE_EUROPEAN"
    assert isinstance(adjusted, convert.Contract) and not adjusted.standard
    assert adjusted.osi == "SPY1  261016C00575000"
    assert [getattr(u, "scheme", "") for u in out[3:]] == [
        "massive:contracts.cfi",
        "massive:contracts.exercise_style",
    ]


def test_a_chain_snapshot_states_quote_volume_and_open_interest() -> None:
    day_ns = 1791576000000000000
    said = {
        "results": [
            {
                "details": {"ticker": "O:SPY261016C00575000"},
                "day": {"volume": 868, "last_updated": day_ns},
                "last_quote": {
                    "bid": Decimal("8.1"),
                    "ask": Decimal("8.2"),
                    "bid_size": 40,
                    "ask_size": 35,
                    "last_updated": day_ns,
                },
                "open_interest": 1543,
                "greeks": {"delta": Decimal("0.5")},
                "implied_volatility": 1,
            }
        ]
    }
    (option,) = convert.chain(fetched(said))
    assert option.osi == "SPY   261016C00575000"
    assert option.bid == Decimal("8.1") and option.ask == Decimal("8.2")
    assert option.bid_size == 40 and option.ask_size == 35
    assert option.volume == 868 and option.volume_date == dt.date(2026, 10, 9)
    assert option.open_interest == 1543 and option.quote_at_ns == day_ns


def test_exchanges_by_code_with_their_mic() -> None:
    said = {"results": [{"id": 12, "mic": "XNAS", "name": "Nasdaq"}, {"id": 4, "mic": ""}]}
    held = convert.exchanges(fetched(said))
    assert held["12"].mic == "XNAS" and held["4"].mic == ""


def test_conditions_are_massives_rules_together() -> None:
    rule = {"updates_high_low": True, "updates_open_close": True, "updates_volume": True}
    odd = {"updates_high_low": False, "updates_open_close": False, "updates_volume": True}
    table = Conditions()
    table.load(
        "stocks",
        [
            fetched(
                {
                    "results": [
                        {
                            "id": 0,
                            "name": "Regular Trade",
                            "update_rules": {"consolidated": rule, "market_center": rule},
                        },
                        {
                            "id": 37,
                            "name": "Odd Lot Trade",
                            "update_rules": {"consolidated": odd, "market_center": odd},
                        },
                        {
                            "id": 14,
                            "name": "Intermarket Sweep",
                            "update_rules": {"consolidated": rule},
                        },
                    ]
                }
            )
        ],
        dt.date(2026, 10, 9),
    )
    regular = table.attributes("stocks", [0])
    assert regular.consolidated == (True, True, True, True) and regular.characteristics == ()
    lot = table.attributes("stocks", [0, 37])
    assert lot.consolidated == (False, False, False, True)
    assert lot.characteristics == ("odd_lot",)
    sweep = table.attributes("stocks", [14])
    assert sweep.consolidated == (True, True, True, True)
    assert sweep.market_centre == (None, None, None, None)
    unknown = table.attributes("stocks", [0, 999])
    assert unknown.consolidated == (None, None, None, None)
    assert [(u.scheme, u.code) for u in unknown.unconverted] == [
        ("massive:stocks.condition", "999")
    ]
    assert not table.due("stocks", dt.date(2026, 10, 9)) and table.due(
        "options", dt.date(2026, 10, 9)
    )


def test_a_codes_name_says_its_characteristics() -> None:
    assert characteristics_of("Single Leg Auction Non ISO") == ("other_auction",)
    assert characteristics_of("Single Leg Cross ISO") == ("intermarket_sweep", "cross")
    assert characteristics_of("Multi Leg auto-electronic trade") == ("multi_leg",)
    assert characteristics_of("Form T") == ("extended_hours",)
    assert characteristics_of("Calculated") == ()
