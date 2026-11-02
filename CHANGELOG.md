# Changelog

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
