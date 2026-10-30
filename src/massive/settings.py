"""What the plugin needs from the deployment, declared as settings (W4.7, W6.11).

In the data role's order (spec/plugin-pages-follow-their-role): no
`environment`, since Massive has one; the credential; the vendor's own; sample
data last.

- **API key** (`massive_api_key`), secret and required: the key of the
  account holder's own Massive account, from its dashboard. Entered by a
  person in the dashboard's Settings form, sent only to Massive -- in the
  `Authorization` header of a REST call, and in the `auth` message of a
  WebSocket session -- never in an address, a log line, a page or a raw record.
  `Credentials` keeps it out of its repr; the Connection page says whether it
  is set, never what it is. Its name follows the data plugins' (the vendor's,
  then what it is: `tiingo_api_token`, `alpaca_api_key_id`).
- **Stocks plan** (`stocks_timeliness`) and **Options plan**
  (`options_timeliness`), each a choice: what the key's plan serves of that
  asset class -- `real_time`, `delayed` (15 minutes) or `end_of_day`.
  Massive sells stocks and options as separate subscriptions, each tiered
  by timeliness, and a key reaches whatever its plans include; Massive's
  answers do not say a plan's tier, so the account holder says it here. The
  plugin records each asset class's live data into the dataset of that
  timeliness (one dataset per timeliness, ruling 382), declines a want the
  plan does not serve, and paces its calls to the plan: an end-of-day plan
  allows five calls a minute, the paid plans no stated limit. `delayed` by
  default, so no dataset is recorded as real time unless a person said so.
  Names proposed (not yet approved): `stocks_timeliness`,
  `options_timeliness`, with the data terms' `timeliness` values
  (spec/every-data-source-is-licensed-on-the-same-terms).
- **Sample data** (`sample_data`, `meridian.sample_data`), a developer's
  setting: responses committed with the plugin in Massive's documented
  shapes, every value invented (synthetic.py), in place of Massive, with no
  key, for the e2e and a first look.

The SDK declares the storage's two settings from the declaration's kind:
`responses_window_days` (30 days by default) and `responses_past_window`.
"""

from __future__ import annotations

from collections.abc import Collection, Mapping
from dataclasses import dataclass, field

import meridian
from meridian import Choice

TOKEN = "massive_api_key"
STOCKS_TIMELINESS = "stocks_timeliness"
OPTIONS_TIMELINESS = "options_timeliness"
SAMPLE_DATA = "sample_data"
REAL_TIME, DELAYED, END_OF_DAY = "real_time", "delayed", "end_of_day"
TIMELINESS = (REAL_TIME, DELAYED, END_OF_DAY)
#: What each timeliness is called wherever a page says it.
SAID = {
    REAL_TIME: "Real time",
    DELAYED: "Delayed 15 minutes",
    END_OF_DAY: "End of day",
}
CHOICES = tuple(Choice(value, SAID[value]) for value in TIMELINESS)


@meridian.settings
class Declared:
    """The settings as declared, in the data role's order: one typed class,
    the SDK's form, which `connect` and the declaration register."""

    massive_api_key: str = meridian.setting(
        "API key",
        required=True,
        secret=True,
        description=(
            "From your Massive account's dashboard (API keys). Sent only to Massive. Massive's "
            "plans for individuals are for personal, non-business use; a business use needs a "
            "business plan with Massive."
        ),
    )
    stocks_timeliness: str = meridian.setting(
        "Stocks plan",
        default=DELAYED,
        choices=CHOICES,
        description=(
            "What your Massive Stocks plan serves: Real time (Advanced, or a business plan "
            "with real-time data), Delayed 15 minutes (Starter, Developer), or End of day "
            "(Basic, the free plan, five calls a minute). Live stock data is recorded into the "
            "dataset of this timeliness."
        ),
    )
    options_timeliness: str = meridian.setting(
        "Options plan",
        default=DELAYED,
        choices=CHOICES,
        description=(
            "What your Massive Options plan serves: Real time (Advanced, or a business plan "
            "with real-time data), Delayed 15 minutes (Starter, Developer), or End of day "
            "(Basic, the free plan: option contracts' terms only, five calls a minute). Option "
            "chains are recorded into the dataset of this timeliness."
        ),
    )
    sample_data: bool = meridian.sample_data(
        description=(
            "Answer from responses committed with the plugin in Massive's documented shapes, "
            "every value invented, instead of calling Massive. No key needed. Development "
            "deployments only."
        )
    )


DECLARED = Declared
#: Each setting it declares, in its order.
SETTINGS: tuple[meridian.Setting, ...] = Declared.declared()  # type: ignore[attr-defined]


@dataclass(frozen=True)
class Credentials:
    """What Massive is called with. Never in a repr, a log line, a page or a
    raw record."""

    key: str = field(repr=False)

    def __post_init__(self) -> None:
        if not self.key:
            raise ValueError("a Massive API key is needed")

    def headers(self) -> dict[str, str]:
        """Massive's authentication header, for the request alone."""
        return {"Authorization": f"Bearer {self.key}"}

    def secrets(self) -> tuple[str, ...]:
        """The values never to be kept anywhere, for raw records to look for."""
        return (self.key,)


def _timeliness(value: object) -> str:
    return str(value) if value in TIMELINESS else DELAYED


@dataclass(frozen=True)
class Config:
    """The settings as the plugin uses them: the key, if given, whether it is
    set (never its value), each asset class's timeliness, and sample data."""

    credentials: Credentials | None = None
    token_set: bool = False
    stocks: str = DELAYED
    options: str = DELAYED
    sample_data: bool = False
    #: The settings the deployment holds nothing for, as delivered.
    missing: tuple[str, ...] = ()

    @property
    def ready(self) -> bool:
        """Whether there is something to ask: sample data, or the key."""
        return self.sample_data or self.credentials is not None

    def timeliness(self, asset_class: str) -> str:
        """What the plan serves of `stocks` or `options`: sample data serves
        every dataset in real time."""
        if self.sample_data:
            return REAL_TIME
        return self.options if asset_class == "options" else self.stocks


def config_from(values: Mapping[str, object], unset: Collection[str] = ()) -> Config:
    """The settings as delivered, typed. A timeliness the deployment holds
    nothing for is `delayed`, the default."""
    key = values.get(TOKEN)
    text = key.strip() if isinstance(key, str) else ""
    return Config(
        credentials=Credentials(text) if text else None,
        token_set=bool(text),
        stocks=DELAYED
        if STOCKS_TIMELINESS in unset
        else _timeliness(values.get(STOCKS_TIMELINESS)),
        options=(
            DELAYED if OPTIONS_TIMELINESS in unset else _timeliness(values.get(OPTIONS_TIMELINESS))
        ),
        sample_data=values.get(SAMPLE_DATA) is True,
        missing=tuple(sorted(unset)),
    )
