"""What a job costs the machine it runs on, and what it is sold for.

The owner, 2026-09-28: "I want it to be something what other software
services provide. markup and raw cost should be around equal at low raw
costs, then with the cost rising, the markup should also rise, but in a bit
lower rate, so people will hesitate less to send larger files."

So a price is the job's cost plus a markup, and the markup is the cost itself
while the cost is small, then grows more slowly than the cost does:

    price = cost + markup(cost)
    markup(cost) = cost                               while cost <= KNEE
                 = KNEE * (cost / KNEE) ** POWER      after it (POWER < 1)

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
ordinary rate it is three times that. That keeps the owner's condition on the
discounts -- that they "at least cover the hardware usage".

Every number is a setting (FAMILY_PRICE_*), and tests/test_pricing.py holds
the whole table to the rules above.
"""
from __future__ import annotations

import math
import os

# ---- what the machine costs -------------------------------------------------
# Railway's published usage prices (docs.railway.com/reference/pricing/plans,
# checked 2026-09-28): $20 a vCPU-month, $10 a GB-month of memory, $0.05 a GB
# out. Coming in is free.
MONTH_SECONDS = 30 * 24 * 3600
USD_PER_VCPU_SECOND = 20 / MONTH_SECONDS
USD_PER_GB_SECOND = 10 / MONTH_SECONDS
USD_PER_GB_EGRESS = 0.05
# Telegram's payout to a bot's developer per Star, via Fragment.
USD_PER_STAR = float(os.environ.get("FAMILY_USD_PER_STAR") or 0.013)
# The most a job may hold: ffmpeg is held to two threads, and a 150 MP decode
# with an encoder's buffers is about 1.5 GB.
JOB_CPUS = 2
JOB_MEMORY_GB = 1.5

# ---- what it is sold for ----------------------------------------------------
# A few seconds of a database and Telegram's API around every job, in ⚡.
OVERHEAD = float(os.environ.get("FAMILY_PRICE_OVERHEAD") or 0.5)
# Where the markup stops being the whole cost, in ⚡.
KNEE = float(os.environ.get("FAMILY_PRICE_KNEE") or 5)
# How fast the markup grows past the knee: 1 would be a flat 100%, 0 a flat
# fee. 0.8 keeps it 60% or more of the cost at the largest file there is.
POWER = float(os.environ.get("FAMILY_PRICE_POWER") or 0.8)
# The least a paid job is sold for.
MINIMUM = int(os.environ.get("FAMILY_PRICE_MINIMUM") or 3)

GB = 1024 ** 3


def deepest_credits_per_star() -> float:
    """The most ⚡ one Star ever buys, from the family's own ladder."""
    import family_link
    ladder = [multiplier for _, multiplier in family_link.TOPUP_LADDER]
    return family_link.TOPUP_MULTIPLIER * max([1.0] + ladder)


def usd_per_credit() -> float:
    """What one ⚡ is worth at the least it may have brought in."""
    return USD_PER_STAR / deepest_credits_per_star()


def running_usd(seconds: float) -> float:
    """A job holding its two cores and 1.5 GB for `seconds`."""
    return seconds * (JOB_CPUS * USD_PER_VCPU_SECOND + JOB_MEMORY_GB * USD_PER_GB_SECOND)


def egress_usd(sent_bytes: float) -> float:
    return sent_bytes / GB * USD_PER_GB_EGRESS


def cost(seconds: float, sent_bytes: float) -> float:
    """The most a job allowed to run `seconds` and send `sent_bytes` costs,
    in ⚡, overhead included."""
    return OVERHEAD + (running_usd(seconds) + egress_usd(sent_bytes)) / usd_per_credit()


def markup(job_cost: float) -> float:
    if job_cost <= KNEE:
        return job_cost
    return KNEE * (job_cost / KNEE) ** POWER


def price(job_cost: float) -> int:
    """What a job of this cost is sold for, in whole ⚡."""
    return max(MINIMUM, math.ceil(job_cost + markup(job_cost) - 1e-9))


def affordable_seconds(sold_for: int, sent_bytes: float) -> float:
    """How long a job sold for `sold_for` ⚡ may run, sending `sent_bytes`,
    before it would cost more than it was sold for."""
    left = (sold_for - OVERHEAD) * usd_per_credit() - egress_usd(sent_bytes)
    return max(0.0, left / running_usd(1))
