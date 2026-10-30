# Pages

Both under **Manage**, for an admin of the plugin; each is also a read tool
for agents on your deployment's MCP, answering what the page shows. Neither
shows a price, a trade or a quote, nor your key.

## Connection

How the plugin reaches Massive, one group at a time:

- **Key**: the mode (Live, Sample data or No key), whether the API key is
  set, what the plans serve as you set them, and each plan.
- **Limits and use**: the pace kept for each plan, the calls made and those
  rate-limited and waited out, the last call and error, and the trade
  conditions and exchanges read from Massive each day.
- **Stream**: each of Massive's streams, stocks and options -- its state,
  what it streams, since when, and its last error.
- **Sessions**: each stream's latency, once measured.

## Datasets

What the plugin serves your lake:

- **Served**: each of the seven datasets as declared, whether your plans
  serve it, whether a deployment admin has licensed it, what it recorded
  and declined, and the source behind it (the SIPs or OPRA).
- **Wants**: the standing wants it keeps current, the latest few (the tool
  answers every one), streamed or kept current.
- **Subjects**: whether your deployment lets it read an instrument's record.
