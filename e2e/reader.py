"""The e2e's reader: a stand-in for whatever reads prices in a deployment (a
reporting plugin valuing a book), and from contract v19 trades and quotes (a
`signal` plugin: `reporting` reads neither), run from the plugin's own image
with this file mounted, on the SDK alone. It holds `custody` beside them
only so that it can name the instruments it asks about as a custodian's
book would, by resolving their identifiers (W3.1): SPY and BRK.B by Massive's own
symbols, and TQQQ as a custodian's book holds it, by its CUSIP and the
custodian's symbol alone, a record Massive's plugin names to Massive by
reading it (W3.6, a data plugin's from contract v18 as ruled 2026-10-10).

Its page answers JSON, for the harness's runner (`page --instance reader
--level read PATH`):

  /resolve                                   the three records' IDs
  /prices?subject=ID[&date=D][&kind=K]&dataset=D
                                             one read: its rows, what it left
                                             unanswered and why
  /bars?subject=ID&date=D&dataset=D          one read of daily bars
  /quotes?subject=ID&dataset=D               the latest quotes in force
  /trades?subject=ID&dataset=D&from=NS&until=NS
                                             one read of trades over a range
  /trades?subject=ID&dataset=D&after=W       the trades recorded after a
                                             watermark (W: what a read said)

Standard library and the SDK only.
"""
import asyncio
import base64
import http.server
import json
import threading
import urllib.parse

import meridian
from meridian import Interface, Page, SourceChoice
from meridian.operations import as_decimal, as_money
from meridian.plugin.v1 import operations_pb2 as ops

PORT = 8000
LOOP = asyncio.new_event_loop()
ASKED = {
    "spy": [
        ops.Identifier(scheme="figi", value="BBG00E2ESPY1"),
        ops.Identifier(scheme="symbol", value="SPY", source="massive"),
    ],
    "brk": [
        ops.Identifier(scheme="figi", value="BBG00E2EBRKB"),
        ops.Identifier(scheme="symbol", value="BRK.B", source="massive"),
    ],
    "other": [
        ops.Identifier(scheme="cusip", value="74347X831"),
        ops.Identifier(scheme="figi", value="BBG00E2ETQQQ"),
        ops.Identifier(scheme="symbol", value="TQQQ", source="e2e-custodian"),
    ],
}
#: What a custodian's statement states beside each identifier (contract v24,
#: W3.7): a description and a currency, the minimum a record is minted from.
#: Each listing also carries its FIGI, invented for the e2e (the Makefile's
#: E2E_MINTS), so it is the platform's INS- record, which the lake wants.
STATED = {
    "spy": "SPDR S&P 500 ETF Trust",
    "brk": "Berkshire Hathaway Inc. Class B",
    "other": "ProShares UltraPro QQQ",
}


def meta_json(meta):
    return {
        "dataset": meta.source.dataset,
        "venue_id": meta.source.venue_id,
        "row_key": meta.row_key,
        "version": meta.version,
        "business_date": meta.business_date,
        "published": [t.at_ns for t in meta.source_times if t.kind == ops.SOURCE_TIME_KIND_PUBLISHED],
        "unconverted": [f"{u.scheme}={u.code}" for u in meta.unconverted],
        "raw": meta.raw.key,
    }


def unanswered(answer):
    return [{"subject": u.subject.entity_id, "dataset": u.dataset,
             "reason": ops.UnansweredReason.Name(u.reason)} for u in answer.unanswered]


class Reader:
    def __init__(self, plugin):
        self.plugin = plugin
        self.ids = {}

    async def resolve(self):
        for name, identifiers in ASKED.items():
            if name not in self.ids:
                found = await self.plugin.resolve_identifier(
                    identifiers=identifiers, stated_description=STATED[name], stated_currency="USD"
                )
                if not found.instrument_id:
                    raise RuntimeError(f"{name} did not resolve: {found}")
                self.ids[name] = found.instrument_id
        return dict(self.ids)

    def asked(self, query):
        one = lambda name: query.get(name, [""])[0]
        asked = {"subjects": [meridian.SubjectRef(entity_id=s) for s in query.get("subject", [])],
                 "sources": SourceChoice(named=query.get("dataset", []))}
        if one("date"):
            asked["business_date"] = one("date")
        return asked, one

    async def prices(self, query):
        asked, one = self.asked(query)
        if one("kind"):
            asked["kinds"] = [one("kind")]
        answer = await self.plugin.list_prices(**asked)
        rows = []
        for price in answer.prices:
            money = as_money(price.price)
            rows.append({**meta_json(price.meta), "kind": ops.PriceKind.Name(price.kind),
                         "amount": str(money.amount), "currency_code": money.currency_code,
                         "instrument_id": money.instrument_id})
        return {"prices": rows, "unanswered": unanswered(answer)}

    async def bars(self, query):
        asked, _ = self.asked(query)
        answer = await self.plugin.list_bars(**asked)
        rows = []
        for bar in answer.bars:
            rows.append({**meta_json(bar.meta), "close": str(as_money(bar.close).amount),
                         "volume": str(as_decimal(bar.volume)), "vwap": bar.HasField("vwap"),
                         "trade_count": bar.trade_count if bar.HasField("trade_count") else None})
        return {"bars": rows, "unanswered": unanswered(answer)}

    async def quotes(self, query):
        asked, _ = self.asked(query)
        answer = await self.plugin.list_quotes(**asked)
        rows = []
        for quote in answer.quotes:
            side = lambda name: str(as_money(getattr(quote, name)).amount) if quote.HasField(name) else None
            size = lambda name: str(as_decimal(getattr(quote, name))) if quote.HasField(name) else None
            rows.append({**meta_json(quote.meta), "bid": side("bid"), "ask": side("ask"),
                         "bid_quantity": size("bid_quantity"), "ask_quantity": size("ask_quantity"),
                         "characteristics": [ops.QuoteCharacteristic.Name(c) for c in quote.characteristics]})
        return {"quotes": rows, "unanswered": unanswered(answer)}

    async def trades(self, query):
        asked, one = self.asked(query)
        if one("after"):
            asked["after_watermark"] = ops.Watermark.FromString(base64.urlsafe_b64decode(one("after")))
        else:
            asked["valid_from_ns"], asked["valid_until_ns"] = int(one("from")), int(one("until"))
        answer = await self.plugin.list_trades(**asked)
        rows = []
        for trade in answer.trades:
            eligible = lambda e: [ops.Eligible.Name(getattr(e, f)) for f in ("high_low", "open", "close", "volume")]
            rows.append({**meta_json(trade.meta), "price": str(as_money(trade.price).amount),
                         "quantity": str(as_decimal(trade.quantity)), "cancelled": trade.cancelled,
                         "aggressor": ops.Aggressor.Name(trade.aggressor),
                         "source_sequence": trade.source_sequence,
                         "characteristics": [ops.TradeCharacteristic.Name(c) for c in trade.attributes.characteristics],
                         "consolidated": eligible(trade.attributes.consolidated),
                         "subjects": [s.entity_id for s in trade.meta.subjects]})
        watermark = base64.urlsafe_b64encode(answer.watermark.SerializeToString()).decode()
        return {"trades": rows, "unanswered": unanswered(answer), "watermark": watermark}

    async def answer(self, path, query):
        if path == "/resolve":
            return await self.resolve()
        if path == "/prices":
            return await self.prices(query)
        if path == "/bars":
            return await self.bars(query)
        if path == "/quotes":
            return await self.quotes(query)
        if path == "/trades":
            return await self.trades(query)
        return {"page": "the e2e's reader"}


def serve(served):
    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            parsed = urllib.parse.urlparse(self.path)
            future = asyncio.run_coroutine_threadsafe(
                served.answer(parsed.path, urllib.parse.parse_qs(parsed.query)), LOOP)
            try:
                body, status = json.dumps(future.result(timeout=60), sort_keys=True), 200
            except Exception as failed:  # said on the page, for the run to quote
                body, status = json.dumps({"failed": f"{type(failed).__name__}: {failed}"}), 500
            data = body.encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *_args):
            pass

    http.server.ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()


#: What the stand-in registers, its whole declaration (contract v25): the
#: e2e prints it with meridian-declaration and the harness holds the
#: stand-in's registration to it.
DECLARATION = meridian.Declaration(
    interface=Interface(
        PORT,
        "Reader",
        pages=[Page("/", "Prices", levels=["read"], roles=["reporting", "signal"])],
    )
)


async def main():
    plugin = await meridian.connect(
        # Contract v25: what the stand-in registers is its declaration.
        declaration=DECLARATION
    )
    print("reader registered as", plugin.identity.instance_id, flush=True)
    threading.Thread(target=serve, args=(Reader(plugin),), daemon=True).start()
    while True:
        await asyncio.sleep(3600)


if __name__ == "__main__":
    asyncio.set_event_loop(LOOP)
    LOOP.run_until_complete(main())
