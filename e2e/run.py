"""`make e2e`: the plugin on core's plugin harness, on sample data, with no key
(the harness's README). Its answers are the synthetic exchange shaped on
Massive's documented responses, every value invented
(src/massive/synthetic/exchange.json); Massive itself answers nowhere (the
e2e's hosts file).

Two plugins, from the same image: `massive`, this one; and `reader`
(e2e/reader.py), a stand-in for whatever reads prices, trades and quotes.

1. The catalogue: the seven datasets listed by `dashboard__list_datasets` as
   the plugin declared them -- daily pulled and pushed, its day New York's to
   16:00; live and trades pulled and streamed, their delayed twins declaring
   15 minutes; trades kept two days -- each with Massive's individual plans'
   terms, one person's, as its default licence.
2. Licensed and entitled: the deployment admin licenses daily, live and
   trades, and entitles the reader to them.
3. A close wanted: the reader reads SPY's close for 2026-10-08, which the lake
   has not got, so asks the plugin; the plugin records it against the want:
   671.42, consolidated (no venue), its business date a plain date,
   published at the close, its raw record named.
4. A share class: BRK.B, its close and daily bar, with Massive's VWAP and
   trade count.
5. Another source's record: TQQQ, known to the deployment only as a
   custodian's book holds it (its CUSIP and the custodian's symbol), is
   wanted; the plugin reads the record (W3.6), finds its own symbol and
   records the close against the want: 92.85.
6. Live, on a venue the deployment does not hold: SPY's last price, its
   exchange by Massive's code 12, recorded with no venue and the code as
   reported (the harness has no platform to pull the venue from).
7. Quotes: the reader reads SPY's latest quote, which the lake has not got;
   live is streamed, so the read is a standing want, and the plugin records
   its quote, two-sided with its sizes in shares, and streams it from then on.
8. Trades: under those standing wants the synthetic stream's trades are
   recorded, kept two days, read over a range: a regular trade eligible for
   every statistic, an odd lot counted in the volume alone, a code Massive's
   table does not hold kept as reported, its eligibilities not known; each
   with its sequence number and no aggressor.
9. Parity: the plugin's pages, Connection and Datasets, the data role's,
   read under Manage and through `/mcp` as `massive__read_connection` and
   `massive__read_datasets`, the same answers under the role's names; neither
   shows a price.
10. Storage: the plugin's container made again keeps which listing each
    subject is, in the storage the deployment grants it; and it reads on start
    that each of its datasets is licensed, as Datasets then says.
11. `store lake`: each row as recorded, numbered with no holes, the wants
    answered, the venue's miss.

Run by the Makefile from the repository's root, after it builds the image and
copies the harness out; standard library only.
"""

import json
import os
import subprocess
import sys
import time

NAME = "massive-e2e"
LOG = ".e2e/runner.log"
COMPONENTS = ".e2e/components.log"
COMPOSE = ["docker", "compose", "-p", NAME, "-f", ".e2e/harness/compose.yaml",
           "-f", ".e2e/harness/plugins.yaml", "-f", "e2e/plugin.yaml"]
DAY = "2026-10-08"
INSTANCE = "massive"
KEYS = ["daily", "live", "live_delayed", "trades", "trades_delayed", "options", "options_delayed"]
DAILY, LIVE, TRADES = f"{INSTANCE}:daily", f"{INSTANCE}:live", f"{INSTANCE}:trades"
MINUTE_NS = 60 * 10**9
DELAY_NS = 15 * MINUTE_NS


class Failed(Exception):
    pass


def log(text):
    with open(LOG, "a", encoding="utf-8") as kept:
        kept.write(text + "\n")


def compose(*args, check=True):
    done = subprocess.run(COMPOSE + list(args), capture_output=True, text=True, timeout=600)
    log(f"$ compose {' '.join(args)[:300]}\n{done.stdout[-4000:]}{done.stderr[-2000:]}")
    if check and done.returncode != 0:
        raise Failed(f"compose {' '.join(args)[:200]}: {(done.stdout + done.stderr).strip()[-600:]}")
    return done


def runner(*args, check=True):
    done = compose("run", "--rm", "-T", "runner", *args, check=check)
    return done.stdout if check else done


def must(condition, why):
    if not condition:
        raise Failed(why)


def page(instance, level, path):
    said = runner("page", "--instance", instance, "--level", level, path)
    status, _, body = said.partition("\n")
    must(status.strip() == "200", f"{instance} {path} answered {said[-600:]}")
    return body


def reader(path):
    return json.loads(page("reader", "read", path))


def until(seconds, attempt, what):
    deadline = time.monotonic() + seconds
    last = None
    while True:
        try:
            answer = attempt()
        except Failed as failed:
            answer, last = None, failed
        if answer:
            return answer
        if time.monotonic() > deadline:
            raise Failed(f"{what}, in {seconds} seconds" + (f": {last}" if last else ""))
        time.sleep(2)


def call(tool, arguments=None):
    done = runner("mcp", "call", tool, json.dumps(arguments or {}), check=False)
    try:
        answer = json.loads(done.stdout)
    except ValueError:
        raise Failed(f"{tool}: no answer: {(done.stdout + done.stderr)[-400:]}")
    must(answer.get("outcome") in ("read", "unchanged"), f"{tool} answered {done.stdout[-700:]}")
    return answer["data"]


def answered(path, rows="prices"):
    """A read, again until the lake answers it with rows."""
    def attempt():
        got = reader(path)
        return got if got[rows] else None
    return until(90, attempt, f"{path} was never answered")


def run():
    started = time.time()
    compose("--profile", "runner", "down", "-v", "--remove-orphans", check=False)
    compose("up", "-d")
    for instance in (INSTANCE, "reader"):
        runner("ready", "--instance", instance)
    runner("settings", "sample_data=true", "stocks_timeliness=real_time",
           "options_timeliness=real_time")
    runner("grant", "--level", "admin")
    runner("grant", "--instance", "reader", "--level", "read", "--role", "reporting")
    runner("mcp", "connect", "--covers", "deployment_admin", "--covers", f"{INSTANCE}:admin")

    # 1. The catalogue.
    def listed():
        held = {d["dataset"]: d for d in call("dashboard__list_datasets")["datasets"]}
        return held if {f"{INSTANCE}:{k}" for k in KEYS} <= set(held) else None
    held = until(90, listed, "step 1, the datasets were not listed")
    entry = {k: held[f"{INSTANCE}:{k}"]["catalogue_entry"] for k in KEYS}
    must(entry["daily"]["modes"] == ["pull", "push"] and entry["live"]["modes"] == ["pull", "stream"]
         and entry["trades"]["modes"] == ["pull", "stream"]
         and entry["options"]["modes"] == ["pull", "stream"],
         f"step 1, the modes: {[(k, e['modes']) for k, e in entry.items()]}")
    must(all(int(entry[k].get("declared_delay_ns") or 0) == (DELAY_NS if k.endswith("_delayed") else 0)
             for k in KEYS),
         f"step 1, each delayed dataset declares 15 minutes: {[(k, e.get('declared_delay_ns')) for k, e in entry.items()]}")
    must(entry["trades"]["licence_default"]["kept"] is True
         and entry["trades"]["licence_default"]["retention_days"] == 2,
         f"step 1, trades kept two days by default: {entry['trades']['licence_default']}")
    must(entry["daily"]["day_time_zone"] == "America/New_York" and entry["daily"]["day_end_minute"] == 960,
         f"step 1, the declared day: {entry['daily']}")
    must(entry["daily"]["licence_default"]["personal_use"] is True
         and entry["live"]["licence_default"]["kept"] is False,
         f"step 1, Massive's terms as the default licence: {entry['daily']['licence_default']}")

    # 2. Licensed and entitled.
    runner("licence", DAILY, "kept=true", "retention_days=0", "derived_use=true", "display=true",
           "personal_use=true", "note=Massive's individual terms: personal use.")
    runner("licence", LIVE, "kept=true", "retention_days=2", "derived_use=true", "display=true",
           "personal_use=true", "note=Kept two days, for the e2e to read back.")
    runner("licence", TRADES, "kept=true", "retention_days=2", "derived_use=true", "display=true",
           "personal_use=true", "note=Massive's terms; kept two days, for catching up.")
    for dataset in (DAILY, LIVE, TRADES):
        runner("entitle", "--instance", "reader", dataset, "note=The e2e's reader.")

    ids = reader("/resolve")
    spy, brk, other = ids["spy"], ids["brk"], ids["other"]

    # 3. A close wanted, and recorded against the want.
    reader(f"/prices?subject={spy}&date={DAY}&kind=close&dataset={DAILY}")
    close = answered(f"/prices?subject={spy}&date={DAY}&kind=close&dataset={DAILY}")["prices"]
    must(len(close) == 1, f"step 3, one close: {close}")
    row = close[0]
    must(row["amount"] == "671.42" and row["venue_id"] == "" and row["business_date"] == DAY
         and row["published"] and row["version"] == 1
         and row["row_key"] == f"SPY:1d:{DAY}@{spy}" and row["raw"].startswith("responses/"),
         f"step 3, SPY's close as recorded: {row}")

    # 4. A share class: the close and the bar, with Massive's VWAP.
    reader(f"/prices?subject={brk}&date={DAY}&kind=close&dataset={DAILY}")
    brk_close = answered(f"/prices?subject={brk}&date={DAY}&kind=close&dataset={DAILY}")["prices"]
    must(len(brk_close) == 1 and brk_close[0]["amount"] == "488.17"
         and brk_close[0]["row_key"] == f"BRK.B:1d:{DAY}@{brk}",
         f"step 4, BRK.B's close: {brk_close}")
    reader(f"/bars?subject={brk}&date={DAY}&dataset={DAILY}")
    bars = answered(f"/bars?subject={brk}&date={DAY}&dataset={DAILY}", rows="bars")["bars"]
    must(len(bars) == 1 and bars[0]["close"] == "488.17" and bars[0]["vwap"] is True
         and bars[0]["volume"] == "3655812" and bars[0]["trade_count"] == 63980,
         f"step 4, BRK.B's bar, its VWAP and trade count: {bars}")

    # 5. Another source's record, read and priced.
    reader(f"/prices?subject={other}&date={DAY}&kind=close&dataset={DAILY}")
    held = answered(f"/prices?subject={other}&date={DAY}&kind=close&dataset={DAILY}")["prices"]
    must(len(held) == 1 and held[0]["amount"] == "92.85"
         and held[0]["row_key"] == f"TQQQ:1d:{DAY}@{other}",
         f"step 5, TQQQ's close for another source's record: {held}")

    # 6. Live, on a venue the deployment does not hold.
    reader(f"/prices?subject={spy}&kind=last&dataset={LIVE}")
    last = answered(f"/prices?subject={spy}&kind=last&dataset={LIVE}")["prices"]
    must(last[0]["amount"] == "673.88" and last[0]["venue_id"] == ""
         and last[0]["unconverted"] == ["massive:venue=12"] and last[0]["business_date"] == "",
         f"step 6, SPY's last, its exchange kept as reported: {last}")

    # 7. Quotes: a standing want of a streamed dataset.
    def quoted():
        got = reader(f"/quotes?subject={spy}&dataset={LIVE}")
        return got["quotes"] or None
    quote = until(90, quoted, "step 7, SPY's quote was never recorded")[0]
    must(quote["bid"] == "673.87" and quote["ask"] == "673.89" and quote["bid_quantity"] == "300"
         and quote["ask_quantity"] == "200" and quote["business_date"] == "",
         f"step 7, SPY's quote, two-sided with its sizes: {quote}")

    # 8. Trades streamed under the standing wants, read over a range.
    def traded():
        now = time.time_ns()
        got = reader(f"/trades?subject={spy}&dataset={TRADES}"
                     f"&from={now - 10 * MINUTE_NS}&until={now + MINUTE_NS}")
        return got if len({t["row_key"] for t in got["trades"]}) >= 3 else None
    rows = until(120, traded, "step 8, SPY's trades were never recorded")["trades"]
    regular = [t for t in rows if t["consolidated"] == ["ELIGIBLE_ELIGIBLE"] * 4]
    odd = [t for t in rows if "TRADE_CHARACTERISTIC_ODD_LOT" in t["characteristics"]]
    unknown = [t for t in rows if "massive:stocks.condition=999" in t["unconverted"]]
    must(regular and all(r["aggressor"] == "AGGRESSOR_UNSPECIFIED" and r["source_sequence"] > 0
                         and r["business_date"] == "" for r in regular),
         f"step 8, a regular trade, every statistic, no aggressor, its sequence: {rows[:3]}")
    must(odd and odd[0]["consolidated"] == ["ELIGIBLE_NOT_ELIGIBLE"] * 3 + ["ELIGIBLE_ELIGIBLE"],
         f"step 8, an odd lot counted in the volume alone: {odd[:1]}")
    must(unknown and unknown[0]["consolidated"] == ["ELIGIBLE_UNSPECIFIED"] * 4,
         f"step 8, a code not in Massive's table kept as reported: {unknown[:1]}")
    must(all(t["row_key"].startswith("SPY:t:") and spy in t["subjects"] for t in rows),
         f"step 8, each row keyed by its sequence, naming its listing's record: {rows[:1]}")

    # 9. Parity: the pages under Manage and their tools through /mcp.
    tools = runner("mcp", "list").split()
    for tool in (f"{INSTANCE}__read_connection", f"{INSTANCE}__read_datasets"):
        must(tool in tools, f"step 9, {tool} is offered: {tools}")
    connection = call(f"{INSTANCE}__read_connection")
    must(connection["mode"] == "Sample data"
         and connection["credentials"] == [{"label": "API key", "set": False}]
         and connection["stocks_plan"] == "Real time"
         and connection["sessions"] >= 1,
         f"step 9, read_connection: {connection}")
    datasets = call(f"{INSTANCE}__read_datasets")
    by_key = {d["key"]: d for d in datasets["served"]}
    must(by_key["daily"]["recorded"] >= 4 and by_key["live"]["recorded"] >= 1
         and by_key["trades"]["recorded"] >= 3 and datasets["listings"] == 3
         and datasets["reads_instruments"] is True and datasets["streamed"] >= 1
         and datasets["backfills_a_restart"] is True
         and by_key["live_delayed"]["entitled_at_vendor"] == "No: the plan is real time",
         f"step 9, read_datasets: {datasets}")
    shown = page(INSTANCE, "admin", "/datasets")
    must(f"{DAILY}" in shown and "671.42" not in shown and "<h2>Served</h2>" in shown,
         "step 9, the Datasets page, the role's, shows no price")
    shown = page(INSTANCE, "admin", "/")
    must("<h2>Key</h2>" in shown and "?tab=limits-and-use" in shown and "?tab=stream" in shown,
         "step 9, the role's Connection")

    # 10. Storage: which listing each subject is, kept across a new container.
    compose("up", "-d", "--no-deps", "--force-recreate", INSTANCE)
    kept = compose("exec", "-T", INSTANCE, "python", "-c",
                   "import json; from meridian import edge; "
                   "print(json.dumps(json.load(open(edge.storage_dir() / 'products.json'))))").stdout
    products = json.loads(kept)
    must(products.get(spy) == "SPY" and products.get(brk) == "BRK.B" and products.get(other) == "TQQQ"
         and sorted(products.values()) == ["BRK.B", "SPY", "TQQQ"],
         f"step 10, the product map kept: {kept}")
    runner("ready", "--instance", INSTANCE)

    def licensed():
        served = call(f"{INSTANCE}__read_datasets")["served"]
        return served if all(s["licensed"] for s in served if s["key"] in ("daily", "live", "trades")) else None
    until(90, licensed, "step 10, the licences were never read")

    # 11. The store.
    lines = compose("run", "--rm", "-T", "store", "lake").stdout.split("\n")
    log("store lake:\n" + "\n".join(lines))
    daily_rows = [line.split("|") for line in lines if line.startswith(f"row|{DAILY}|")]
    sequences = sorted(int(r[3]) for r in daily_rows)
    must(sequences == list(range(1, len(sequences) + 1)) and len(sequences) >= 3,
         f"step 11, daily's rows numbered from 1 with no holes: {sequences}")
    keys = {(r[2], r[4]) for r in daily_rows}
    must({("price", f"SPY:1d:{DAY}@{spy}"), ("price", f"BRK.B:1d:{DAY}@{brk}"),
          ("bar", f"BRK.B:1d:{DAY}@{brk}")} <= keys, f"step 11, the rows kept: {sorted(keys)}")
    must(any(line.startswith(f"want|{DAILY}|answered|{other}") for line in lines),
         f"step 11, another source's record answered: {[l for l in lines if l.startswith('want|')]}")
    must(any(line.startswith(f"miss|{INSTANCE}|") for line in lines), f"step 11, the venue's miss: {lines}")
    trade_rows = [line.split("|") for line in lines if line.startswith(f"row|{TRADES}|")]
    numbered = sorted(int(r[3]) for r in trade_rows)
    must(numbered and numbered == list(range(1, len(numbered) + 1)),
         f"step 11, the trades numbered from 1 with no holes: {numbered[:20]}")

    print(f"{NAME} OK in {int(time.time() - started)}s on the plugin harness, sample data, no key: "
          "the seven datasets listed as declared, the delayed ones declaring 15 minutes and "
          "Massive's individual terms the default licence; licensed and the reader entitled; "
          "SPY's 2026-10-08 close wanted and recorded against the want (671.42, consolidated, a "
          "plain date, published at the close); BRK.B's close and bar with its VWAP and trade "
          "count; another source's record read and its close recorded (92.85); SPY's last on its "
          "exchange with the venue kept as reported and the miss counted; a quote as a standing "
          "want, streamed; trades streamed and kept, their conditions converted by Massive's "
          "rules, an odd lot and an unknown code among them; Connection and Datasets read under "
          "Manage and through /mcp alike, no price shown; the product map kept across a new "
          "container; `store lake` numbered with no holes")


def main():
    os.makedirs(".e2e", exist_ok=True)
    open(LOG, "w").close()
    try:
        run()
    except (Failed, subprocess.TimeoutExpired, KeyError, ValueError, IndexError, TypeError) as failed:
        logs = compose("logs", "--no-color", check=False).stdout
        with open(COMPONENTS, "a", encoding="utf-8") as kept:
            kept.write(logs)
        # The plugin's and its sidecar's last words, where CI's log shows them.
        said = [line for line in logs.splitlines() if line.startswith((f"{INSTANCE}-", f"{INSTANCE} ", f"sidecar-{INSTANCE}"))]
        print("\n".join(said[-60:]), file=sys.stderr)
        compose("--profile", "runner", "down", "-v", "--remove-orphans", check=False)
        print(f"{NAME} FAILED: {failed}; the runner's log is in {LOG}, the components' in {COMPONENTS}",
              file=sys.stderr)
        return 1
    compose("--profile", "runner", "down", "-v", "--remove-orphans", check=False)
    return 0


if __name__ == "__main__":
    sys.exit(main())
