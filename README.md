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
your deployment's to settle with Massive: see [docs/terms.md](docs/terms.md).

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
