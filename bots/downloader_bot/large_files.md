# `large_files.py`

[↑ downloader_bot](README.md) · [code](large_files.py)

Downloads over 48 MB: fetched anyway, sent over MTProto, and paid for --
only for somebody who has said yes.

The cloud Bot API will not let a bot send anything over 50 MB, which is why
DownloaderBot stops at 48 (DBOT_MAX_DOWNLOAD_MB): a download that cannot be
sent is a download wasted. shared/big_files.py lifts that the way it does for
ConvertBot -- MTProto, logged in as this same bot, in private chats -- and a
file past the Bot API's limit costs real egress, so it is not free.

Asked for on 2026-09-28: DownloaderBot downloads and sends files over 50 MB, not for free,
behind a switch that allows or refuses those large files and is off by default, so that nobody
spends credit unwillingly; and the bot says beforehand that a download this big costs credit.

So:

```text
off by default, per person, on the /settings screen;
with it off, a file over the free ceiling is refused with the price that
would have applied and a button to switch it on -- nothing past 48 MB is
fetched, and nothing is charged;
with it on, the bot says before it starts that this one costs credit,
fetches up to LARGE_MAX_MB, and charges once the file is here and its size
is known, by the hundred megabytes begun. A balance that does not cover it
is charged nothing and the file is deleted; a send that fails gives the
credit back.
```

The price is shared/pricing.py's rule -- cost plus a markup equal to it while
it is small, growing more slowly after -- over what sending the file costs:
egress, mostly, since coming in is free. So the first 100 MB cost the most,
and each 100 MB after costs less than they did. Until 2.0.0 it was a flat
⚡10 for every 100 MB; tests/test_pricing.py holds every size up to
LARGE_MAX_MB to the rule.

_At `FREE_MAX_MB = int(os.environ.get("DBOT_MAX_DOWNLOAD_MB", "48"))`:_

What is free: the most a download may be when it goes through the Bot API.

_At `LARGE_MAX_MB = min(int(os.environ.get("DBOT_LARGE_MAX_MB") or big_files.MTPROTO_MAX_BYTE …`:_

What a large download may be: Telegram's own limit for a bot over MTProto,
or less if DBOT_LARGE_MAX_MB says so -- never more.

_At `RUNNING_SECONDS = 60`:_

What a large download holds besides the sending: a minute of a job's
resources covers the fetch and the hand-over, which are mostly waiting.

_At `BOT_API_SEND_BYTES = big_files.BOT_API_SEND_BYTES`:_

Past this, a file goes over MTProto rather than the Bot API.

### `_refused()`

Telegram refused the API id and hash (big_files.verify): no switch, and
FREE_MAX_MB is the limit, as if they had never been set.

### `price_for()`

⚡ for a large download of this size, by the 100 MB begun.

### `first_price()`

What the first 100 MB cost, and the least any large download does.

### `top_price()`

What the largest download there is costs.

### `available()`

Whether a file over the free ceiling can be had in this chat at all:
the route is on, and this is a private chat.
