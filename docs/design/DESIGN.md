# Design

What the five bots look like, and what is left to do about it.

A Telegram bot is four surfaces and nothing else: a **34-pixel circle**, a
**profile card** somebody reads before they press Start, a **slash menu**, and
**text in a chat bubble**. There is no CSS, no typeface, no colour in a
message and no layout. Everything below is about those four.

`ARCHITECTURE.md` covers how the bots are built, `DEPLOY.md` how they ship.
This file is the one that says why the amber is amber-600 and not amber-500,
and it was internal until 2.0.0, when the whole repository became MumuBots.

---

## Where things stand

| surface | state |
|---|---|
| Logo (the circle) | **Done**, and uploaded to all five (2026-09-09). Five marks in `scripts/make_logos.py`, tested by `tests/test_logos.py`. |
| Short description / description | **Done** for all five as of this version — the four public ones in three languages, ManagerBot in English. |
| Slash menu | **Done** — both scopes, and all three languages as of v1.6.0. |
| Description picture | **Done** as of v1.6.0: `hero.png` per bot, generated from the same geometry as the avatar. Needs the one manual upload. |
| Message typography | **Mostly done.** The helpers exist and the long screens use them; a few short ones do not. |

---

## 1. The circle

Five marks, one system: a flat ground with a white glyph inside the middle
70%, built from thick strokes. Declared once as geometry and rendered to both
SVG and PNG, because an SVG diffs and a PNG is what Telegram accepts.

| bot | ground | the glyph |
|---|---|---|
| StickerBot | `#D97706` amber-600 | a square with one corner turned back |
| ConvertBot | `#7C3AED` violet-600 | two arrows passing each other |
| DownloaderBot | `#0D9488` teal-600 | an arrow arriving on a line |
| AnonBot | `#475569` slate-600 | a speech bubble with nobody in it |
| ManagerBot | `#1F2937` gray-800, amber centre | one in the middle, four around it |

ManagerBot is the only one whose glyph is not all white. The amber centre is
deliberate: it is the bot that is not like the others, and the colour it
borrows is StickerBot's, the oldest of them.

**This does not need redesigning.** It needs uploading. *Edit Botpic* is not
on the Bot API — no method sets a bot's own photo — so it is five minutes in
BotFather, once — **done on 2026-09-09**, after being deferred across two
versions. The step, for whenever a mark changes:

> `@BotFather` → `/mybots` → the bot → *Edit Bot* → *Edit Botpic* → send
> `<bot folder>/logo.png`.

Regenerate with `python scripts/make_logos.py` if anything is touched; the
test fails if the committed files stop matching the generator.

## 2. The description picture

Above the Start button, an empty chat can show a **picture** as well as the
description text. It is the largest piece of screen a stranger sees before
deciding whether to press Start, and it was blank on all five.

`scripts/make_logos.py` now writes `hero.svg` and `hero.png` beside the logo:
640×360, the same ground colour, the same glyph. No new artwork, because the
mark is the thing a person is being asked to recognise and a second design per
bot would be two things to keep in step instead of one. The renderers had to
stop assuming a square canvas to do it, which turned up a real bug — the `ring`
branch of `to_png` unpacks a stroke width into a local called `width`, and a
frame dimension by the same name was silently overwritten by it.

**No text on it.** The description sits directly underneath in the reader's own
language, and the family is trilingual; a picture repeating it in English would
be worse than one that only says which bot this is. It would also need a font,
which the file's own rule forbids.

**And the glyph is not cropped**, which is where this ended up disagreeing with
the plan that proposed it. "Oversized and bled off one edge" is the ordinary
way to make a banner feel confident and it is wrong for these five: two of the
marks are made of separated parts — ManagerBot's ring of four dots, ConvertBot's
two passing arrows — and an edge through either stops it being that mark rather
than cropping it. A rule that flatters a square and destroys a constellation is
not a rule. So the glyph is whole, about seventy per cent of the frame height,
set right of centre, with the empty ground on the left doing the composition.

`tests/test_logos.py` checks all three of those — nothing touching an edge, the
glyph carrying the frame, the ground to its left — plus its own feature floor.
The avatar's `MIN_FEATURE` is a rule about surviving 34 pixels in a chat list
and does not apply here; the card is drawn at roughly the width of the chat, so
it has a floor of its own.

Uploading is still a manual BotFather step, like the avatar: *Edit Bot* →
*Edit Description Picture*. `DEPLOY.md` §3 in the BotFather section.

## 3. Fonts — the one idea here worth arguing with

There is no way to ship a typeface in a Telegram message, so "interesting
fonts" in practice means the Unicode look-alike alphabets: 𝐛𝐨𝐥𝐝, 𝓈𝒸𝓇𝒾𝓅𝓉,
ᴄᴀᴘs. **These should not go anywhere near these bots**, for four reasons and
the last one is decisive:

1. **They are not text.** They are Mathematical Alphanumeric Symbols. A screen
   reader says "mathematical bold small a" or nothing at all. AnonBot exists so
   that people can write to each other; a chrome around that which a blind user
   cannot hear is not a style choice.
2. **Search stops working.** Telegram's in-chat search and the phone's own
   copy-paste match characters. Somebody scrolling back for the word
   *download* will not find 𝖽𝗈𝗐𝗇𝗅𝗈𝖺𝖽.
3. **Rendering is a lottery.** These live outside the basic multilingual plane;
   older Android builds and most desktop fallback fonts draw a box.
4. **Two thirds of the users would get nothing.** The alphabets are Latin. The
   family is trilingual, and there is no bold-script Unicode block for
   Cyrillic and none for the Uzbek Latin diacritics. So a styled interface
   would be styled in English and plain in Russian and Uzbek — a bot that
   visibly cares less about the languages most of its users read.

**What to use instead is the real markup, which Telegram has and the family is
already using half of.** `shared_features.py` has `heading`, `body`,
`lead_in`, `collapsed`, `command_list` and `joined`; the rules they encode are
that a long block goes behind an expandable blockquote, and that a `/command`
belongs in a monospace run because it is a thing to type rather than a word in
a sentence. Everything interpolated is escaped, which is load-bearing — a
stray `<` in a translated string takes the whole message down.

Three parts of the palette are unused, and on inspection none of them is worth
having. This list proposed two of them and both were wrong:

- **Spoiler** (`<tg-spoiler>`) was proposed for AnonBot showing a conversation
  excerpt in `/conversations`. **There is no excerpt, and there cannot be one.**
  AnonBot stores no message contents — that is a published claim in its
  `PRIVACY.md` and the reason a "send it anyway" button was removed in v1.4.0 —
  so `/conversations` lists numbers and timestamps as buttons and holds no text
  to hide. Nothing else in the family displays content a spoiler would suit.
  Building one would have meant storing the thing the bot promises not to.
- **Inline links as the label**, rather than the address itself: no.
  `_policy_footer()` renders `PRIVACY_URL` and `TERMS_URL` as their own anchor
  text on purpose, because a privacy notice is the wrong place to hide where a
  link goes behind a word. That reasoning was already in the code and this list
  had not read it.
- **Underline and strikethrough**: no. Underline reads as a broken link and
  strikethrough has nothing to say here.

Two proposals out of three withdrawn on contact with the code is worth noticing
about this file: a design list written from the outside is a list of guesses
until somebody checks each one against what the bots actually do.

## 4. The slash menu

Shipped in this version, and it is the part that was actually broken.

Every bot handles commands it never offered. The owner-only ones — `/status`,
`/dbdump`, `/messageas` and each bot's own — were kept out of the menu on the
sound reasoning that there is no point advertising to a stranger a command
they cannot run. The unsound part was that this hid them from the one person
who *can*: the owner types `/status` into their own bot from memory, or does
not, and a command nobody remembers is a command that stops existing.

Telegram has scopes and the family was not using them. Now: the public list at
the default scope, and the public list plus the owner-only ones published into
each admin's own chat with `BotCommandScopeChat`. **A scope decides what is
offered, never what is allowed** — every guard is untouched, and the menu is
not a permission.

ManagerBot is the same rule upside down. It is private, so the whole list is
the owner's, and what goes up at the default scope is `/start` alone. A
stranger who finds it was previously handed a menu reading `/sql`, `/backup`,
`/restart` — all of which refuse them, but a list of the levers is an
invitation with no upside.

`tests/test_menu.py` now checks both directions and covers ManagerBot, which it
never did. That gap is how `/probe`, `/providers`, `/stars` and `/crashtest`
went several versions handled and unlisted.

### And in three languages, as of v1.6.0

The bots were trilingual everywhere except the one list a user reads before
they have understood anything. `set_my_commands` takes a `language_code` the
same way `publish_profile()` already sends the description three times, so the
menu now goes up four times per bot: untagged, `en`, `uz`, `ru`.

English stays in each bot's `BOT_COMMANDS`, where the menu can be read by
reading `bot.py`. Uzbek and Russian live in `i18n.COMMAND_MENU`, keyed by
command name — 48 commands across the four bots, 96 strings. A command with no
translation keeps its English description rather than disappearing from that
language's menu, which is the failure worth designing against: a
half-translated menu is a menu with commands missing, and a missing command
reads as a bot that cannot do the thing.

**Four commands are deliberately not translated**, and they are the four that
look most like they should be. `/en`, `/uz` and `/rus` are each described in
the language they switch *to*, and `/language` is written in all three at
once. They are the way back for somebody who chose the wrong language, so they
have to be legible whatever the menu is currently in — translating them would
give a Russian client three lines of Russian, one of which is the only exit.

The owner's scoped menu stays untagged English. All admin output in this family
is English by policy, and the owner is the one reader whose language is not in
question.

`tests/test_menu.py` covers the new half the same way it covers the old one: a
command in `BOT_COMMANDS` with no `uz` or `ru` entry fails, an entry naming a
command that no longer exists fails, a translation appearing for one of the
four self-describing commands fails, and both lists are checked against what
Telegram will actually accept — names of `[a-z0-9_]{1,32}`, descriptions under
256 characters. A rejected list is a `set_my_commands` that fails at startup
and a menu that silently stays on the previous version.

## 5. Names and profile text

`publish_profile()` sets the short description and the description at startup
in all three languages, plus the English text a second time untagged, which is
what Telegram falls back to for a client in a fourth language. ManagerBot was
skipped on the reasoning that a private bot has no audience for them — which
is backwards. The owner is the one person who never reads them; the stranger
who has just found a bot called *mumu manager* is exactly who they are for. It
has both now, in English, built from `USERNAME_ALIASES` so a rename carries.

**The private bot was renamed rather than its username** (v1.6.0). It was
ParentBot at `@mumu_manager_bot`, and the plan here had been to move the
username to `@mumu_parent_bot` — five free minutes in BotFather, since nobody
holds a link to a private bot. Flipping it was the better trade even so: a
username is the one layer with anything depending on it, six layers were free,
and the family now has one naming exception instead of two. The cost was a
schema rename and a column of row updates in the shared database, which is
`scripts/rename_to_managerbot.py` and `DEPLOY.md` §8.

One name is still open: the GitHub repository is called `ParentBot` and should
be `manager-bot`, because a dashed name is what this family uses to mean
private. It is not one command — it has to be paired with confirming Railway
still builds from the renamed repository — so it waits for a morning.

`@mumu_chat_bot` is frozen. Every inbox link AnonBot has ever handed out is
built from it, and that is the exception that stays.

---

## The order to do them in

Everything on it is now done or withdrawn.

1. ~~**Upload the five logos.**~~ **Done** (2026-09-09). All five bots carry
   their mark instead of a grey circle with a letter in it.
2. ~~**Trilingual command menus.**~~ **Done** (v1.6.0). 96 strings, §4.
3. ~~**Description pictures.**~~ **Done** (v1.6.0). `hero.png` per bot, §2 —
   with the one manual BotFather upload still to do.
4. ~~**The spoiler.**~~ **Withdrawn**, §3. It was proposed for AnonBot's
   `/conversations` excerpts, and there are no excerpts: the bot stores no
   message contents, which is a published claim rather than an oversight.
5. ~~**The username rename.**~~ **Flipped and done** (v1.6.0) — the bot was
   renamed to ManagerBot instead, §5. What is left is the GitHub repository,
   which is an operations job rather than a design one.

**Also withdrawn:** *"link labels instead of bare URLs"*. `_policy_footer()`
had already made that decision the other way, deliberately, and its reason is
better than the one for changing it — *a privacy notice is the wrong place to
hide where a link goes behind a word.* The address stays the label.

## What is actually left

Two things, and neither is a design decision:

- **Upload the five `hero.png` cards** in BotFather. `DEPLOY.md` says where.
  The same five minutes the avatars took.
- **The `ParentBot` GitHub repository → `manager-bot`**, paired with a Railway
  check. §5.

And one thing that is not on any list yet, noted so it does not get invented
twice: the four surfaces are now all designed, so the next real question is
whether any of them is *wrong* rather than missing — which is a question for
somebody using the bots, not for this file.

Not planned, and listed so the reasons survive: a Web App menu button (nothing
here is a web app), custom emoji (Premium-gated, and the fallback is a box), a
family sticker pack (fun, unrelated to anything a user needs), and animated
avatars (not settable over the API, and the mark reads better still).
