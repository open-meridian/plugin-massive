# Troubleshooting

## No key yet

Enter the API key on **Settings**. Until then nothing is asked of Massive,
and every read is declined as the source silent.

## Not answering

Massive refused the key or did not answer. **Connection** shows Massive's
words under Last error: a refused key says so; a call your plan does not
include answers "not entitled" -- set **Stocks plan** or **Options plan** to
what your plan serves.

## A read is declined, not covered

The dataset is not the one your plan serves (a real-time read on a delayed
plan, or the reverse), your plan does not include it (quotes on a delayed
plan; anything but daily bars and contracts on an end-of-day plan), or the
listing is one Massive does not carry. **Datasets** says which datasets your
plans serve.

## Rate-limited

On an end-of-day plan Massive allows five calls a minute; the plugin waits
between calls, and a 429 is waited out. **Connection**'s Limits and use
counts both.

## The stream is waiting or refused

A stream that drops is opened again after a second, then up to a minute;
the trades in between are caught up from Massive's trade history. Refused
means Massive refused the key on the stream: it is tried again after five
minutes. By default Massive allows one stream per asset class for a key:
another program using the same key closes this one's.

## Open interest is not recorded

The deployment's US equity calendar did not answer, so the previous market
day is not known. **Datasets** says why. Volume, series and quotes are
recorded meanwhile. The plugin asks the calendar again after 30 seconds,
then less often, at least every 15 minutes, until it answers.
