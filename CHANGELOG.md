# Changelog

## 0.3.0

On open-meridian 0.36.0, contract 30, on core aae501d's harness (chart
0.1.538); `meridian` 0.1.60 in the check. What the plugin records is
unchanged.

- **Massive's terms and pricing in words** (ruling 1032): each dataset's
  `licence_default` (an individual plan's, one person's) is gone, and
  `terms_and_pricing` names its source (the US equity SIPs, or OPRA), its
  delay where it has one, then Massive's plans -- individual plans for
  personal, non-business use, business plans' internal use and display,
  real time under the exchanges' agreements, prices per plan -- and that
  the plugin is not affiliated. Shown on Data sources and in the Datasets
  page's row details (was "kept; one person's"). The licence a deployment
  admin sets is the one enforced; "trades kept two days" is the admin's to
  set now.
- **How a gap is recovered** (`gap_recovery`): `daily`, the trades and the
  chains by `backfill` (Massive serves each again); `live` `stream_only`;
  `live_delayed`, pulled for the latest alone, `none`. No
  `sessions_calendar_id`: the platform's calendar master holds no US equity
  or OPRA trading-sessions calendar yet.
- **Row keys checked against the lake's rule 5** (ruled 2026-11-12): a
  close and daily bar keyed by symbol and business date, a live price,
  quote and trade by its own time or SIP sequence, each under its record
  (`@INS-...`). **Two cases change valid time:** an option series' open
  interest or volume is keyed by its series, its day and its kind (the
  SDK's `helpers.statistic`) but valid from when the plugin read it, so a
  volume read again later the same day restates the key under another valid
  time; and a series' terms (`helpers.option_series`) are keyed by its OCC
  symbol but valid from when read, so terms that change restate under
  another valid time. Neither has a cancellation; listed for step 3d,
  unchanged.
- e2e: core lists each dataset's terms in words and how a gap is recovered
  (was: the default licences).
- `meridian plugin migrate --to 0.36.0` moved the pins (the base by digest,
  `meridian` 0.1.60); its `licence-default-left` findings done by hand.
- Core's runtime and harness images at `aae501d`, by digest.

## 0.2.0

open-meridian 0.35.2 (each role one class; rulings 1003 to 1005 and 1009 to
1029), contract v29, on core v29's harness.

- **The pins:** open-meridian 0.33.4 to 0.35.2, plugin-python:0.35.2 by tag
  and digest, meridian 0.1.57 in the check, and the e2e's runtime and
  harness at core 1ae36f0 (contract v29) by digest.
- **One class for its role:** `Market`, the data role's class (lake.py's
  listings feed and chains), registered with `meridian.run`: the settings
  applied as delivered, and in its `run` the two streams, the timetable,
  the licences and the latency reports, restarted after a backoff should
  one stop. The chains' acts (liveness, the calendar, each record) are
  made through the class, never the connection. The raw answers are tended
  from the settings as delivered (`RawStore.tend(settings=...)`).
- **The streams on the SDK's supervisor** (`helpers.sessions.Supervisor`,
  ruling 1027): its own reconnect loop is gone; the WebSocket stays the
  plugin's standard-library client (ruling 1038). Each wait between
  sessions is now jittered, a stocks reconnect's gap is caught up from when
  each listing was last heard, and each stream's signs of life and
  reconnects are on Connection's Sessions. A message that converts to
  nothing (a status) is still handed on, the feed's sign of life.
- **The edge** is the SDK's listings feed's (`listings.at_the_edge`); the
  plugin's temporary copy is gone, a chain's standing wants told to it.
- **Each row once** through the SDK's `SentRows`, in place of its own copy.
- **The suite at contract v29:** a derived series and a pool's factor
  named not presented, with why.
- **The e2e's reader** is three role classes, `custody`, `reporting` and
  `signal`, on `meridian.run_async`.

## 0.1.2

SDK 0.33.4: raw-record writes and enum conversion fixes (ruling 937).

- **The pins:** open-meridian 0.33.3 to 0.33.4, and plugin-python:0.33.4 by tag
  and digest (`meridian plugin migrate --to 0.33.4`: only the pins move).

## 0.1.1

A bug fix (ruling 926), until the SDK's own calendar helper replaces it
(924).

- **A failed calendar read is tried again:** where the deployment's US
  equity calendar did not answer (or was not found), the read is made again
  after 30 seconds, the wait doubling to at most 15 minutes, until it
  answers. Before, one failed read left open interest undated until the
  next day.

## 0.1.0

The first version: Massive's US stock and option market data from its public
REST and WebSocket APIs, on open-meridian 0.33.3 (contract v28) and meridian
0.1.53 (rulings 865, 866, 868 and 899; 382, 387, 551 and 660).

- **Seven datasets, one per timeliness:** `daily`; `live` and
  `live_delayed`; `trades` and `trades_delayed`; `options` and
  `options_delayed`. Each names the source behind it as its vendor -- the
  US equity SIPs (CTA, UTP), or OPRA -- and Massive as its aggregator; each
  delayed one declares its 15 minutes; each licence default what Massive's
  plans for individuals say.
- **Each asset class's plan** (`stocks_timeliness`, `options_timeliness`):
  real time, delayed 15 minutes or end of day, as the account holder says;
  each kind recorded into the dataset its plan serves, a want the plan does
  not serve declined not covered, calls paced to the plan.
- **Stocks:** daily bars unadjusted with their VWAP and trade count; live
  prices and, real time, quotes from the snapshot; trades and quotes
  streamed over Massive's WebSocket; a trade keyed by its SIP sequence
  number alike from the stream and the history, so a range is read and a
  reconnect's or restart's gap caught up; its attributes from Massive's own
  rules for its conditions; its exchange matched by Massive's code and MIC;
  both clocks kept.
- **Option chains:** each series' terms by OCC OSI symbol under its
  underlying, never resolved, its style, settlement (from its CFI code) and
  deliverable; open interest dated by the deployment's calendar, volume by
  its own day; quotes on a real-time plan; trades streamed for a standing
  want's series. Only the expirations or series a want names are read.
- **The key:** a secret setting, sent to Massive in a header and the
  stream's `auth` message, nowhere else.
- **Sample data:** Massive's documented shapes with every value invented.
  No key needed.
- The data role's two pages, Connection and Datasets, each a read tool; the
  data suite, 28 cases presented; the e2e on the plugin harness of core
  8def82a.
