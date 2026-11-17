from __future__ import annotations

import math
import os

import big_files
import pricing

MB = 1024 * 1024
big_files.configure("DBOT", "downloaderbot")

FREE_MAX_MB = int(os.environ.get("DBOT_MAX_DOWNLOAD_MB", "48"))
LARGE_MAX_MB = min(int(os.environ.get("DBOT_LARGE_MAX_MB") or big_files.MTPROTO_MAX_BYTES // MB),
                   big_files.MTPROTO_MAX_BYTES // MB)
RUNNING_SECONDS = 60
BOT_API_SEND_BYTES = big_files.BOT_API_SEND_BYTES
CONFIGURED = big_files.CONFIGURED


def _refused() -> None:
    global CONFIGURED
    CONFIGURED = False


big_files.on_disabled(_refused)


def price_for(size_bytes: int) -> int:
    blocks = max(1, math.ceil(size_bytes / (100 * MB)))
    return pricing.price(pricing.cost(RUNNING_SECONDS, blocks * 100 * MB))


def first_price() -> int:
    return price_for(1)


def top_price() -> int:
    return price_for(LARGE_MAX_MB * MB)


def available(chat_id: int) -> bool:
    return big_files.usable(chat_id)
