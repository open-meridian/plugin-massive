# Setting it up

Each step by an admin of the plugin, unless it says otherwise. The plugin
must already be launched, its role approved by a deployment admin.

## 1. The API key

In your Massive account's dashboard, copy your API key. The free plan
(Basic) serves daily bars and option contracts' terms, five calls a minute;
the paid plans serve the rest, as your plan includes it.

## 2. Enter it, and say your plans

**Manage → Settings.**

1. **API key**: a secret setting. Once saved it is never shown again,
   logged, put on a page or sent anywhere but Massive.
2. **Stocks plan** and **Options plan**: what each of your Massive plans
   serves -- **Real time**, **Delayed 15 minutes** or **End of day**.
   Massive's answers do not say a plan's tier, so you say it here; each is
   Delayed 15 minutes until you choose. The plugin records into the dataset
   of that timeliness, paces its calls to the plan (five a minute on End of
   day), and streams only what the plan includes.

**Connection** then says under Key whether the key is set, never its value,
whether Massive answered it, and each plan as you set it. With none, "No key
yet", linking to Settings.

## 3. License and entitle its datasets

A deployment admin, on the deployment's **Data sources** page, licenses the
datasets your plans serve -- `daily`; `live` or `live_delayed`; `trades` or
`trades_delayed`; `options` or `options_delayed` (option chains, licensed
apart from the stock data) -- each starting from Massive's terms for
individual plans, one person's use, and entitles the plugins that read
prices, trades, quotes and option chains. Nothing is read from Massive until
a plugin entitled to a dataset asks for it.

## 4. Watch it work

**Datasets** shows each dataset with whether your plans serve it and what
was recorded and declined; **Connection** the calls made and each stream's
session.
