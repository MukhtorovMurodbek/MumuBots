"""DownloaderBot's /lossless switch, with Telegram and the network stubbed.

Sending a download as a photo or a video is what makes it play inline, and
it is also what makes Telegram re-encode it. Sending it as a document keeps
the bytes intact. /lossless is which of the two a given user gets, and the
thing worth testing is not the toggle -- it is that the switch is honoured
on *every* path out of this bot, since a download that silently keeps
arriving compressed from one platform out of five is exactly the bug this
would ship with.

So each path is driven for real, with only the fetch and the send faked:

  - Instagram/TikTok/YouTube video (the yt-dlp path)
  - a Pinterest image
  - a Reddit image, and a Reddit text post's rendered card
  - a single Twitter photo, several Twitter photos (a media group), and a
    tweet's rendered card

Run from the repo root:  python tests/lossless.py
"""
import os
import subprocess
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent
DSN = os.environ.get("TEST_DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/botfamily_test")

CHILD = r'''
import asyncio, os, sys
from types import SimpleNamespace
sys.path.insert(0, os.getcwd())
sys.path.insert(1, os.path.join(os.path.dirname(os.getcwd()), "shared"))

import db, i18n, bot, net, platforms, resolvers

UID = 424244
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
JPG = b"\xff\xd8\xff\xe0" + b"\x00" * 64

db.init_db()

failures = []


def check(label, cond):
    print(("  ok   " if cond else "  FAIL ") + label)
    if not cond:
        failures.append(label)


class Msg:
    def __init__(self):
        self.message_id, self.chat_id = 10, UID
        self.text = "https://example.invalid/x"
        self.caption, self.reply_to_message = None, None
        self.sent = []          # (how, filename) for every send this update made

    async def reply_text(self, text, **kw):
        return Status(self)

    async def reply_photo(self, data, **kw):
        self.sent.append(("photo", None))
        return SimpleNamespace(message_id=11)

    async def reply_video(self, data, **kw):
        self.sent.append(("video", None))
        return SimpleNamespace(message_id=11)

    async def reply_document(self, document=None, filename=None, **kw):
        self.sent.append(("document", filename or getattr(kw.get("caption"), "name", None)))
        return SimpleNamespace(message_id=11)

    async def reply_media_group(self, media, **kw):
        self.sent.append(("group:" + type(media[0]).__name__, None))
        return [SimpleNamespace(message_id=11)]


class Status:
    """The "Downloading..." message: edited, then deleted.

    Carries chat_id/message_id because LiveMessage.reply_to() takes a handle
    on whatever reply_text returned, and reads both off it."""
    def __init__(self, parent):
        self.parent = parent
        self.chat_id = getattr(parent, "chat_id", 1)
        self.message_id = 1

    async def edit_text(self, *a, **k):
        return self

    async def delete(self):
        return True


class Ctx:
    def __init__(self):
        self.user_data, self.chat_data, self.bot_data = {}, {}, {}
        self.args = []
        self.bot = SimpleNamespace()


def make_update():
    msg = Msg()
    return SimpleNamespace(
        message=msg, effective_message=msg, callback_query=None,
        effective_user=SimpleNamespace(id=UID, username="tester", full_name="Tester"),
        effective_chat=SimpleNamespace(id=UID),
    )


# ---- the fetches and the download, faked; everything else is the real code

async def fake_fetch_bytes(url):
    return JPG


async def fake_pinterest(url):
    return JPG


async def fake_tweet(url):
    return {
        "user": {"screen_name": "someone", "name": "Someone"},
        "text": "a tweet with no media at all",
        "favorite_count": 3,
        "photos": FAKE_PHOTOS,
        "video": None,
    }


async def fake_reddit(url):
    return REDDIT_POST


# The video and Pinterest paths do not fetch anything themselves any more --
# since v1.2.1 they ask resolvers.py for a route and send back whatever it
# hands over. So they are faked at that seam instead: `resolve` says what the
# post turned out to be, `download` puts a plausible file on disk, and
# everything between them -- the caption, the lossless switch, the choice of
# send call -- is the real code, which is the part this test is about.
#
# Until v1.2.2R this file still called bot._download_and_send_video and
# bot._handle_pinterest, neither of which had existed since that rewrite, so
# the run died on the first case and the five that still worked were never
# reached. A test that names a function nobody has can only fail, and this
# one had been failing quietly for a version.

RESOLVED = None      # what the provider chain is pretending to have found


async def fake_resolve(platform, url):
    if RESOLVED == "nothing":
        raise resolvers.NothingWorked(
            platform, {"test": resolvers.ProviderFailed("no media here", "missing")})
    count = 3 if RESOLVED == "album" else 1
    kind = "video" if platform in ("instagram", "tiktok", "youtube") else "photo"
    return resolvers.Resolved(
        platform=platform, provider="test",
        items=[resolvers.MediaItem(
            kind=kind, url="https://example.invalid/f%d" % n,
            filename=("downloaded_clip.mp4" if kind == "video" else "image%d.jpg" % n))
            for n in range(count)],
    )


async def fake_resolver_download(item, work_dir=None):
    path = os.path.join(
        os.environ.get("TEMP", "."),
        "resolved" + (".mp4" if item.kind == "video" else ".jpg"))
    with open(path, "wb") as fh:
        fh.write(b"\x00" * 32 if item.kind == "video" else JPG)
    return path


platforms.fetch_bytes = fake_fetch_bytes
platforms.fetch_pinterest_image = fake_pinterest
platforms.fetch_tweet_syndication = fake_tweet
platforms.fetch_reddit_post = fake_reddit
net.fetch_bytes = fake_fetch_bytes
bot.platforms = platforms
bot.net = net
resolvers.resolve = fake_resolve
resolvers.download = fake_resolver_download

FAKE_PHOTOS = []
REDDIT_POST = {}


async def drive(name, lossless):
    """Run one path end to end and report how it sent what it produced."""
    global FAKE_PHOTOS, REDDIT_POST, RESOLVED
    db.set_lossless_enabled(UID, lossless)
    update, ctx = make_update(), Ctx()
    RESOLVED = None

    if name == "video":
        await bot._resolve_and_send(update, ctx, "instagram", "https://example.invalid/v")
    elif name == "pinterest":
        await bot._resolve_and_send(update, ctx, "pinterest", "https://pin.example.invalid/p")
    elif name == "reddit_image":
        REDDIT_POST = {"post_hint": "image", "url": "https://i.example.invalid/a.jpg",
                       "is_video": False, "title": "t", "subreddit": "s", "author": "a"}
        await bot._handle_reddit(update, ctx, "https://reddit.example.invalid/r")
    elif name == "reddit_card":
        REDDIT_POST = {"title": "a text post", "selftext": "with a body",
                       "subreddit": "s", "author": "a", "score": 7}
        await bot._handle_reddit(update, ctx, "https://reddit.example.invalid/r")
    elif name == "twitter_photo":
        await bot._handle_twitter(update, ctx, "https://x.example.invalid/t")
    elif name == "twitter_album":
        # Several files from one post is now _send_media's media-group
        # branch, reached through the provider chain like everything else --
        # the hand-rolled Twitter album path this used to drive is gone.
        RESOLVED = "album"
        await bot._handle_twitter(update, ctx, "https://x.example.invalid/t")
    elif name == "twitter_card":
        # Nothing to download is what sends a tweet to the card renderer, and
        # it is the chain that decides that now. Faking empty `photos` no
        # longer reaches it: the chain answers first and would have sent a
        # photo, which is how this case passed for a version without ever
        # rendering a card.
        RESOLVED = "nothing"
        FAKE_PHOTOS = []
        await bot._handle_twitter(update, ctx, "https://x.example.invalid/t")
    return update.message.sent


COMPRESSED = {
    "video": "video",
    "pinterest": "photo",
    "reddit_image": "photo",
    "reddit_card": "photo",
    "twitter_photo": "photo",
    "twitter_album": "group:InputMediaPhoto",
    "twitter_card": "photo",
}
LOSSLESS = {
    "video": "document",
    "pinterest": "document",
    "reddit_image": "document",
    "reddit_card": "document",
    "twitter_photo": "document",
    "twitter_album": "group:InputMediaDocument",
    "twitter_card": "document",
}


async def run():
    check("lossless is off until asked for", db.get_lossless_enabled(UID) is False)

    for name in COMPRESSED:
        sent = await drive(name, False)
        check("[off] %s -> %s" % (name, COMPRESSED[name]),
              len(sent) == 1 and sent[0][0] == COMPRESSED[name])

    for name in LOSSLESS:
        sent = await drive(name, True)
        ok = len(sent) == 1 and sent[0][0] == LOSSLESS[name]
        check("[on]  %s -> %s" % (name, LOSSLESS[name]), ok)
        if ok and sent[0][1]:
            check("[on]  %s is named by what it is" % name,
                  sent[0][1].rsplit(".", 1)[-1] in ("jpg", "png", "mp4"))

    # The filename is the only thing a document shows for itself, and the
    # bytes are the only honest source for it.
    check("PNG bytes are named .png", bot._image_extension(PNG) == "png")
    check("JPEG bytes are named .jpg", bot._image_extension(JPG) == "jpg")
    check("unknown bytes do not claim a type", bot._image_extension(b"nope") == "bin")

    check("the toggle round-trips", (
        db.set_lossless_enabled(UID, True) or db.get_lossless_enabled(UID)) is True)
    db.set_lossless_enabled(UID, False)
    check("and back", db.get_lossless_enabled(UID) is False)


try:
    asyncio.run(run())
finally:
    with db.pooled() as conn:
        conn.execute("DELETE FROM settings WHERE user_id = %s", (UID,))
        conn.commit()
    db.close_pool()
sys.exit(1 if failures else 0)
'''


def main() -> int:
    env = dict(os.environ)
    env.update({
        "DATABASE_URL": DSN, "DB_SCHEMA": "downloader_bot", "PYTHONIOENCODING": "utf-8",
        "DBOT_TOKEN": "1:AA", "DBOT_USERNAME": "x",
        "SIBLING_BOTS": "convertbot:ConvertBot:b",
    })
    proc = subprocess.run(
        [sys.executable, "-c", CHILD], cwd=ROOT / "downloader_bot", env=env,
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    print("== downloader_bot: /lossless ==")
    if proc.stdout.strip():
        print(proc.stdout.rstrip())
    if proc.returncode != 0 and proc.stderr.strip():
        print(proc.stderr.rstrip()[-2500:])
    print("\nFAILURES:", 1 if proc.returncode else 0)
    return 1 if proc.returncode else 0


if __name__ == "__main__":
    raise SystemExit(main())
