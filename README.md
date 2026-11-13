# plugin-massive

Massive's US stock and option market data for [Open Meridian](https://open-meridian.com),
as a data plugin: Massive's REST and WebSocket APIs (formerly Polygon.io),
read with the account holder's own Massive API key, into your deployment's
lake. Massive carries the exchanges' consolidated feeds; each dataset names
the source behind it -- the US equity SIPs (CTA, UTP) or OPRA -- and Massive
as its aggregator.

| Dataset | What it holds |
|---|---|
| `daily` | Each listing's close and daily bar, with its VWAP and trade count, unadjusted |
| `live`, `live_delayed` | The last trade's price and the best bid and offer; on a real-time plan the quote with its sizes, streamed |
| `trades`, `trades_delayed` | Each trade with its attributes from Massive's condition rules and its SIP sequence number, streamed, a range and a reconnect's gap read from the trade history |
| `options`, `options_delayed` | An underlying's option chain: each series' terms by OCC symbol, its open interest and volume, its trades streamed, and on a real-time plan its quotes |

**One dataset per timeliness.** Massive sells stocks and options as separate
plans, each real time, delayed 15 minutes or end of day; you say which in
Settings, and the plugin records into the dataset your plan serves. The free
plan serves daily bars and option contracts' terms.

**A Massive API key is needed**, from your Massive dashboard. It is a secret
setting: sent to Massive alone, never logged or shown. Massive's terms are
your deployment's to settle with Massive: each dataset describes them in
words on the Data sources page (contract 30), and
[docs/terms.md](docs/terms.md) says the same.

**A row key fixes its valid time** (spec/the-lake rule 5; open-meridian
0.36.3): an option series' open interest and volume are valid from the start
of the market day they describe, in New York, never when they were read, so
a re-read restates the value alone. A series' terms keep their key's first
valid time and first underlying, held across restarts in the plugin's
storage (`series.json`, beside its raw records, through the SDK's
`option_chains.SeriesTimes`); terms changed since are an adjustment, a
new key valid from that day (`<OSI>:<date>:ADJ`); a series the lake already holds under
another first valid time is taken as recorded, said once, and one asked
under another underlying than its first is skipped and counted.

The documentation the plugin ships with -- overview, setup, pages,
troubleshooting and terms -- is in [docs/](docs/README.md); `AGENTS.md` is
for whoever works on it next.

## Develop

    make ci-remote     # lint and the tests, in a container
    make check         # meridian plugin check --verified --run-tests
    make e2e           # the plugin on the plugin harness, on sample data
    make preview       # the pages on sample data, in preview/

Every gate runs in a container; plugins land CI-first, through a pull
request.

Apache-2.0. Massive is a trademark of its owner; this project is not
affiliated with or endorsed by Massive.com, Inc.
