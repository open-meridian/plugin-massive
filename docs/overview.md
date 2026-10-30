# Overview

The Massive plugin reads Massive's US stock and option market data and
records it in your deployment's lake, where a plugin that values your book,
shows prices or analyses option chains reads it under the licence and
entitlements you set. Massive (formerly Polygon.io) carries the exchanges'
consolidated feeds: the US equity SIPs (CTA and UTP) for stocks, OPRA for
options. Each dataset names that source as its vendor and Massive as its
aggregator.

It holds one role, `data`: the first-use path, the common pages and the
Summary figures are described once, on [Using a data
plugin](https://open-meridian.dev/how-to/using-a-data-plugin/). These
sections cover what is Massive's own.

## One dataset per timeliness

Massive sells stocks and options as separate plans, each real time, delayed
15 minutes or end of day. The plugin declares each kind twice, real time and
delayed, and records into the one your plan serves, as you set it in
Settings; a read of the other is declined, not covered.

## Its stock datasets

- **Daily** (`massive:daily`): each listing's close and daily bar for a
  business date -- open, high, low, close, volume, volume-weighted price and
  number of trades -- as traded, not adjusted for splits. A date is New
  York's trading day, final at the 16:00 close; until then the day is still
  forming, and is updated in place as it fills. Read when a plugin asks, and
  each evening the closes of every listing already served are recorded
  unasked. Every plan serves it, the free plan included.
- **Live** (`massive:live`) and **Live, delayed** (`massive:live_delayed`):
  the last trade's price and the national best bid and offer, kept current
  once a minute while something reads them. On a real-time plan the best bid
  and offer is also recorded as a quote with its sizes in shares, streamed
  as it changes, at most once a second. A delayed plan includes no quote.
- **Trades** (`massive:trades`) and **Trades, delayed**
  (`massive:trades_delayed`): each trade, streamed while something reads the
  listing, with what it may count for in a day's high, low, open, close and
  volume, from Massive's own rules for each trade condition, and the SIP's
  sequence number. Kept two days by default. A range is read from Massive's
  trade history, and if the stream drops or the plugin restarts, the gap is
  caught up from it, a trade never recorded twice.

## Its option chains

- **Options** (`massive:options`) and **Options, delayed**
  (`massive:options_delayed`): an underlying's option chain -- each series'
  terms (right, strike, expiration, style, settlement, multiplier and, for a
  standard contract, its 100 shares), its open interest and volume, its
  trades streamed while something reads them, and on a real-time plan its
  best bid and offer with sizes in contracts. A delayed plan includes no
  quote. A free (end-of-day) plan lists contracts alone: their terms are
  recorded into `options_delayed`, and nothing else.

Each series is named by its OCC symbol under its underlying, never added to
your instruments: a series gets its own record only when a plugin asks for
one, for an order, under your deployment's series request rule. A chain is
read for the expirations or series a plugin asks for, and, while something
keeps reading it, kept current, each expiration at most once a minute.

Open interest is the count at the end of the previous market day, dated by
your deployment's US equity calendar; if the calendar does not answer, open
interest is not recorded rather than misdated. Volume is dated by the day
Massive states for it. Greeks, implied volatility and Massive's fair market
value are not read: your deployment computes its own.

## How it records

Every price is exact, as Massive stated it. Every answer and stream message
Massive sent is kept in the plugin's own storage, without your key, so any
row can be traced to what Massive said. A trade's exchange is matched to
your deployment's venues by Massive's code and its market identifier code.
Massive does not say which side of a trade took liquidity, so the plugin
does not either.

An instrument your deployment knows only from another source, such as a
custodian, is priced under its symbol.
