from __future__ import annotations

import math
import os

MONTH_SECONDS = 30 * 24 * 3600
USD_PER_VCPU_SECOND = 20 / MONTH_SECONDS
USD_PER_GB_SECOND = 10 / MONTH_SECONDS
USD_PER_GB_EGRESS = 0.05
USD_PER_STAR = float(os.environ.get("FAMILY_USD_PER_STAR") or 0.013)
JOB_CPUS = 2
JOB_MEMORY_GB = 1.5

OVERHEAD = float(os.environ.get("FAMILY_PRICE_OVERHEAD") or 0.5)
KNEE = float(os.environ.get("FAMILY_PRICE_KNEE") or 5)
POWER = float(os.environ.get("FAMILY_PRICE_POWER") or 0.8)
MINIMUM = int(os.environ.get("FAMILY_PRICE_MINIMUM") or 3)

GB = 1024 ** 3


def deepest_credits_per_star() -> float:
    import family_link
    ladder = [multiplier for _, multiplier in family_link.TOPUP_LADDER]
    return family_link.TOPUP_MULTIPLIER * max([1.0] + ladder)


def usd_per_credit() -> float:
    return USD_PER_STAR / deepest_credits_per_star()


def running_usd(seconds: float) -> float:
    return seconds * (JOB_CPUS * USD_PER_VCPU_SECOND + JOB_MEMORY_GB * USD_PER_GB_SECOND)


def egress_usd(sent_bytes: float) -> float:
    return sent_bytes / GB * USD_PER_GB_EGRESS


def cost(seconds: float, sent_bytes: float) -> float:
    return OVERHEAD + (running_usd(seconds) + egress_usd(sent_bytes)) / usd_per_credit()


def markup(job_cost: float) -> float:
    if job_cost <= KNEE:
        return job_cost
    return KNEE * (job_cost / KNEE) ** POWER


def price(job_cost: float) -> int:
    return max(MINIMUM, math.ceil(job_cost + markup(job_cost) - 1e-9))


def affordable_seconds(sold_for: int, sent_bytes: float) -> float:
    left = (sold_for - OVERHEAD) * usd_per_credit() - egress_usd(sent_bytes)
    return max(0.0, left / running_usd(1))
