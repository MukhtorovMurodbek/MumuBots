#!/usr/bin/env python3
"""Run every check in this folder.

    python3 tests/run.py

No dependencies and no network: python-telegram-bot, psycopg, Pillow and
httpx are all stubbed in tests/stubs, and nothing here opens a socket or a
database connection. It runs on the Python you have -- including 3.9, which
is why mirror39.py exists.

What is covered is the machinery added for zero-downtime deploys, evolving
messages and the /ping report; the bots' own logic is not, and this is not
trying to be a full test suite.
"""
import os
import pathlib
import shutil
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent
REPO = ROOT.parent
BOTS = ["sticker_bot", "convert_bot", "downloader_bot", "anon_bot", "manager_bot"]
# ManagerBot is private, has one user, and has no /privacy to render.
PUBLIC_BOTS = [bot for bot in BOTS if bot != "manager_bot"]

sys.path.insert(0, str(ROOT))
from mirror39 import mirror


def main() -> int:
    mirrors = ROOT / ".mirror"
    if mirrors.exists():
        shutil.rmtree(mirrors)
    for name in BOTS:
        mirror(REPO / name, mirrors / name)

    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1", PYTHONIOENCODING="utf-8")
    runs = [
        ("evolving messages", ["test_live_message.py"]),
        ("state across restarts", ["test_lifecycle.py"]),
        ("the /ping report", ["test_ping.py"]),
        ("/backup is restorable, and stays pinned", ["test_backup.py"]),
        ("a transcript is read once and kept nowhere", ["test_transcript.py"]),
        ("an export copies without reading, or reads once", ["test_export.py"]),
        ("problems are levelled, alerted once, reported nightly", ["test_reporting.py"]),
        ("one Conflict is a warning; three in two minutes is a second copy, said once", ["test_conflict_alert.py"]),
        ("reactions travel, and feedback reaches only the owner", ["test_reactions_feedback.py"]),
        ("every problem has a code that decodes", ["test_problems.py"]),
        ("the log commands read the right log, with or without files", ["test_logs.py"]),
        ("announced updates", ["test_maintenance.py"]),
        ("every bot honours /pause", ["test_pause_gate.py"]),
        ("names line up across every layer", ["test_naming.py"]),
        ("startup jobs actually run", ["test_startup_jobs.py"]),
        ("the command menus", ["test_menu.py"]),
        ("every menu command is in its README", ["test_readmes.py"]),
        ("no SQL is built from user input", ["test_sql_safety.py"]),
        ("the logos survive a circular crop", ["test_logos.py"]),
        ("the shared modules live once, and nothing shadows them", ["test_shared_copies.py"]),
        ("settings: the environment, the bot's own .env, then shared/.env", ["test_envfiles.py"]),
        ("everything ConvertBot advertises, it can produce", ["test_conversions.py"]),
        ("a file built to hurt is refused, not run", ["test_hostile_files.py"]),
        ("a photo comes out the way up it was taken", ["test_orientation.py"]),
        ("an album is one conversion", ["test_batching.py"]),
        ("every discount still pays for the machine", ["test_pricing.py"]),
        ("a conversion ends right, and so does its credit", ["test_jobs.py"]),
        ("files past the Bot API's limits, and the bot without them", ["test_big_files.py"]),
        ("the status card a conversion keeps up to date", ["test_card.py"]),
        ("TestBot as any of the five", ["test_switch.py"]),
        ("a release goes to the right repository, and never leaves the bots paused", ["test_release_push.py"]),
        ("no database call waits for ever", ["test_db_timeouts.py"]),
        ("CI installs bsdtar, so the archive tests do not skip there", ["test_ci_workflow.py"]),
        ("the move to MumuBots sends what Railway accepts, and fits", ["test_railway_move.py"]),
        ("the old versions go to GitHub without a secret", ["test_build_history.py"]),
        ("the idle and alarm arithmetic", ["test_usage.py"]),
        ("uncompressed downloads", ["test_lossless.py"]),
        ("downloads over 48 MB: only with a yes, for credit", ["test_large_files.py"]),
        ("downloads keep their audio", ["test_formats.py"]),
        ("a refusal reads as a sentence", ["test_refusals.py"]),
        ("the download chain survives its providers", ["test_resolvers.py"]),
        ("the daily route check, replacing yt-dlp, the page cap, cobalt", ["test_routewatch.py"]),
        ("a body is read under a ceiling, however it is encoded; cookies; the check in time", ["test_bounded_bodies.py"]),
        ("a pinterest video pin is a video", ["test_pinterest.py"]),
        ("downloads fetch the public internet, and nothing else", ["test_public_only.py"]),
        ("a download nobody waits for stops and cleans up", ["test_ytdlp_stop.py"]),
        ("StickerBot's ffmpeg is capped on the decoder too", ["test_sticker_ffmpeg.py"]),
        ("a video too big for the server is brought down, and only then", ["test_video_fit.py"]),
        ("a payment delivered twice is credited once, by every caller", ["test_payment_once.py"]),
        ("the one-time gift: counted first, given once, told only to who got it", ["test_gift.py"]),
        ("ManagerBot's help per command: a page each, owner-only, every number read from the code", ["test_manager_help.py"]),
        ("the 220 gift's rules: the window, the claim, the notices, started once, stopped early", ["test_promo.py"]),
        ("the 220 gift in ManagerBot: the count, the one notice, /promo, the card that starts it once", ["test_promo_manager.py"]),
        ("archives, both ways, and a hostile one", ["test_archives.py"]),
        ("camera raw photos, developed and sized for a small machine", ["test_raw.py"]),
        ("text read from pictures and scanned PDFs, on the machine", ["test_ocr.py"]),
        ("reading text is offered, priced, timed and run as sold", ["test_ocr_wiring.py"]),
        ("an album is one PDF or one ZIP, and nothing is kept", ["test_merge.py"]),
        ("a sweep never holds the tick that starts it", ["test_pump_tasks.py"]),
        ("a video arrives in its real shape, not as a square", ["test_video_shape.py"]),
        ("the last download is kept five minutes, for the person it was for", ["test_delivery_cache.py"]),
        ("one post is one link, however it was shared", ["test_canonical_links.py"]),
        ("the same link twice while the first is going is one job", ["test_link_dedupe.py"]),
        ("a post sent before is sent again by its file ids, and the table knows nobody", ["test_sent_files.py"]),
        ("button taps are counted, and the counts say nothing about who", ["test_button_taps.py"]),
        ("/buttons reports the counts, to the owner only", ["test_button_taps.py", "manager"]),
    ]
    runs += [(f"{bot}: the policy commands read as prose", ["test_policies.py", bot])
             for bot in PUBLIC_BOTS]
    runs += [(f"{bot}: untrusted filenames cannot become paths", ["test_untrusted_names.py", bot])
             for bot in ("convert_bot", "downloader_bot")]
    runs += [(f"{bot}: every module imports", ["test_imports.py", bot]) for bot in BOTS]
    runs += [(f"{bot}: main() wiring", ["test_wiring.py", bot]) for bot in BOTS]
    runs += [(f"{bot}: main() wiring, with state", ["test_wiring.py", bot, "--with-state"])
             for bot in BOTS]

    failed = []
    for title, args in runs:
        print(f"\n=== {title} ===", flush=True)
        result = subprocess.run([sys.executable, str(ROOT / args[0])] + args[1:],
                                cwd=ROOT, env=env)
        if result.returncode != 0:
            failed.append(title)

    shutil.rmtree(mirrors, ignore_errors=True)
    print()
    if failed:
        print(f"FAILED ({len(failed)}):")
        for title in failed:
            print("  -", title)
        return 1
    print(f"All {len(runs)} checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
