# `pricing.py`

[↑ shared](README.md) · [code](pricing.py)

What a job costs the machine it runs on, and what it is sold for.

2026-09-28: pricing like other software services: markup and raw cost about equal at low
raw costs, then, as the cost rises, a markup that rises too but at a lower rate, so that
people hesitate less to send larger files.

So a price is the job's cost plus a markup, and the markup is the cost itself
while the cost is small, then grows more slowly than the cost does:

```text
price = cost + markup(cost)
markup(cost) = cost                               while cost <= KNEE
             = KNEE * (cost / KNEE) ** POWER      after it (POWER < 1)
```

The markup never stops rising, but the price per megabyte falls as files get
bigger. That shape is what quantity pricing studies find people's
willingness to pay looks like -- rising with quantity, at a falling rate
(Iyengar & Jedidi, "A Conjoint Model of Quantity Discounts", Marketing
Science 31(2), 2012) -- and it is how the services people compare against
charge: CloudConvert sells conversion minutes, at least one per conversion,
cheaper per minute the more are bought.

WHAT "COST" IS. Not the typical job -- the worst one a price can buy. Every
job runs under limits (jobs.py: how long it may run, how much it may send
back), and its cost is those limits priced at Railway's published rates:
two cores and 1.5 GB for as long as it may run, and every byte it may send.
So no price is ever below what its job can cost, whatever the job turns out
to be; the markup is what is kept when the job is typical, which it almost
always is.

IN WHAT MONEY. Credit (⚡), valued at the deepest rate a Star ever buys it:
somebody's first 500 Stars earn 6 ⚡ each (family_link.TOPUP_LADDER), so one ⚡
may have brought in as little as a sixth of a Star. "Markup equal to cost" is
therefore true for the cheapest credit there is; for credit bought at the
ordinary rate it is three times that. That keeps the condition set on the
discounts -- that they "at least cover the hardware usage".

Every number is a setting (FAMILY_PRICE\_\*), and tests/test_pricing.py holds
the whole table to the rules above.

_At `MONTH_SECONDS = 30 * 24 * 3600`:_

---- what the machine costs -------------------------------------------------
Railway's published usage prices (docs.railway.com/reference/pricing/plans,
checked 2026-09-28): $20 a vCPU-month, $10 a GB-month of memory, $0.05 a GB
out. Coming in is free.

_At `USD_PER_STAR = float(os.environ.get("FAMILY_USD_PER_STAR") or 0.013)`:_

Telegram's payout to a bot's developer per Star, via Fragment.

_At `JOB_CPUS = 2`:_

The most a job may hold: ffmpeg is held to two threads, and a 150 MP decode
with an encoder's buffers is about 1.5 GB.

_At `OVERHEAD = float(os.environ.get("FAMILY_PRICE_OVERHEAD") or 0.5)`:_

---- what it is sold for ----------------------------------------------------
A few seconds of a database and Telegram's API around every job, in ⚡.

_At `KNEE = float(os.environ.get("FAMILY_PRICE_KNEE") or 5)`:_

Where the markup stops being the whole cost, in ⚡.

_At `POWER = float(os.environ.get("FAMILY_PRICE_POWER") or 0.8)`:_

How fast the markup grows past the knee: 1 would be a flat 100%, 0 a flat
fee. 0.8 keeps it 60% or more of the cost at the largest file there is.

_At `MINIMUM = int(os.environ.get("FAMILY_PRICE_MINIMUM") or 3)`:_

The least a paid job is sold for.

### `deepest_credits_per_star()`

The most ⚡ one Star ever buys, from the family's own ladder.

### `usd_per_credit()`

What one ⚡ is worth at the least it may have brought in.

### `running_usd()`

A job holding its two cores and 1.5 GB for `seconds`.

### `cost()`

The most a job allowed to run `seconds` and send `sent_bytes` costs,
in ⚡, overhead included.

### `price()`

What a job of this cost is sold for, in whole ⚡.

### `affordable_seconds()`

How long a job sold for `sold_for` ⚡ may run, sending `sent_bytes`,
before it would cost more than it was sold for.
