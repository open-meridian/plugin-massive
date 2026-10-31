# Changelog

## 0.1.1 (2026-10-30)

A bug fix (ruling 926), until the SDK's own calendar helper replaces it
(924).

- **A failed calendar read is tried again:** where the deployment's US
  equity calendar did not answer (or was not found), the read is made again
  after 30 seconds, the wait doubling to at most 15 minutes, until it
  answers. Before, one failed read left open interest undated until the
  next day.

## 0.1.0 (2026-10-30)

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
