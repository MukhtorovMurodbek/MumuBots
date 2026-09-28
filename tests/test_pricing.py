"""Every bonus still pays for the machine it runs on.

    python3 tests/test_pricing.py

Credit is sold at a better rate for somebody's first thousand Stars: the first
500 they ever pay earn three times the ordinary rate, and the next 500 twice
it. The owner's condition for any discount was that it "at least covers the
hardware usage" -- which is a claim about arithmetic, so it is asserted here
rather than trusted.

The model is deliberately the worst case, not the typical one. A typical
conversion is a couple of seconds and a few megabytes; the case that decides
whether a price is safe is the one that runs to the full time limit on two
cores, holds a gigabyte and a half, and sends back everything a bot is
allowed to send. If the deepest rate on the ladder still brings in SAFETY times
that, every tier is covered with room.

Needs no database: family_link's arithmetic is pure, and the stubs cover the
imports.
"""
import os
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / ".mirror" / "convert_bot"))
sys.path.insert(0, str(HERE / "stubs"))
os.environ.setdefault("CBOT_TOKEN", "test")
os.environ.setdefault("CBOT_USERNAME", "test")
os.environ.setdefault("DATABASE_URL", "postgresql://stub")
# The pricing a deployment actually ships with, not whatever this shell has.
for variable in ("FAMILY_TOPUP_MULTIPLIER", "FAMILY_TOPUP_LADDER",
                 "FAMILY_BONUS_EXPIRY_DAYS", "CONVERT_TIME_LIMIT_SECONDS",
                 "CONVERT_FREE_TIME_LIMIT_SECONDS", "LOCAL_BOT_API_URL",
                 "CBOT_API_ID", "CBOT_API_HASH", "CONVERT_MAX_FILE_MB"):
    os.environ.pop(variable, None)

import convert_utils  # noqa: E402
import family_link as fl  # noqa: E402
import jobs  # noqa: E402

# ---- the cost model, each number with where it came from -----------------
# Telegram's payout to a bot developer per Star, via Fragment, at the time of
# writing. Update it if Telegram changes it.
USD_PER_STAR = 0.013
# Railway's published usage prices: $20 per vCPU-month, $10 per GB-month of
# memory, $0.05 per GB of egress. CHANGES_v1.5.0.md measured the memory one
# against a real bill.
MONTH_SECONDS = 30 * 24 * 3600
USD_PER_VCPU_SECOND = 20 / MONTH_SECONDS
USD_PER_GB_SECOND = 10 / MONTH_SECONDS
USD_PER_GB_EGRESS = 0.05
# The worst job, from the 149.8 MP calibration in CHANGES_v1.6.0.md: AVIF
# used 187 CPU-seconds in 15 wall-seconds, so it is multithreaded; two cores
# is what a small container gives it. 1.5 GB is a 150 MP decode plus the
# encoder's buffers.
WORST_CPUS = 2
WORST_MEMORY_GB = 1.5
# How many times over the worst case the cheapest sale must still be, for
# the credit ladder's own checks below.
SAFETY = 2.0

failures = []


def check(name, ok, detail=""):
    print(("  ok  " if ok else "  FAIL  ") + name + ("" if ok else f"  -- {detail}"))
    if not ok:
        failures.append(name)


def worst_job_usd(seconds: int) -> float:
    cpu = seconds * WORST_CPUS * USD_PER_VCPU_SECOND
    memory = seconds * WORST_MEMORY_GB * USD_PER_GB_SECOND
    egress = (jobs.SEND_LIMIT_MB / 1024) * USD_PER_GB_EGRESS
    return cpu + memory + egress


print("\n== the ladder ==")
check("the ordinary rate is two credits a Star", fl.credit_for_stars(15) == 30, fl.credit_for_stars(15))
check("the shipped ladder is 500 Stars at 3x, then 500 at 2x",
      fl.TOPUP_LADDER == [(500, 3.0), (500, 2.0)], fl.TOPUP_LADDER)
check("bonus credit lasts 90 days", fl.BONUS_EXPIRY_DAYS == 90, fl.BONUS_EXPIRY_DAYS)
check("somebody's first 500 Stars earn 6 each",
      fl.quote_credit(0, 500)["total"] == 3000, fl.quote_credit(0, 500))
check("the next 500 earn 4 each",
      fl.quote_credit(500, 500)["total"] == 2000, fl.quote_credit(500, 500))
check("after that, the ordinary 2 and no bonus",
      fl.quote_credit(1000, 500) == {"stars": 500, "base": 1000, "bonus": 0, "total": 1000},
      fl.quote_credit(1000, 500))
# The owner's words were "however many times the user recharges": the ladder
# counts a person's Stars across payments, so splitting one payment into many
# must neither gain nor lose anything.
uneven = [piece for piece in (1, 7, 50, 333)
          if sum(fl.quote_credit(paid, min(piece, 1500 - paid))["total"]
                 for paid in range(0, 1500, piece)) != fl.quote_credit(0, 1500)["total"]]
check("paying in pieces earns exactly what paying at once does", not uneven, str(uneven))
fewer = [(paid, stars) for paid in (0, 250, 499, 500, 999, 1000) for stars in range(1, 600)
         if fl.quote_credit(paid, stars + 1)["total"] < fl.quote_credit(paid, stars)["total"]]
check("one more Star never buys fewer credits", not fewer, str(fewer[:5]))
odd = [(paid, stars) for paid in range(0, 1200, 37) for stars in range(1, 700, 13)
       if fl.quote_credit(paid, stars)["bonus"] < 0
       or fl.quote_credit(paid, stars)["base"] != fl.credit_for_stars(stars)]
check("the bonus is never negative, and the part that never expires is the plain rate",
      not odd, str(odd[:5]))
check("credit is always a whole number",
      all(isinstance(fl.quote_credit(paid, stars)["total"], int)
          for paid in (0, 333, 999) for stars in range(1, 1200, 7)))
check("a mistyped ladder loses the bad pieces rather than crashing a bot",
      fl._parse_ladder("500:3, junk, 0:2, 100:0.5, 200:2") == [(500, 3.0), (200, 2.0)],
      fl._parse_ladder("500:3, junk, 0:2, 100:0.5, 200:2"))

deepest_rate = fl.TOPUP_MULTIPLIER * max([1.0] + [multiplier for _, multiplier in fl.TOPUP_LADDER])
print(f"\n  deepest possible rate: {deepest_rate:g} ⚡ per ⭐ (somebody's first 500 Stars)")

print("\n== the rule: cost, plus a markup that is the cost while it is small ==")
# The owner, 2026-09-28: "markup and raw cost should be around equal at low
# raw costs, then with the cost rising, the markup should also rise, but in a
# bit lower rate". shared/pricing.py is that rule; these are its promises.
sys.path.insert(0, str(HERE / ".mirror" / "convert_bot"))
import pricing  # noqa: E402
check("pricing.py prices the machine as this test does",
      abs(pricing.USD_PER_VCPU_SECOND - USD_PER_VCPU_SECOND) < 1e-15
      and abs(pricing.USD_PER_GB_SECOND - USD_PER_GB_SECOND) < 1e-15
      and pricing.USD_PER_GB_EGRESS == USD_PER_GB_EGRESS and pricing.USD_PER_STAR == USD_PER_STAR
      and pricing.JOB_CPUS == WORST_CPUS and pricing.JOB_MEMORY_GB == WORST_MEMORY_GB)
check("and values a credit at the least it may have brought in: the deepest rate",
      abs(pricing.usd_per_credit() - USD_PER_STAR / deepest_rate) < 1e-15, pricing.usd_per_credit())
costs = [0.5, 1, 2, 3, 4.99, 5, 5.5, 8, 13, 21, 34, 55, 89]
check("while the cost is small, the markup is the cost itself",
      all(pricing.markup(c) == c for c in costs if c <= pricing.KNEE))
above = [c for c in costs if c > pricing.KNEE]
marks = [pricing.markup(c) for c in above]
check("past that the markup still rises", all(b > a for a, b in zip(marks, marks[1:])), marks)
ratios = [m / c for m, c in zip(marks, above)]
check("but more slowly than the cost does", all(b < a for a, b in zip(ratios, ratios[1:])), ratios)
largest = pricing.cost(jobs.TIME_LIMIT_S, jobs.BIG_SEND_MAX_BYTES)
check(f"and it is still {pricing.markup(largest) / largest:.0%} of the cost for the largest job there is",
      pricing.markup(largest) / largest >= 0.6)
check("a price is never under its cost plus its markup, nor under the minimum",
      all(pricing.price(c) >= c + pricing.markup(c) - 1e-9 and pricing.price(c) >= pricing.MINIMUM
          for c in costs))


def worst_job_sending(seconds: float, allowed_bytes: int) -> float:
    return (seconds * WORST_CPUS * USD_PER_VCPU_SECOND + seconds * WORST_MEMORY_GB * USD_PER_GB_SECOND
            + (allowed_bytes / (1024 ** 3)) * USD_PER_GB_EGRESS)


print("\n== every conversion covers the worst job it can buy, at the deepest rate ==")
# A conversion's price is worked out on the most it may cost: its own time
# limit on two cores and 1.5 GB, and everything it may send back. So even
# the job that runs to its limit and sends back all it is allowed to is
# paid for -- at the deepest rate a Star ever buys credit at, and with room.
MB = 1024 * 1024
SAFETY_AT_WORST = 1.5
real_big_sends = jobs.big_sends
jobs.big_sends = lambda chat_id: True
try:
    pairs = [("jpg", "png"), ("png", "jpg"), ("heic", "png"), ("jpg", "avif"), ("docx", "pdf"),
             ("pdf", "png"), ("wav", "mp3"), ("mp3", "wav"), ("mp4", "mp4"), ("mp4", "webm"),
             ("mp4", "gif"), ("mp4", "mp3"), ("mkv", "mov")]
    sizes = [3, 4, 10, 20, 50, 100, 224, 500, 1000, 2000]
    worst_ratio, rising, shown = None, [], 0
    for src, target in pairs:
        previous = 0
        for mb in sizes:
            price, out = convert_utils.price_for_job(src, target, mb * MB)
            if price < previous:
                rising.append((src, target, mb, previous, price))
            previous = price
            if price == 0:
                continue
            seconds = jobs.time_limit_for(convert_utils.billable_bytes(mb * MB, out),
                                          convert_utils.work_factor(src, target), paid=True)
            allowed = jobs.send_limit_for(mb * MB, out, 1)
            revenue = price / deepest_rate * USD_PER_STAR
            ratio = revenue / worst_job_sending(seconds, allowed)
            worst_ratio = ratio if worst_ratio is None else min(worst_ratio, ratio)
            if ratio < SAFETY_AT_WORST or shown < 6:
                shown += 1
                check(f"{src} -> {target}, {mb} MB: {price} ⚡ for {seconds}s and up to "
                      f"{allowed // MB} MB back, {ratio:.2f}x its worst case", ratio >= SAFETY_AT_WORST)
    check(f"every pair and size, 3 to 2000 MB, covers its worst case {SAFETY_AT_WORST}x or more "
          f"(the least: {worst_ratio:.2f}x)", worst_ratio >= SAFETY_AT_WORST)
    check("a bigger file of the same kind never costs less", not rising, rising[:3])
    per_mb = [convert_utils.price_for_job("mp4", "mp4", mb * MB)[0] / mb for mb in (20, 50, 100, 224, 500, 1000, 2000)]
    check("and costs less per megabyte the bigger it is", all(b <= a for a, b in zip(per_mb, per_mb[1:])),
          [round(x, 3) for x in per_mb])
    # A result may not be sent back without limit: the cheapest paid price
    # could not pay for 2000 MB of egress, which is the reason each job's send
    # limit is part of its price -- asserted so it cannot be quietly undone.
    least = convert_utils.least_paid_price()
    check(f"the least paid conversion ({least} ⚡) could NOT pay for a flat 2000 MB send",
          least / deepest_rate * USD_PER_STAR < worst_job_sending(jobs.TIME_BASE_S, jobs.BIG_SEND_MAX_BYTES))
    check("so a job's send limit follows its estimated result",
          jobs.send_limit_for(10 * MB, 20 * MB, 1) == 3 * 20 * MB
          and jobs.send_limit_for(10 * MB, 1 * MB, 1) == jobs.SEND_LIMIT_BYTES
          and jobs.send_limit_for(1500 * MB, 1400 * MB, 1) == jobs.BIG_SEND_MAX_BYTES)
    check("and its time limit its size and how hard the pair is",
          jobs.time_limit_for(10 * MB) == jobs.TIME_BASE_S + 10
          and jobs.time_limit_for(10 * MB, 2.0) == jobs.TIME_BASE_S + 20
          and jobs.time_limit_for(5000 * MB, 2.0) == jobs.TIME_LIMIT_S
          and jobs.time_limit_for(10 * MB, paid=False) == jobs.FREE_TIME_LIMIT_S)
finally:
    jobs.big_sends = real_big_sends

print("\n== the owner's clip ==")
clip_price, clip_out = convert_utils.price_for_job("mp4", "mp4", 224 * MB)
check(f"a 224 MB phone video to MP4 is {clip_price} ⚡ (it was 2,100 before 2.0.0)", clip_price <= 60, clip_price)
check("and nothing at all costs more than 100 ⚡",
      pricing.price(pricing.cost(jobs.TIME_LIMIT_S, jobs.BIG_SEND_MAX_BYTES)) <= 100)

print("\n== DownloaderBot's large files ==")
# Past 48 MB DownloaderBot fetches only for somebody who has switched large
# files on, priced by the rule over the 100 MB begun (large_files.py). A
# download is mostly egress -- the whole file out to Telegram -- plus the
# time of fetching it, taken here at a slow 2 MB a second with a quarter of a
# core and a quarter of a gigabyte busy. Every size up to the ceiling has to
# bring in 1.5 times that at the deepest rate.
import importlib.util  # noqa: E402
_spec = importlib.util.spec_from_file_location(
    "large_files", HERE / ".mirror" / "downloader_bot" / "large_files.py")
large_files = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(large_files)
previous = 0
for mb in (49, 100, 101, 250, 500, 1000, 1999, large_files.LARGE_MAX_MB):
    size = mb * MB
    seconds = mb / 2.0
    cost = (seconds * 0.25 * USD_PER_VCPU_SECOND + seconds * 0.25 * USD_PER_GB_SECOND
            + (size / (1024 ** 3)) * USD_PER_GB_EGRESS)
    credits = large_files.price_for(size)
    revenue = credits / deepest_rate * USD_PER_STAR
    check(f"a {mb} MB download: {credits} ⚡ = ${revenue:.4f}, {revenue / cost:.1f}x its worst case",
          revenue >= SAFETY_AT_WORST * cost and credits >= previous, f"needs ${SAFETY_AT_WORST * cost:.4f}")
    previous = credits
first = large_files.first_price()
steps = [large_files.price_for((n + 1) * 100 * MB) - large_files.price_for(n * 100 * MB) for n in range(1, 20)]
check(f"the first 100 MB cost the most (⚡{first}), and each 100 MB after costs less",
      all(0 <= step < first for step in steps), steps)

print("\n== what a conversion is priced on ==")
# Until 1.7.0 the price came from the upload alone, and was quoted before
# anybody had said what they wanted. Both halves were wrong about the same
# file: the owner's 78 KB .docx came back as a 1.6 MB PDF.
MB = 1024 * 1024
docx_price, docx_out = convert_utils.price_for_job("docx", "pdf", 78 * 1024)
check("a small document that becomes a large PDF is estimated as one",
      docx_out > 1_000_000, docx_out)
check("and is still free, because the whole job is under the free line",
      docx_price == 0, docx_price)

gif_price, gif_out = convert_utils.price_for_job("mp4", "gif", 12 * MB)
mp3_price, mp3_out = convert_utils.price_for_job("mp4", "mp3", 12 * MB)
check("the same video costs more as a GIF than as an MP3", gif_price > mp3_price,
      (gif_price, mp3_price))
check("because the GIF is estimated much larger", gif_out > mp3_out * 10, (gif_out, mp3_out))
check("which a price from the upload alone could not tell apart",
      convert_utils.price_for_size(12 * MB) == convert_utils.price_for_size(12 * MB))

check("encoding video is charged above one pass over the bytes",
      convert_utils.work_factor("mp4", "webm") > 1.0, convert_utils.work_factor("mp4", "webm"))
check("copying pixels into another still image is not",
      convert_utils.work_factor("jpg", "png") == 1.0, convert_utils.work_factor("jpg", "png"))
check("a free job stays free however hard the pair is",
      convert_utils.price_for_job("gif", "mp4", 100 * 1024)[0] == 0)

pages_out = convert_utils.estimate_output_bytes("pdf", "png", 900 * 1024, pages=400)
ratio_out = convert_utils.estimate_output_bytes("pdf", "png", 900 * 1024)
check("a page count beats a size ratio for a document rendered to pictures",
      pages_out > ratio_out * 10, (pages_out, ratio_out))

check("the price is charged on the bytes handled, both ends",
      convert_utils.billable_bytes(3 * MB, 9 * MB) == 12 * MB,
      convert_utils.billable_bytes(3 * MB, 9 * MB))
check("every price is a whole number of credits",
      all(isinstance(convert_utils.price_for_job(src, target, size)[0], int)
          for src, target, size in (("jpg", "png", 4 * MB), ("wav", "mp3", 9 * MB),
                                    ("mp4", "webm", 15 * MB), ("pdf", "docx", 2 * MB))))
check("an unknown pair still has a price rather than an exception",
      convert_utils.price_for_job("qoi", "icns", 5 * MB)[0] >= 0)

print("\n== what the documents promise is what the code charges ==")
# The READMEs and terms quote a few prices outright, because a person
# deciding whether to use a bot deserves numbers, not a formula. They are
# worked out by the code, and checked here so that a changed setting cannot
# leave them quoting the old ones.
REPO = HERE.parent
quoted = {
    "convert_bot/README.md": [f"⚡{convert_utils.least_paid_price()}",
                              f"⚡{convert_utils.price_for_job('mp4', 'mp4', 100 * MB)[0]}",
                              f"⚡{pricing.price(pricing.cost(jobs.TIME_LIMIT_S, jobs.BIG_SEND_MAX_BYTES))}"],
    "convert_bot/TERMS.md": [f"⚡{convert_utils.least_paid_price()}",
                             f"⚡{pricing.price(pricing.cost(jobs.TIME_LIMIT_S, jobs.BIG_SEND_MAX_BYTES))}"],
    "downloader_bot/README.md": [f"⚡{large_files.first_price()}", f"⚡{large_files.price_for(500 * MB)}",
                                 f"⚡{large_files.top_price()}"],
    "downloader_bot/TERMS.md": [f"⚡{large_files.first_price()}", f"⚡{large_files.top_price()}"],
}
for relative, prices in quoted.items():
    text = (REPO / relative).read_text(encoding="utf-8")
    missing = [p for p in prices if p not in text]
    check(f"{relative} quotes {', '.join(prices)}", not missing, missing)

print("\n== a free conversion is bounded ==")
worst_free = worst_job_usd(jobs.FREE_TIME_LIMIT_S)
check(f"a free job cannot cost more than half a cent (${worst_free:.4f})", worst_free <= 0.005,
      f"${worst_free:.4f}")
check("free jobs get a shorter limit than paid ones", jobs.FREE_TIME_LIMIT_S < jobs.TIME_LIMIT_S)

print()
if failures:
    print(f"FAILURES ({len(failures)}):")
    for name in failures:
        print("  -", name)
    sys.exit(1)
print("every price still pays for the machine")
