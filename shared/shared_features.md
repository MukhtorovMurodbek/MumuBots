# `shared_features.py`

[↑ shared](README.md) · [code](shared_features.py)

```text
Small self-contained features used by this bot -- kept in one file for
convenience. It is copied byte-identically into every bot rather than
imported across folders, because each bot is its own repo and its own
deployment -- the same arrangement family_link.py uses. Nothing here
touches another bot's data: the cross-bot machinery all lives in
family_link.py. Things that live here:
  1. Sibling-bot cross-promotion text/buttons, for /start and /help --
     purely cosmetic (one env var), no database involved. Plus the one-tap
     language picker /start shows, which every bot renders the same way.
  2. A throttled, non-annoying donation reminder + a self-serve /donate
     command paid in Telegram Stars (no external payment processor needed).
  3. /privacy, /terms and /deletemydata: what this bot keeps on somebody,
     who else gets to see it, and the command that erases it again.
  4. The shared half of /cancel: the states this file can leave a user
     waiting in, the release of Telegram's client-side reply lock, and the
     one report format every bot's /cancel answers in.
  5. Logging setup, unhandled-exception tracking, active-user tracking, and
     hosting-environment detection, all in support of each bot's operator-only
     /status command.
  6. attach_maintenance()/flush_on_shutdown(): the jobs that keep a
     long-running process cheap -- writing buffered activity counts out in
     batches instead of a row per update, and dropping the cached per-user
     state of people who stopped using the bot months ago.
  7. attach_flood_gate(): a ceiling on what one person can make the bot do
     per minute, applied before any handler runs.
  8. /feedback: a suggestion or a report about anything, for whoever runs
     the bots -- the words and nothing about who wrote them.
```

## Sibling-bot cross-promotion

```text
SIBLING_BOTS format (one env var, same value given to every bot in the
family): "id:Display Name:username,id:Display Name:username"
  e.g. "stickerbot:StickerBot:MyStickerBot,convertbot:ConvertBot:MyConvertBot"
Each bot filters *itself* out of the list using its own BOT_NAME constant.
This is cosmetic only -- purely a shared env var, not a shared database.
```

### `_convert_bot_name()`

ConvertBot as somebody can tap it -- its @username from SIBLING_BOTS --
or just its name when this deployment does not list it.

### `sibling_bots_blurb()`

One short pointer line, not a repeat of the sibling list itself --
sibling_bots_keyboard_row() below already renders that list as buttons,
so spelling it out again in text too was just duplicate noise.

## The language picker

The trilingual greeting used to end with "/en — English, /uz — O'zbekcha,
/rus — Русский" and wait for the user to type one. Asking someone to type a
command before they have been told what the bot does is the worst possible
first impression, and on a phone it is three taps and a keyboard. One row of
buttons is one tap, and the /en /uz /rus commands still work for anyone who
has learned them.

This is what a brand-new user's first /start shows, and what /language
shows on demand at any point after that -- see start() and
language_command() in each bot. Passing `current` ticks the language
already in force, so a returning user can see at a glance which one they
are on before deciding to change it.

## /help in two lengths, the same in every public bot

2026-10-03, of AnonBot's: the help message was already too long, so there is a brief
one and another with the full information. And of the other three, the same day: all
bots are alike, or share one template.

So /help and /start show each bot's "help_brief" -- what it does in a few
bullets, then the same bullet about /feedback in every bot, its main
commands, and where the rest is -- with one button to the full guide,
"help_text", which tests/test_menu.py holds to naming every command a user
can send. The button turns the message into the other version in place
(while it is still the last thing in the chat; see live_message.py), and
/help full goes straight to the long one. A bot keeps its own rows of
buttons under either version.

### `wants_full_help()`

/help full -- or all, or more.

### `help_keyboard()`

The bot's page buttons (when it has pages), then the button to the other
version, then the bot's own rows -- the items first and "everything" last,
as under /ping.

### `help_toggle()`

The toggle's handler, for one bot: `build_text(lang, full=...)` is the
bot's own build_help_text, and `rows(lang)` the button rows it keeps
under its help, if any. `pages` and `labels` are the page buttons (see
help_page_buttons); they are rebuilt in both directions, so the buttons
survive the toggle. Register it with pattern=HELP_TOGGLE_PATTERN.

## A page for each command

2026-10-17: each command that is complex or has options gets a
page of its own, "a detailed explanation and one simple example", and every
price and limit on it is worked out from the code that charges or limits,
never typed in.

The help message carries one button per page, two to a row, above the
"Full guide" button -- the items first and "everything" last, as under
ManagerBot's /ping. A tap sends the page as a new message (the help message
stays as it is), /help &lt;name> shows the same page, and /fullhelp is the full
guide in one tap -- /help full stays. A bot's pages are HELP_PAGES in its
bot.py: name -> function(lang) -> text, with every number looked up when the
page is shown (by attribute, on the module that owns it, so a value that
changes at runtime is read as it is now). A page is plain text, because its
examples hold links and angle brackets, and it carries no keyboard: it ends
by pointing at /fullhelp.

### `help_page_name()`

The page that `/help <word>` asks for, or None. Reads the first word
only, and forgives how it was typed: any case, a leading slash, the
"@botname" Telegram adds, a "help\_" in front.

### `help_page_text()`

One page, then where the full guide is.

### `help_page_buttons()`

One button per page, in the order of `pages` (an alias never gets one),
two to a row. The label is `labels[name]` translated when the bot gives
one, else the command's name with a slash.

### `help_page_button()`

The handler of a tap on a page button, for one bot: the page arrives as
a new message, so the help message above it is never edited and stays to
be tapped again. A name that is not a page (a button from before a
redeploy, or a forged one) is told so and nothing is sent. Register it
with pattern=HELP_PAGE_PATTERN.

### `full_help_command()`

The handler of /fullhelp: what /help full gives, for one bot --
`build_text(lang, full=...)` is its build_help_text, `rows(lang)` the
button rows it keeps under its help, if any, and `pages`/`labels` its page
buttons (see help_page_buttons).

## Runtime tuning

_At `GC_THRESHOLD = int(os.environ.get("GC_GEN0_THRESHOLD", "5000"))`:_

The garbage collector's generation-0 threshold. The default of 700 means a
full young-generation sweep every 700 net allocations, which on a bot
handling one message a minute is a sweep every few seconds, forever, over a
heap that is almost entirely long-lived. Raising it trades a little peak
memory for a lot of pointless scanning.

### `tune_runtime()`

Call from each bot's post_init. Three settings, all of them about what
an idle-most-of-the-day process costs to keep running.

```text
1. asyncio's default executor sizes itself to min(32, cpu_count + 4), and
   on a shared cloud host cpu_count is the *machine's* core count rather
   than this container's slice of it. That is up to 32 threads, each with
   its own stack, standing by for a workload whose peak is a couple of
   concurrent database calls and one ffmpeg. Four is plenty, and the
   difference is real resident memory on the smallest plan that fits.
```

```text
2. Everything imported at startup -- python-telegram-bot, psycopg, this
   bot's own modules -- is permanent by definition, and the garbage
   collector walks all of it on every full collection for the rest of the
   process's life. gc.freeze() moves it out of the collector's reach
   entirely. Called here, at the end of startup, because that is the last
   moment at which "allocated so far" and "will never be freed" mean the
   same thing.
```

3. The collection threshold above.

(The fourth knob is not settable from inside Python: glibc gives a
threaded process up to 8 x cpu_count malloc arenas, each of which can
hold on to tens of megabytes it will never hand back. MALLOC_ARENA_MAX
caps that, and is set in nixpacks.toml where the runtime environment is.)

## What one person is allowed to cost

Telegram rate-limits what a bot SENDS and nothing at all about what it
receives, so until now one script could hold a container and a shared
database busy for as long as it liked: /stats in a loop is a query each, a
link pasted into DownloaderBot is a fetch and an encode, and an inbox link
is meant to be posted somewhere public, which is where the traffic that
does this comes from.

The ceiling is per user and deliberately high -- a fast typist, an album,
and a run of button taps all have to pass -- because what it is defending
against is a loop, not a hurry.

Two properties matter as much as the number:

```text
It runs BEFORE any handler, so a refused update costs one dictionary
lookup rather than a database round trip. A limit checked inside the
handler has already paid for the thing it was meant to prevent.
```

```text
It answers once and then goes quiet. A gate that replies to every
refused message turns an incoming flood into an outgoing one, which is
worse: the sender pays nothing and the bot pays Telegram's send budget
for every other user in the queue.
```

In memory rather than in the database on purpose, for the same reason
AnonBot's own limiter is: one container holds the polling lease, so there
is exactly one counter, and paying a round trip to enforce a per-minute
limit would cost more than the limit saves. A redeploy forgets it, and the
worst that costs is one burst getting through after a restart.

### `flood_wait_seconds()`

0 if this person may be served now, otherwise the whole seconds until
their oldest counted update falls out of the window.

### `attach_flood_gate()`

Register in a group of its OWN, above every other group.

Its own group because python-telegram-bot runs one handler per group and
this one matches every update; above the others because a limit that
runs after the work has not limited anything. The operator is exempt --
/messageas and a sweep through /status are the bot's own operator using
it, and being throttled out of one's own bot during an incident is the
wrong failure.

`exempt`, if given, is asked about each update first, and True lets it
through uncounted: for updates a bot asked for itself, such as forwards
that arrive a hundred at a time.

#### `attach_flood_gate._gate()`

- At `return`:
  Not counted in a group chat: most of what a person says there is
  not meant for the bot (DownloaderBot reads a whole group for its
  links), and a "please wait" about somebody's chatting would go to
  everybody in it. Their updates there still run one at a time,
  with at most PER_USER_WAITING_MAX waiting (PerUserUpdateProcessor),
  and a download still has its own limits.

## The 220 ⚡ gift, claimed by writing (ConvertBot and DownloaderBot, 2.2.0)

While the gift is open (family_link, "The 220 ⚡ gift"), the first message
somebody sends one of these two bots gives it to them -- once across the
family -- and tells them. The bot's own answer is never held up for it: the
handler only decides whether to look, and the reading, the claim and the
notice run as a task of their own.

_At `_promo_claiming: set = set()`:_

Whose claim is running now, so two messages at once claim once.

_At `_promo_state = {"over": False}`:_

Set once the gift is over: from then on a message costs nothing at all.

### `_notice_language()`

The language for the gift's notice. Blocking. The ad sends people to a
bot they have never used, so most claimants have chosen no language here
yet, and their first /start is showing the picker: the one they chose in
this bot if any, else the one their Telegram app is set to, else English.
Never i18n.get_lang, which would cache "en" for them as if they had chosen
it -- and never written back, so the picker still decides.

### `_claim_and_tell()`

- At `if window is not None and now >= window[1]:`:
  Not started yet (nothing remembered: it may start later), or over.
- At `log.warning("The gift could not be claimed (%s); the next message tries again", type(exc …`:
  Nothing was given (a claim is one transaction) and nothing is
  remembered, so their next message tries again.
- At `log.debug("The gift's notice could not be sent", exc_info=True)`:
  The credit is theirs either way, and /balance shows it.

### `attach_promo_claim()`

Register in a group of its OWN (python-telegram-bot runs one handler per
group), after the flood gate's: somebody being throttled claims nothing.
Only a new message in a private chat counts -- not a button, an edit or a
group -- because "the first message they send" is what was asked for.

## Donation reminder + /donate (Telegram Stars)

Cadence: usage only, on a schedule that runs out.

Each bot decides what counts as an "action" -- a finished pack, a completed
conversion, a delivered download -- and calls maybe_donation_nudge() at
that point. DONATION_STEPS is how many further actions are needed before
each successive nudge: the first at 15, the second 60 after that, the third
200 after that, and then never again.

What this replaced, and why (v1.2.3):

```text
The old rule was "20 actions OR 14 days since the last nudge", floored at
3 days. The time half was the problem. It fired on people who were barely
using the bot -- the ones least likely to pay for it and most likely to
read a fortnightly reminder as spam -- and it never stopped, so a user of
three years who had already decided not to donate would be asked around
seventy times. An ask that repeats forever is not an ask, it is a tax on
being a regular.
```

```text
Usage-only fixes the first half: the nudge now only ever arrives right
after the bot has just done something useful, which is the one moment it
has earned the right to ask. The schedule fixes the second: someone who
has been asked three times across 275 successful actions and has not
given has answered the question, and continuing to ask only costs
goodwill.
```

The counters live in THIS bot's own schema -- the five bots share one
database but never each other's tables -- so somebody active in two bots
may be asked by each. That is an accepted tradeoff and it is bounded now,
because each bot asks at most three times ever.

Anyone with a paid row in star_transactions is never nudged again by that
bot. Thanking somebody for donating by asking them to donate is the worst
sentence this file could produce.

_At `DONATION_PIN = os.environ.get("DONATION_PIN", "off").lower() in ("on", "1", "true", "yes")`:_

---- pinning the nudge instead of repeating it (off by default) ----
Pinning the prompt in the chat, so that it stays visible, was proposed
without being re-sent. The instinct is right -- "ask once, remain visible"
beats "ask again" -- and the implementation is here, but it is off, for
three reasons worth reading before turning it on:

```text
A pin is a permanent claim on the top of the user's chat window, for
something explicitly voluntary. The nudge's own text ends "no pressure
either way", and a pinned ask contradicts it.
```

```text
Pinning posts a service message into the chat ("… pinned a message"),
so the thing meant to avoid a second notification creates one. Pinning
silently avoids the alert but not the service message.
```

```text
It competes with the bot's real uses of the chat header. Nothing pins
today, but the first genuinely useful thing to pin -- a maintenance
notice, a sticker pack link -- would be arguing with a tip jar.
```

With the schedule above capping the ask at three times ever, the problem
pinning was meant to solve is mostly already solved. To have it anyway:
DONATION_PIN=on. It pins the nudge silently, unpins the previous one first,
and unpins for good the moment that person donates.

_At `DONATE_STAR_OPTIONS = [15, 50, 150, 500]`:_

Preset amounts shown as buttons on /donate. Four tiers: a tap that costs
almost nothing and exists so that saying thanks is possible, two in the
middle, and a high anchor -- which is there as much to make the middle
look reasonable as to be chosen. Roughly $0.30 / $1 / $3 / $10 at what a
user pays for Stars.

_At `MAX_DONATION_STARS = 100_000`:_

Loose sanity ceiling for /donate &lt;amount> -- not Telegram's real per-invoice
cap (which Telegram enforces itself; a rejected amount just surfaces
Telegram's own error text back to the sender), just a guard against an
obvious typo like an extra zero or two.

## Paying without paying, for TestBot

With this on, a Stars top-up skips Telegram entirely: no invoice is sent,
nothing is charged, and the credit lands in the balance exactly as a real
payment would put it there. It exists because the alternative for testing
the money path is spending real Stars on every run, and the part worth
testing is what happens \*after\* the payment -- the rate, the first-payment
bonus, the ledger row, the balance the user is shown.

It is off unless the environment says otherwise, and the environment that
says otherwise is testbot/token.env. Two things make it safe to have in the
shared file rather than in a fork of it:

```text
every message it produces says so, in the user's own language, so nobody
can believe they paid;
it logs a warning at import, so a production process that somehow had it
set would say so in the first line of its log rather than silently
handing out free credit.
```

Fiat is deliberately not covered: a card payment has a provider behind it
and a sandbox for it belongs to that provider, not here.

_At `FIAT_CURRENCIES = {`:_

---- optional fiat alternative (USD) to Stars ----
Cashing Stars out to real money goes through Fragment (Stars -> TON ->
sell on an exchange) -- several hops for something as simple as "cover the
hosting bill." A regular Telegram Payments provider skips that: connect
one via @BotFather -> /mypayments (Stripe or another provider that
supports the operator's country), then set PAYMENT_PROVIDER_TOKEN_USD. Nothing below
activates until that's set -- with it unset, /donate behaves exactly as it
did before (Stars only).

"exp"/min/max here are straight from Telegram's own reference table
(https://core.telegram.org/bots/payments/currencies.json, checked
2026-08-24) -- invoice amounts must be in the currency's smallest unit
(10\*\*exp per whole unit), e.g. $5.00 -> 500. Don't add another currency
here without checking that table for its real exp -- guessing it risks
over/undercharging someone by a factor of 100. Its options list must stay
the same length as DONATE_STAR_OPTIONS -- donate_command() zips them
together into one Stars-column-then-fiat-column button grid per row.

_At `"options": [1, 3, 5, 10], "min_minor": 100, "max_minor": 1_000_000,`:_

Deliberately not a straight conversion of the Stars column. The
Stars tiers are priced for a tap; a card payment has a floor below
which the processor's own fee eats most of it, so the bottom tier
is $1 rather than $0.30.

_At `FIAT_DONATIONS = os.environ.get("FIAT_DONATIONS", "off").lower() in ("on", "1", "true", …`:_

---- the freeze ----
Real-currency donations are FROZEN as of v1.3.0. The code below is
complete and stays complete; what it does is nothing.

Why frozen rather than deleted: connecting a provider is a decision about
payouts and a country, not about this file, and the day it is made the work
should already be done. Deleting it would mean rewriting the button grid,
the amount validation, the invoice call, the ledger's currency column and
three languages' worth of strings, all of which exist and all of which
work.

Why frozen rather than just left unconfigured: it already did nothing
without PAYMENT_PROVIDER_TOKEN_USD, but "does nothing because a variable
happens to be empty" is one accidental deploy-variable away from a half-
enabled payment path -- an invoice offered on a rail that cannot take it,
in front of a real user. One flag, checked at the single point every fiat
path already goes through, so the freeze cannot be lifted by accident.

To unfreeze: set FIAT_DONATIONS=on AND PAYMENT_PROVIDER_TOKEN_USD=&lt;token>.
Both, deliberately. Nothing else has to change.

### `_fiat_provider_token()`

The one chokepoint. Every fiat path in this file asks this first --
the button grid via \_available_fiat_currencies, `/donate 5 usd` before it
validates anything, and \_send_donation_invoice before it calls Telegram
-- so returning None here is the whole freeze, and no other function
needed a line changed to honour it.

### `format_ledger_amount()`

amount is in the currency's smallest unit (see FIAT_CURRENCIES) for
fiat, or a plain Stars count for XTR.

### `_donation_nudge_due()`

Blocking; the async wrapper below is what handlers call. One statement
to bump-and-read, and a second one only in the rare case where the nudge
actually fires.

- `return False` — asked three times across 275 actions; that is an answer
- `return False` — hard floor -- never twice in quick succession

### `maybe_donation_nudge()`

Await right after a successful action. Returns text to append to the reply, or None most of the time (send nothing).

Async because it writes to the database, and this is called on the
success path of the bot's main job -- doing it inline on the event loop
stalls every other user's update for the round trip.

`context` and `chat_id` are only used when DONATION_PIN is on, in which
case the nudge is sent as its own message and pinned rather than handed
back to be appended -- there is nothing to pin about a sentence glued to
the end of somebody else's reply. That path returns None, so a caller
that passes them and one that does not both do the right thing with the
return value.

### `_pin_donation_nudge()`

Send the nudge on its own and pin it, replacing any earlier one.

Every step is best-effort. A pin that fails must not cost the user the
action they actually asked for, and pinning is the sort of thing
Telegram refuses for reasons outside this bot's control -- a chat
cleared, a message older than the pin limit, a client that unpinned it
already. `disable_notification=True` because a nudge that pings someone's
phone is worse than one that does not; it still leaves the "pinned a
message" service line, which is one of the reasons this is off by
default.

### `unpin_donation_nudge()`

Called when somebody donates. Whatever the cadence says, the ask is
over for this person -- and leaving it pinned would make a thank-you look
like a repeat request.

### `payer_language()`

The language to answer a payment in. Never the reason a payment goes
uncredited: when the database cannot say which language the payer chose,
their Telegram app's, else English.

_At `_MARK_ATTEMPTS = 3`:_

This bot's star_transactions row for an invoice is bookkeeping -- the credit is
family_link.topup's -- so it is written after the credit, tried again through a
database blip, and never stops a payment.

### `mark_star_transaction()`

Set this bot's row for the invoice `payload` to `status`. Never raises;
returns whether it was written.

### `credit_stars_payment()`

Credit a Stars payment once, and see that nobody who paid is left with
nothing. Returns family_link.topup's result; or None when the caller must do
nothing more: the payment was credited before (Telegram delivered it again),
or it could not be credited and has been refunded or handed to the operator,
with the payer told -- a conversion it paid for is then not started.

The credit comes first and this bot's row after it. topup itself retries a
database blip; what happens when it still fails is \_payment_not_credited's.

- At `emit_event("warning", "payment", "A Stars payment was delivered twice and credited once.")`:
  Telegram delivered a payment it had already delivered. It was credited
  the first time; crediting it again is the person paying once and being
  paid twice, and thanking them twice is just noise.

### `_payment_not_credited()`

A payment topup gave up on. Its claim is staked first (family_link.
claim_uncredited), so that no later delivery of it can credit it once it is
refunded; then:

```text
* staked: the Stars go back (refundStarPayment), the row says "refunded",
  the payer is told (topup_refunded) and so is the operator;
* topup's own claim was there: the credit landed, from an attempt whose
  answer was lost. Nothing is refunded and nothing more is said to the
  payer (the credit is on /balance); the operator is told;
* another delivery's claim: that one deals with it;
* no claim could be staked (the database is away, or there is no charge
  id), or the refund failed: whether it was credited is not known, or
  Telegram said no, so nothing more is done by itself -- the payer is told it will be put right (topup_failed), and the operator gets the charge id.
```

### `_credit_topup()`

Turn a completed payment into credit, and say what it bought.

The one place money becomes balance, so the real payment path and the
sandbox path cannot drift apart -- which for a thing that hands out
credit is the drift that matters.

Returns the text to send, or None when there is nothing more to say: a
payment that was already credited (see family_link.topup), or one that could
not be credited and was dealt with (credit_stars_payment). It always names three things: what was
charged, what was credited, and what the credit can and cannot do. The
last of those is not decoration -- credit is worth more than the Stars
paid for it and cannot be paid back out, so a message that only said
thanks for 15 ⭐ would be describing a donation rather than the
purchase this now is.

- At `text = i18n.t(lang, "topup_thanks", stars=result["stars"],`:
  `total` and `convert_bot` are for the bots that take donations: there the
  thank-you says the credit arrived in ConvertBot, the one place it can be
  spent. ConvertBot's own wording does not use them.
- At `emit_event(`:
  What was paid and what it bought, not who paid: the operator reads the family log, which carries no user details. The ledger keeps the id,
  which is where /balance &lt;id> looks when somebody asks.

### `_send_donation_invoice()`

amount is in the currency's smallest unit for fiat (see
FIAT_CURRENCIES), or a plain Stars count for XTR. Returns None on
success, or an error message to show the sender if Telegram itself
rejects the amount/currency (e.g. above Telegram's own per-invoice cap,
or no provider connected for that currency).

- At `return i18n.t(lang, "donate_currency_not_configured", currency=currency)`:
  Belt and braces. Nothing reaches here with a frozen currency today
  -- every caller checks first -- and the cost of being wrong about
  that is an invoice a user cannot pay, so it is checked again before
  anything is written to the ledger.
- At `text = await _credit_topup(chat_id, context, user, amount, lang,`:
  No invoice, no precheckout, no successful_payment update -- so this
  credits the balance itself rather than waiting for a callback that
  is never coming. The charge id records which path wrote the row, so
  a sandbox top-up is distinguishable in the ledger forever rather
  than looking like money that arrived.
- At `description=(i18n.t(lang, "donate_invoice_description",`:
  Fiat buys no credit (see donation_payment_received), so its
  invoice must not say it does -- it used to render "adds 500 ⚡"
  for a five-dollar donation, from the amount in cents.
- `provider_token=provider_token,` — empty string is required for Telegram Stars payments

### `_validate_donation_amount()`

whole_amount is in whole units (a Stars count, or whole dollars for
USD). Returns (amount in the invoice's own unit, None) on success, or
(None, error message) if it's outside the currency's allowed range.

### `donate_command()`

/donate -- with no args, shows preset-amount buttons for Stars plus
any fiat currency that has a payment provider connected (see
FIAT_CURRENCIES above), with a Custom button per currency for anything
else. /donate &lt;amount> [currency] skips straight to an invoice --
currency defaults to Stars, e.g. /donate 500 or /donate 5 usd.

- At `if not _fiat_provider_token(requested):`:
  Frozen or unconfigured both land here, and the sentence is the
  same either way -- "not set up on this bot" is true of both and
  is what the user needs to know. See FIAT_DONATIONS above.
- At `fiat_options = _available_fiat_currencies()`:
  One row per preset tier, Stars in the left column and each connected
  fiat currency in its own column to the right -- e.g. with just USD
  connected: [15⭐ 1$] / [50⭐ 5$] / [100⭐ 10$] -- plus a final row of
  Custom buttons, one per column, for anything not on the list.
- At `totals = await asyncio.to_thread(family_link.star_totals, update.effective_user.id)`:
  Said before the amount is chosen, not only in the thank-you: a Stars
  payment becomes credit that cannot be taken back out, and nobody should
  find that out after paying.
  What THIS person's next Stars earn: the ladder counts every Star they
  have ever paid, so the same button means a different amount of credit
  for somebody new than for somebody who has paid a thousand.

### `_donate_button_label()`

The amount and its currency. The bonus is no longer on the button: it
depends on what this person has paid before, across all their payments,
so the prompt above the buttons says what their next Stars earn.

_At `async def donate_amount_chosen(update, context) -> None:`:_

Callback data is not the button's. It is whatever the client sends back,
and a modified client can send anything at all for a button that exists --
so the three handlers below check what they are given against what this bot
actually offered, exactly as the typed `/donate <amount>` path above always
has. Taken on trust, `donate:abc` raised ValueError, `donatefiat:x` raised
on the unpack and an unknown currency raised KeyError: three ways for
anybody to fill the operator's crash channel from a phone, and a fourth to
raise an invoice for an amount no tier offers, which is the one thing
MAX_DONATION_STARS exists to prevent.

### `donate_fiat_amount_chosen()`

- At `if (cfg is None or currency not in _available_fiat_currencies()`:
  \_available_fiat_currencies() and not FIAT_CURRENCIES: a currency that is
  known but has no provider token, or is frozen, was never on a button.

### `donate_custom_button_chosen()`

Tapping a 'Custom' button asks for an amount via ForceReply; the
actual amount is picked up by donate_custom_amount_received below,
matched via the donate_custom_currency flag this sets in user_data.

### `donate_custom_amount_received()`

Register in a group before the bot's normal text handling (see
track_activity for the same pattern) -- a no-op unless
donate_custom_button_chosen just set the awaiting-amount flag, in which
case it consumes the reply and stops it from also being treated as a
normal message (ApplicationHandlerStop).

_At `async def donation_precheckout(query) -> None:`:_

---- primitives for bots that ALSO have their own Stars flow (e.g. ConvertBot's
/convert), which need to check the payload prefix themselves and only fall
through to these for "donate:"-prefixed ones ----

### `donation_precheckout()`

Caller has already confirmed query.invoice_payload starts with 'donate:'.

### `donation_payment_received()`

Caller has already confirmed the payload starts with 'donate:'.

A payment used to end in a thank-you and nothing else. Now it ends in
credit, through the same \_credit_topup the sandbox uses -- so what a
tester sees and what a payer sees are produced by one function.

Fiat still only gets the thank-you: the credit rate is defined against
Stars, and inventing an exchange rate from a currency's minor units would
be making up a number.

- At `await unpin_donation_nudge(context, user.id, update.effective_chat.id)`:
  The ask is over for this person -- \_donation_nudge_due checks the same
  paid row and will never fire again, and anything still pinned comes
  down now rather than sitting above a thank-you.

_At `_LEDGER_LABELS = {"refund": "returned"}`:_

How a ledger reason reads in /balance. Reasons stay short English tokens (see
below), but "refund" beside credit coming back reads as Stars coming back,
and in what the bots say only Stars are ever refunded -- and they are not.

_At `BALANCE_RECENT_ROWS = 8`:_

How many of the latest movements /balance prints; the balance page under /help says it.

### `balance_command()`

/balance -- what this person is holding, and what it is for.

Registered by every bot rather than only by the ones that charge, because
the balance is one wallet for the whole family: somebody who topped up in
StickerBot has to be able to ask StickerBot where it went.

It always prints the rate and the no-withdrawal line, not only when the
balance is zero. A number on its own invites exactly the wrong question
later -- whether it can be had back -- and the answer costs nothing to give
before it is asked.

- At `lines.append(f"  {when}  {sign}{row['delta']} ⚡  "`:
  The reason is a short English token from LEDGER_REASONS rather
  than a translated phrase, the same choice /mystars makes about
  ledger data: one word that groups rows, not prose.

_At `async def donation_precheckout_callback(update, context) -> None:`:_

---- full standalone handlers, for bots (like StickerBot) whose ONLY Stars
usage is donations -- register these two directly, no branching needed ----

## The two screens somebody sees before they ever press Start

A Telegram bot has three pieces of profile text and the family was setting
one of them. set_my_commands fills the slash menu, which only helps once the chat is already open. The other two are what a stranger meets first:

```text
the short description -- one line, next to the bot in search results and
under its name on the profile card;
the description -- up to 512 characters, and the whole of what an empty
chat shows above the Start button, under "What can this bot do?".
```

Both were blank, so an empty chat said nothing at all and search results
showed a name and a username. Both take a language_code, so all three
languages go up, and the English text goes up twice: once tagged "en" and
once untagged, because the untagged one is what Telegram falls back to for
somebody whose client is in German.

Called from each bot's \_post_init beside set_my_commands. It costs six API
calls on a start that happens a few times a day, and it never raises: a rate
limit on a cosmetic call is not a reason to fail a deploy.

## The slash menu, twice

Every bot in the family handles commands it never offers. The operator-only ones -- /status, /dbdump, /messageas and each bot's own -- were kept out of
set_my_commands on the sound reasoning that there is no point advertising to
a stranger a command they cannot run. The unsound part was that this also
hid them from the one person who can: the operator types /status into the bot from memory, or does not, and a command nobody remembers is a command
that may as well not exist.

Telegram already has the answer and the family was not using it. A command
menu has a \*scope\*, and BotCommandScopeChat sets one for a single chat.
So the menu goes up twice: the public list at the default scope, which is
what everybody sees, and the public list plus the operator-only ones in each
admin's own chat with the bot. Nobody else's menu changes, and the guards
are untouched -- a scope decides what is offered, never what is allowed.

Two things are worth knowing about scopes:

```text
A scoped menu outlives the reason for it. Drop an id from *_ADMIN_ID and
that person keeps the longer menu until somebody calls delete_my_commands
for their chat. It is cosmetic -- every one of those commands still checks
_is_admin and still refuses them -- but it is why the menu is not a
permission.
set_my_commands for a chat Telegram has never seen fails. An admin who has
never opened their own bot is exactly that chat, so this never raises: a
cosmetic call is not a reason to fail a deploy, same as publish_profile.
```

AND IN THREE LANGUAGES, SINCE v1.6.0
The bots answer in English, Uzbek and Russian everywhere except the one list
somebody reads before they have understood anything. A menu also takes a
language_code -- the same way publish_profile() already sends the profile
text three times -- and Telegram picks the list matching the client's own
language, falling back to the untagged one.

So English lives in each bot's BOT_COMMANDS, where the menu can be read by
reading bot.py, and the other two live in i18n.COMMAND_MENU keyed by command
name. A command with no translation keeps its English description rather
than disappearing from that language's menu, which is the one failure mode
worth designing against: a half-translated menu is missing commands, and a
missing command looks like a bot that cannot do the thing.

Four commands are deliberately NOT translated, and they are the four that
look most like they should be. /en, /uz and /rus are each described in the
language they switch \*to\*, and /language is described in all three at once.
A per-language menu does not change that: somebody whose client is Russian
but who wants Uzbek has to recognise "O'zbekchaga o'tish", and somebody who
set the wrong language needs /language to be legible whatever the menu is
currently in. They are the way back, so they are written for everyone.

The operator's scoped menu stays untagged English. All admin output in this
family is English by policy, and the operator is the one reader whose language
is not in question.

### `_menu_in()`

`commands` with each description swapped for its `language` one, from
i18n.COMMAND_MENU (or i18n.GROUP_COMMAND_MENU for the group-chat menu).

Falls back per command, not per menu: an untranslated entry keeps its
English text and the rest of the list is still translated.

## A menu in the language somebody chose

Telegram picks which of the per-language menus to show from the language
the person's Telegram APP is set to, not from anything a bot knows. So /uz
changed every message this bot sends and left the menu in English for
anybody whose phone is in English or Russian -- which in Uzbekistan is most
people who would choose Uzbek. A menu set for one chat (BotCommandScopeChat)
outranks every language list, so choosing a language now sets one.

The catch with a per-chat menu is that it is frozen when it is set: a
command added in a later version would never reach it. Each person's
user_data remembers a signature of the menu they were given, and
track_activity compares it with what they would be given now, and sets it
again when the two differ. One call per person per change, made the next
time they use the bot rather than all at once at startup.

### `_menu_for()`

The menu this person should have, or None before publish_commands has
said what the menus are.

### `refresh_chat_menu()`

Give one person's chat the menu in their language. Never raises: a
menu is cosmetic, and a failure here must not cost anybody the language
change they asked for.

_At `_UNOWNED_SCOPES = (`:_

Scopes this code never writes (except all-group-chats, for a bot that passes
publish_commands a group menu). Telegram resolves a chat's menu by scope
precedence -- a chat's own, then all-private-chats, then the default -- so
a menu left in one of these by anything else outranks the default the code
does write, and reading the default says everything is fine. That is what
kept StickerBot's commands on TestBot's token long after it ran ConvertBot's
code. They are cleared on every start, so the code is the only authority.

### `publish_commands()`

The default menu for everyone, in every language; the scopes the code
does not own, cleared; and the operator's chat, in the operator's language.
`group`, for a bot that works in group chats (2.2.0: Sticker and
DownloaderBot), is the menu every group chat gets instead of being cleared.

- At `for language in (None,) + tuple(i18n.SUPPORTED_LANGUAGES):`:
  None first: the untagged list is what a client in a fourth language
  gets, so it goes up even if every tagged one fails after it.

## How a long message is laid out

Telegram gives a bot four things to design with: bold, a monospace run, an
expandable blockquote, and blank lines. That is the whole palette, and it is
enough -- what it is not is automatic, and until this existed every screen in
the family was one undifferentiated column of sentences. /privacy was the
worst of them at about two thousand characters, which on a phone is four
thumb-flicks of unbroken grey.

The rules, such as they are:

```text
A title line, once, at the top: the subject in bold, with the emoji that
the string already carries. Telegram has no heading levels -- there is
bold and there is not-bold -- so the title and the section headings use
the same helper and are told apart by position.
Section headings in bold, with a blank line above and none below.
Anything longer than a short paragraph goes in an expandable blockquote,
so the shape of the message is visible without scrolling and the detail is
one tap away.
Commands in a monospace run, so /deletemydata reads as a thing to type
rather than as a word in a sentence.
```

EVERYTHING INTERPOLATED IS ESCAPED. The text these wrap comes from i18n.py
and is written by whoever translated it; a stray "<" in a Russian string
would otherwise take the whole message down with "can't parse entities", and
the failure lands on the user as silence rather than as a log line.

_At `NO_PREVIEW = LinkPreviewOptions(is_disabled=True)`:_

A link in a notice should not drag a preview card in behind it: the card
for a GitHub page is a screenful of nothing under a document that is already
long. Same call downloader_bot/bot.py makes.

### `esc()`

The three characters Telegram's HTML parser cares about.

_At `_COMMAND_RE = re.compile(r"(?<![\w/])(/[a-z][a-z0-9_]{1,30})\b")`:_

Two touches that give a translated string structure without the translator
having had to think about markup, and without this file having to know which
language it is looking at.

### `body()`

Escaped, with every /command in it set as a command.

A slash-word is a thing to type, and it reads as one when it is in a
monospace run and as an ordinary word when it is not -- which matters most
in the sentence that offers /deletemydata, since that is the one somebody
is scanning for.

### `lead_in()`

`Money: it is voluntary` with the lead-in in bold.

Several strings already open with a short label and a colon, in all three
languages, because that is how somebody writes a paragraph that answers a
question. Bolding it costs nothing and gives a wall of paragraphs the
headings it already implies. A string with no such opening is returned as
it is, which is most of them.

### `collapsed()`

A blockquote that shows a few lines and opens on a tap.

Bot API 7.2 and later. An older client -- or an older self-hosted Bot API
server -- renders it as an ordinary blockquote, which is a worse but
perfectly readable version of the same thing, so there is nothing to
detect and nothing to fall back to.

### `quoted()`

A blockquote that is always open. For a few lines, not for many.

### `joined()`

The blocks that are not empty, one blank line between each.

_At `_TAG_RE = re.compile(r"<[^>]+>")`:_

The one thing that can still go wrong is a Bot API server too old to know
what an expandable blockquote is, which answers 400 rather than degrading.
That is a self-hosted-server setup (LOCAL_BOT_API_URL) and nobody's fault,
but the user should not pay for it with silence: the same text goes out
again with every tag removed. Worth the eight lines -- a bot that cannot
answer /privacy is worse than a bot that answers it in plain text.

## /privacy, /terms, /deletemydata -- what is held, and getting rid of it

A Telegram bot has no cookie banner, no tracking pixel and no third-party
embeds, so most of a website's compliance checklist does not apply to one.
What does apply is the part underneath: a bot holds personal data from the
first message, because a numeric Telegram user id is the only thing it can
address a person by, and it cannot ask permission before receiving one.

Telegram asks every bot for a privacy policy and gives BotFather a field to
put the link in, so there has to be something to link to. Three commands
rather than one document nobody opens:

```text
/privacy       what this bot keeps, who else sees it, how long it stays
/terms         what the bot may be used for, and where the money stands
/deletemydata  the erase button that makes the other two mean something
```

A policy is worth what it can be held to, which is why the third command
exists. Without it the first two describe a filing cabinet nobody can open.

The wording is per-bot on purpose. StickerBot keeps sticker packs, AnonBot
keeps who is talking to whom, DownloaderBot hands a link to somebody else's
server -- a policy describing "the bot" in the abstract describes none of
them truthfully. So this file owns the shape and the handlers, and each
bot's i18n.py owns two keys: "privacy_stored", the list of what it actually
keeps, and "privacy_others", whoever outside the family it has to talk to.
The long forms live in PRIVACY.md and TERMS.md in each bot's folder.

_At `OPERATOR_CONTACT = (os.environ.get("OPERATOR_CONTACT") or "mukhtorovmurodbek@gmail.com") …`:_

Who is running this copy, and how to reach them. A privacy notice that
cannot name someone to complain to is a notice about nobody. Left blank the
commands still work and omit the line, which is the honest result for a
local test instance -- better than printing an address that goes nowhere.
The operator's contact, as the default rather than only an environment
variable: an unset variable on one Railway service used to mean that bot's
/privacy named nobody. A deployment run by somebody else sets its own.

_At `PRIVACY_URL = os.environ.get("PRIVACY_URL", "").strip()`:_

Where the long forms were published, if they were. PRIVACY.md and TERMS.md
sit in each bot's folder, and pushed to MumuBots they have a URL; this is
where that URL goes, and it is also what BotFather wants.

### `_policy_footer()`

The two optional trailing lines, each dropped when unconfigured.

The URL is the one thing here that is not escaped and must not be: it goes
inside an anchor so that it is tappable, and the label is the address
itself, because a privacy notice is the wrong place to hide where a link
goes behind a word.

### `privacy_text()`

The notice, laid out so its shape is visible without scrolling.

The two long blocks -- the list of what is kept, and the retention
paragraphs -- are the ones that go behind a tap. What stays on screen is
every heading, who else sees it, and the line offering /deletemydata,
which is what somebody opening /privacy is usually looking for.

### `terms_text()`

Four paragraphs, three of which already open with their own label and a
colon -- in all three languages, because that is how the sentences were
written. lead_in() promotes those to headings; the one without is left as
a paragraph, which is what it is.

_At `ERASE_PREFIX = "erasedata:"`:_

---- erasure ----
Two taps rather than one, because there is no undo and on a phone keyboard
the command is a slip away from /donate. The confirmation spells out what
goes and what does not, per bot: erasing a StickerBot user forgets their
packs without deleting the packs themselves, which live on Telegram's
servers and not here, and erasing an AnonBot inbox holder breaks every copy
of their link that anybody has posted. Both are the right thing to do when
asked and the wrong thing to find out about afterwards.

### `paysupport_command()`

/paysupport -- required of every bot that takes Telegram Stars.

Telegram's rules for digital goods say a bot must answer /paysupport and
handle payment problems itself. Payments were decided to be final, so
this says that first, and then how a payment that went wrong -- charged
with no credit to show for it -- reaches somebody who can put it right,
which here means with credit rather than with Stars.

### `delete_my_data_command()`

Returns None on purpose. StickerBot registers this as one of its
ConversationHandler's entry points -- same reasoning as the language
switches there -- and an entry point returning None leaves the state
machine exactly where it was, which is what asking a question should do.
Only the answer below moves anything.

- At `await reply_formatted(`:
  The consequences stay on screen rather than going behind a tap. This is
  the one message in the family where the detail is the point: it is what
  somebody needs to have read before they press a button that has no undo.

### `delete_my_data_chosen()`

Every edit goes through live_message.edit_in_place rather than
query.edit_message_text, for the two reasons that helper exists. It never
raises -- and this one really can be asked to write the same text twice,
by somebody who taps a second, older confirmation after the first erase
already reported nothing to erase -- and it moves the message down to the
bottom if the person has said something since.

Returns ConversationHandler.END after an erase, which StickerBot's entry
points honour. Without it the state machine would sit in EDITING pointing
at a pack this bot has just forgotten, and the next sticker would raise
KeyError('owner_id') and page the operator about a crash that was really
somebody exercising their right to be forgotten.

- At `try:`:
  The two erasures are reported on separately, and deliberately not in
  one try. db.erase_user is a single transaction over this bot's own
  tables: it either happened or it did not, and that is what the person
  is being told about. lifecycle.forget_user clears a cache of work in
  progress that expires by itself within the day, so failing it does not
  make the answer above it untrue -- and saying "couldn't erase that"
  after the data is already gone is the one answer this command must
  never give.

  First, though: the tap that led here was buffered as activity,
  and the next flush would write a fresh row for the person straight back
  (a settings row, an activity row) after the erase. Note_erased drops
  them from the buffer and makes the flush skip them for a while; it also
  waits out a flush already in flight, so none lands after the erase.
- At `await asyncio.to_thread(reserve.note_erasure, user_id)`:
  A note that they asked, so that a reserve copy taken before now, if one
  is ever restored, erases them again (reserve.py). It logs its own failure.
- At `try:`:
  The family table, which is not this bot's own and so is not in
  db.erase_user. Only the part that is about them: a problem they reported
  still counts as a problem afterwards, it just no longer says who hit it.
  Reported on separately for the same reason as the line above -- failing
  it does not make "the data is gone" untrue of the tables that did.
- At `done = body(i18n.t(lang, "delete_data_done", rows=rows))`:
  Rendered before the cache is dropped, because the chosen language is
  part of what is being erased.
- At `try:`:
  Whatever the process still holds in memory, so the persistence flush a
  minute later does not write a fresh row straight back out again.

## /cancel -- getting out of whatever the bot is waiting for

Every bot has at least one state where it has asked a question and is now
sitting there waiting for the answer, and until this existed there was no
single way out of them.

/cancel asks before it does anything. A bot juggling a pack, a conversion
and a donation prompt at once had one /cancel that stopped all three, and
no way to say which one was meant -- so the command that exists to undo a mistake was itself the mistake, when the wish was to abandon the pack
and keep the conversion. It now offers one button per waiting thing, plus
"everything" when there is more than one and "keep going" always. Nothing
is stopped until a button is tapped.

The exception, and it is the common case, is having nothing to cancel:
with no pending state there is nothing to ask about, so /cancel answers
straight away exactly as it always did.

Each bot builds its own /cancel out of the pieces below: cancel_items()
for the states this file owns, cancel_question() and the \*\_choice helpers
for the asking, release_force_reply() for Telegram's client-side reply
lock, and build_cancel_text() so all of them report back in the same
shape. Reporting \*what\* was stopped is the point, not decoration.

_At `FORCE_REPLY_KEY = "force_reply_msg_id"`:_

Where the id of the last ForceReply prompt this bot sent is parked.

### `remember_force_reply()`

Call right after sending anything carrying a ForceReply, so /cancel
can delete it again. Cheap, and the entry is dropped by the handler that
consumes the reply or by release_force_reply(), whichever comes first.

### `release_force_reply()`

Let go of a forced reply.

ForceReply lives on the \*client\*, not on the server: once a bot sends a
message carrying one, that user's reply box stays pinned to that message
until they answer it or it stops existing. Nothing about restarting the
bot, or even the database, releases it -- which is how a "how many Stars?" donation prompt can still be demanding an answer two
days and several redeployments later. Deleting the prompt is the only
lever a bot actually has.

```text
Two ways to find it, because the first stops working the moment the
process restarts and takes user_data with it:
  1. the id remember_force_reply() stored when it was sent, and
  2. the message this /cancel is itself a reply to -- which is exactly
     what it will be, since a forced reply box is what the user was
     looking at when they typed it.
```

stored_only turns (2) off, for the button path: there, the message being
"replied to" is the bot's own "which one?" question, whose reply target
is the user's /cancel. Deleting that would be deleting the wrong message
entirely -- theirs. ask_cancel_choice stores the id under (1) before the
question goes out, so nothing is lost by ignoring (2) here.

Best-effort throughout. Telegram only lets a bot delete messages under
48 hours old, so an old enough prompt cannot be removed at all; that is
what the ReplyKeyboardRemove in build_cancel_text()'s caller is for, as
it clears the reply lock client-side regardless of the prompt's age.

- At `logging.getLogger(__name__).debug(`:
  Already gone, too old to delete, or never this bot's. None of those
  are worth failing a /cancel over.

### `reset_user_state()`

user_data.clear(), minus the one key /cancel is not finished with.

A bot whose /cancel wipes user_data has to do it \*before\*
release_force_reply() gets a chance to read the prompt id out of it,
which quietly cost StickerBot the ability to take its own donation
prompt back down. Keeping the key here rather than making every caller
remember the ordering is the version that stays correct.

\_last_seen stays too: it says the person is here, and both the soft stop's
count (lifecycle.usage_here) and the idle prune (\_prune_user_data) read it.

_At `CancelItem = namedtuple("CancelItem", "key label button")`:_

```text
What /cancel offers, one per thing the bot is waiting on:
  key    -- goes in the button's callback_data, and comes back to the bot
  label  -- how the "Cancelled:" report names it, a full phrase
  button -- how the button names it, short enough to read on a phone
```

### `cancel_items()`

The waiting-on-the-user states that live in this file, for the calling
bot to offer alongside its own. Reads state; changes none of it -- that
is cancel_shared_item's job, once the user has actually chosen.

### `cancel_shared_item()`

Stop one of this file's states by key. Returns how to report it, or
None if that key is not this file's or was not pending after all.

### `cancel_question()`

The "which one?" message: what is pending, and a button each.

One button per row rather than a grid -- these are full phrases, not
yes/no, and a two-column layout truncates them on a phone. "Everything"
only appears when there is more than one thing it could mean, and "keep
going" always does, because opening this menu must not be a one-way
door: /cancel is what people reach for when they are already unsure.

### `ask_cancel_choice()`

Put the question on screen. False means there was nothing to ask
about, and the caller should answer the old way -- see finish_cancel.

Remembers what /cancel was a reply to, because the answer arrives as a
button tap on a different message and release_force_reply would have no
way to find the prompt otherwise.

### `cancel_choice_key()`

The key behind the tapped button -- an item's own, or "all"/"none".

### `finish_cancel()`

The last two steps of every bot's /cancel: release the reply lock and
say what was stopped. ReplyKeyboardRemove is what actually unpins the
reply box on the client when the prompt itself was too old to delete.
`keyboard` goes up instead of the removal where a bot keeps a reply
keyboard of its own (StickerBot's pack buttons, which a bare removal took
away).

`stored_only` turns off release_force_reply's second route -- "delete
whatever this /cancel is a reply to" -- for bots where that guess is
expensive to get wrong. It is a good guess when every message a bot sends
is a menu or a status line, and a bad one when its messages are the
product: AnonBot's are somebody's conversation, and `/cancel` sent as a
reply to one used to delete it and report a prompt that never existed.

The default is unchanged for the three bots whose messages are menus.
They are not entirely safe from it either -- a `/cancel` replying to a
converted file or a finished download deletes that too -- but the fix
there is a judgement about those bots, not a side effect of this one.

- At `if released and not stopped:`:
  A prompt that could still be deleted, but nothing in memory to go with it, is
  the signature of one that outlived the process that sent it: user_data
  does not survive a restart, but Telegram's reply lock does. Saying
  "nothing to cancel" while visibly deleting the thing that was pestering
  them is the one answer that would make no sense here.

### `finish_cancel_choice()`

finish_cancel's twin for the button path.

The question message becomes the report, which is both tidier than a
reply underneath it and the only way to make sure a set of buttons that
has already been acted on cannot be tapped a second time.

An edit cannot carry ReplyKeyboardRemove, so the client-side reply lock
gets its own one-line message -- but only in the case that actually
needs it: a prompt that was known about and could not be deleted, which means it is
over Telegram's 48-hour deletion limit and still pinning the reply box.
A prompt old enough for that has almost always outlived the process that
sent it, in which case nothing is pending, nothing was asked, and this
path was never reached at all.

- At `await live_message.edit_in_place(query.message, context.bot, text)`:
  Through live_message rather than edit_text: if the user has typed
  anything since tapping, this report belongs at the bottom of the chat
  with their message, not rewritten above it. It handles the send itself,
  and never raises, so there is no fallback left to write here.

### `keep_going()`

"Keep going" -- the way back out of the question, having changed
nothing. The menu is replaced rather than left sitting there with live
buttons under an answer that has already been given.

## /feedback

A way to write to whoever runs the bots, about anything. 2026-10-03: a command for user feedback, where people can send a message with a suggestion or a report about anything.

What is kept is the words and nothing about who wrote them: the bot, the
language and the time (family_link.save_feedback). ManagerBot delivers it to
the operator, each one as it arrives or all of them in the nightly report -- the operator's switch, in ManagerBot's /feedback.

It is one-way, and says so wherever it is mentioned -- the question, the
thank-you, /help, /privacy. Offered a way to reply, the decision was not to: feedback goes one way only -- the text, copied and sent by the bots -- and is said to be read-only wherever it is mentioned.

Two ways to send it: `/feedback <text>` in one go, or `/feedback` alone,
which asks with a ForceReply. The answer is taken only when it is a reply to
that question, and that rule matters most in AnonBot, where nearly every
message is a reply: an inbox holder who taps /feedback and then answers a guest
before writing anything must have the answer reach the guest, not the
person who runs the bots.

_At `FEEDBACK_MAX_CHARS = 4096`:_

Telegram's own ceiling on a message, which an answer cannot pass anyway; a
`/feedback <text>` typed in one go is held to the same.

_At `_feedback_sent: "OrderedDict[int, deque]" = OrderedDict()`:_

user id -> monotonic times of what they sent in the last day. In memory, for
the reason the flood gate is: one process holds the polling lease, and a
redeploy forgetting the count costs at most one more day's allowance.

### `_feedback_room()`

Whether this person may send another one today.

### `_take_feedback()`

Keep it for the operator and say so -- or say exactly why not.

### `feedback_command()`

/feedback [text] -- send it now, or be asked for it.

- At `text = _COMMAND_WORD.sub("", message.text or "", count=1).strip()`:
  The message's own text rather than context.args, which splits on
  whitespace and would flatten a paragraph into one line.

### `awaited_text_received()`

Register in a group before the bot's own message handling, with
`(filters.TEXT | filters.REPLY) & ~filters.COMMAND` -- see track_activity
for why it needs a group of its own. It takes the two things this file
asks somebody to type: the answer /feedback asked for, and a custom
donation amount. One handler for both, because python-telegram-bot runs
one handler per group and both would match the same message.

A no-op for anything else, which then reaches the bot as usual. When it
takes a message it stops it there (ApplicationHandlerStop), so feedback is
never also relayed, converted or downloaded.

- At `await _ask_for_feedback(message, context, lang, "feedback_text_only")`:
  A photo or a voice note sent as the answer. The words are the
  whole of what is kept, so ask again rather than keep nothing.

_At `_LOG_FORMAT = "%(asctime)s %(name)s %(levelname)s %(message)s"`:_

Logging, crash tracking, active-user tracking, hosting detection --
all in support of each bot's operator-only /status command.
Each bot's own process gets its own logs/ folder (next to its bot.py) --
same "no shared files between bots" independence as everything else here.
bot.log gets everything at INFO+; errors.log gets WARNING+ only, from any
logger in the process (not just the PTB error handler below), so a stray
warning logged deep in some helper module still ends up somewhere findable
without needing to grep the full bot.log.

_At `_recent_errors: deque[tuple[str, str]] = deque(maxlen=10)`:_

In-memory only -- resets on restart, which conveniently lines up with
"since this deployment" rather than needing its own DB table. Capped at 10
so a crash loop can't blow up memory; the running \_error_count below still
reflects the true total even once older entries fall off the deque.

_At `_event_hook = None`:_

Set by family_link.attach() to family_link.report_event_soon, so anything
worth waking the operator up for also reaches ManagerBot. Left as None when a
bot runs standalone (FAMILY_BUS=off, or no shared database reachable) --
every call site below tolerates that, and none of them may ever raise:
the most important caller is record_error(), i.e. the crash path itself.

### `_log_to_files()`

Files on a laptop, stdout only in the cloud.

On a host like Railway the container's filesystem is ephemeral and the
platform's own log viewer already captures stdout, so the rotating files
are three handlers' worth of formatting plus up to 12 MB of disk writes
that nobody will ever read -- and disk writes are the slowest thing a
small container does. LOG_TO_FILES=1 forces them back on anywhere;
LOG_TO_FILES=0 forces them off locally.

### `setup_logging()`

Call once near the top of each bot's bot.py, passing \__file_\_.

Always logs to the console -- that is what `docker logs` and Railway's
log viewer show. Adds rotating bot.log/errors.log files next to that
file when running somewhere they will actually survive (see
\_log_to_files above).

- At `for noisy in ("httpx", "httpcore", "telegram.ext.Updater", "apscheduler"):`:
  These three are chatty at INFO -- httpx logs a line per HTTP request,
  which for a long-polling bot means one every few seconds, forever, for
  no information at all. Warnings and above still come through.

## Transient network conditions vs. actual faults

A long poll losing its connection, a read timing out, Telegram asking the bot to slow down: PTB retries all of these itself and the bot keeps working, so
waking the operator with a traceback for each one is noise that trains anyone to ignore the channel that also carries real crashes.

The catch is PTB's class hierarchy: BadRequest \*subclasses\* NetworkError,
and a BadRequest is a genuine fault -- a malformed API call, a bug in the bot -- so a
plain `isinstance(exc, NetworkError)` would swallow exactly the errors most
worth hearing about. It has to be excluded explicitly.

_At `NETWORK_ALERT_AFTER = int(os.environ.get("NETWORK_ALERT_AFTER", "20"))`:_

How many blips in a row before saying something. Reset by any update that
arrives, since one arriving proves the connection is working again. Roughly
a few minutes of a dead link at a 30-second poll.

- `_network_blips = 0` — consecutive, since the last update actually arrived

- `_network_blips_total = 0` — since this process started

_At `PERMANENT_NETWORK_MESSAGES = ("entity too large", "file is too big", "too big", "too lar …`:_

PTB raises Telegram's HTTP 413 -- "Request Entity Too Large" -- as a plain
NetworkError, so it matched the tuple above and was filed as a flaky
connection. It is the opposite: permanent, and the same upload fails every
time. That is how two successful ConvertBot conversions of a 200 MP photo
vanished -- result converted, upload refused, error handler said "retried
by PTB", user told nothing, credit kept. Anything whose text says the
payload is too big is a real failure, whatever class it arrived as.

### `note_network_blip()`

Counted and logged, never reported as a crash -- until there have been
enough in a row to mean the connection is gone rather than flaky, which is
worth exactly one message.

### `note_network_ok()`

An update arrived, so the connection works. Called from track_activity,
which runs before every other handler.

## A Conflict is one blip or a second copy -- only a run of them is the second

Telegram answers a getUpdates with "terminated by other getUpdates request"
when two requests for the same token overlap. A second copy of the bot
polling the same token does that on every poll cycle. But a single one is
also what a ~90 s network/database stall leaves behind (2026-10-09): the
bot's own request was still in flight when its retry arrived. Filing every
one as a crash woke the operator for a false alarm, so one is logged as a
warning and only a run of them is reported -- once.

- `CONFLICT_ALERT_COUNT = 3` — this many Conflicts ...

- `CONFLICT_ALERT_WINDOW_S = 120` — ... inside this many seconds means a second copy

- `CONFLICT_QUIET_RESET_S = 600` — seconds with no Conflict at all before it may alert again

_At `_conflict_times: "deque[float]" = deque(maxlen=CONFLICT_ALERT_COUNT)`:_

Monotonic times of the most recent Conflicts (only the last few matter), when
the latest was, and whether this run has been reported. Per process: every
bot is its own process in the base edition, and the combined edition loads a
separate copy of this module per bot, the same as \_network_blips above.

### `note_conflict()`

A Conflict from the long poll (no update behind it). Counted and logged
as a warning, never a crash -- until CONFLICT_ALERT_COUNT of them arrive
within CONFLICT_ALERT_WINDOW_S, which is a second copy polling the same
token and is worth exactly one report. After that it stays quiet until
CONFLICT_QUIET_RESET_S pass with no Conflict, then counts afresh.

The log line carries numbers only: no token, and not the text of the error.

## Problem codes, and the button that reports one

The request: "Give every possible exception that the user might trigger a code
or a unique identifier. If user triggers something with such an error, they
should get a button too, that says 'report the issue'." The codes and what
they mean are in problems.py; i18n.t() ends every coded message with its
code line.

Both happen at the one place every message leaves through -- the bot's own
send_message, edit_message_text and answer_callback_query -- rather than at a
hundred call sites, each of which would be one more place to forget them.
Every message that ends in a known code is logged with its time, code and a
fresh incident id. The problems worth reporting also get a Report row beside
whatever buttons they already had, carrying that same incident, so a report
and its log line meet. About a Report button under the unknown-command answer: not every message needs a report button, but the report itself stays; every exception is logged inside the bot automatically with time and id, and that one is simple. Which problems are
simple is problems.SIMPLE.

\*\*The problem is written down whether or not anybody taps anything.\*\* Asked for in 1.7.0: the error is included without the report button being pressed, only generic technical data is stored, with nothing specific to a user, and the message says the issue is logged with no user data. So every
coded message any bot sends puts a row in family.problem_reports holding the
bot, the code, the incident, the time and the version -- and says so, on the
message, above the button.

That changes what the button is for. It used to be the only way a problem
reached the operator at all, which made "Report the issue" a chore somebody did
on the bot's behalf. Now the problem is already there, and tapping adds the
one thing it cannot have without asking: who it happened to. As asked for: a user who wants to help can press the report button to include user-specific data, with the kinds of data named before the details are actually sent. The disclaimer names each field before anything is sent, and
nothing is sent until Send is tapped.

What goes to the operator's Telegram is still only that a report arrived, with
the code and the incident. Who sent it is read with ManagerBot's /report
&lt;incident>, deliberately, rather than pushed unasked.

_At `PROBLEM_FLUSH_SECONDS = int(os.environ.get("PROBLEM_FLUSH_SECONDS", "20"))`:_

Recording every problem could not go on the sending path. A coded message is
sent from inside a failure, often several at once -- FM-FLOOD is by
definition many people at the same moment -- and a Postgres round trip there
would add the database to the list of things that can go wrong while the bot
is already going wrong, and add it to the \*user's\* wait.

So occurrences collect here and go out as one pass on a timer, the same
shape as the activity buffer further down for the same reason. A hard kill
loses at most one window of them, and they are in this bot's own problems.log
either way.

## Which problems interrupt the operator, and how often

problems.level() says what a code is worth (1 urgent / 2 fault / 3 refused).
Two rules turn that into messages:

```text
An urgent problem is sent the moment it happens -- but at most once per
code per ALERT_COOLDOWN_SECONDS. A crash loop is one fault, not four
hundred, and four hundred messages is the fastest way to make somebody
stop reading the one that mattered. What the cooldown suppresses is
counted and carried into the next alert for that code.
```

```text
A *fault* escalates to an urgent alert when the same code happens
BURST_COUNT times inside BURST_WINDOW_SECONDS. Fifty "all download routes
failed" in an hour is not fifty people having bad luck, it is a route that
has gone down -- which is a level-1 fact assembled out of level-2 parts,
and the only way to notice it on the day rather than in the nightly
report.
```

_At `_alerted: "dict[str, list]" = {}`:_

code -> (monotonic time of the last alert, how many were suppressed since)

_At `_recent_faults: "dict[str, deque]" = {}`:_

code -> deque of monotonic times, for the burst rule

### `_should_alert()`

(send one now?, how many went unreported since the last one).

The whole cooldown lives here rather than in the caller, so the counting
and the decision cannot drift apart.

### `_is_burst()`

Whether this code has just crossed the burst threshold.

### `_alert_owner()`

Put an urgent problem on the family event bus, which is what ManagerBot
forwards to the operator. Nothing here identifies anybody: the code, the
incident and how many there were.

An event, not a direct message: the bots do not hold the operator's chat and
should not learn to. ManagerBot already decides what is worth a message
(its ALERT_LEVELS) and already handles the operator never having started a
bot, so this rides the rail that exists.

### `note_occurrence()`

Remember that this problem was shown, to be written out on the next
pass -- and interrupt the operator if it is the kind that cannot wait.

Keyed by incident, so a message redrawn with the same problem still on it
is one occurrence here as well as one line in the log.

- At `logging.getLogger(__name__).debug("Could not alert on %s", code, exc_info=True)`:
  An alert that fails must never cost the record it was alerting
  about -- the row below is the thing that has to survive.
- At `while len(_problem_buffer) > MAX_BUFFERED_PROBLEMS:`:
  A ceiling rather than unbounded growth: if the database is unreachable
  for an hour the buffer must not become the reason the container dies.
  The oldest go, because the newest are the ones somebody might still be
  looking at a Report button for.

### `_flush_problems_now()`

Blocking; call through asyncio.to_thread. Takes the whole buffer in one
swap so a problem arriving mid-flush lands in the next window rather than
being lost.

- At `leftover = OrderedDict(list(batch.items())[written:])`:
  Put back what is left, including this one, and stop: a database
  that refused one row will refuse the next forty too.

### `remember_chat_lang()`

The language a chat was last seen in, for labelling a report button on
a message that is already on its way out.

_At `DETAILS_AFTER = int(os.environ.get("PROBLEM_DETAILS_AFTER", "3"))`:_

When a fault gets the details button. Not every time: a download that timed
out once, or an upload Telegram refused once, is written down anyway, and
asking the person for more about it is noise (2026-11-13). The
button comes with the problems that need the person's side of the story
(problems.WANTS_DETAILS), and with any other fault once it keeps happening to
the same chat.

### `offers_details()`

Whether this problem, in this chat, gets the details button: always for
problems.WANTS_DETAILS, never for one that explains itself, and for any
other fault from its DETAILS_AFTER-th time in this chat within
DETAILS_WINDOW_SECONDS. `count` records this occurrence; `ahead` asks about
one not counted yet.

### `report_markup()`

`markup` with a details row added, or a keyboard holding only that row --
when offers_details says so for this chat. Left as it is with the buttons
switched off, and for a keyboard that already has one or is a reply
keyboard.

### `_with_logged_note()`

The same message, with one line saying an anonymous report was saved --
and, when a details button is under it (`offered`), that adding more is up
to them. A message that already carries either line (an edit of one sent
before) has it replaced, not doubled: the button can come or go between
the two.

Above the code line, never below it: problems.find_code only recognises a
code at the very end, and a note underneath it would make every one of
these messages uncoded to everything that reads them afterwards -- the
Report button included.

Only on problems that offer a button, which is where somebody is being
asked to decide something. The unknown-command answer explains
itself and does not need a paragraph about logging under it.

- At `return f"{body}\n{note}\n{problems.MARK}{code}"`:
  Three short lines -- what happened, that it is saved, the code -- with no
  blank lines between them: the longer version was found too long.

### `_button_incident()`

The incident a Report button made elsewhere already carries -- a
conversion's ending, or a crash notice, whose incident is also in the operator's alert or beside the traceback.

_At `problem_log = logging.getLogger("problems")`:_

Every problem shown, one line each: when (the log line's own time), which
code, which incident, and whether it offered a Report button. Nothing about
who it happened to. Into bot.log with everything else, into problems.log of
its own wherever the bot writes log files, and into the lines ManagerBot's
/problemlog reads either way (family_link.keep_recent_log_lines).

_At `_incidents: "OrderedDict[tuple, str]" = OrderedDict()`:_

One incident per occurrence. A message redrawn with the same problem still on
it -- a status line refreshed, a menu re-rendered -- keeps the incident it was
first shown with, so it is logged once and a Report button on it still
matches.

### `note_problem()`

A message on its way out. If it ends in a problem code: log it, write it down for the operator, and give it a details button if the problem deserves
one in this chat (offers_details). Returns (code, incident, text) -- the text possibly grown by a line
saying the problem is already recorded -- or (None, None, text) for a
message with no code.

- At `note_occurrence(code, incident, datetime.now(timezone.utc))`:
  Every problem, not only the ones somebody chooses to report, and not
  only the ones that offer a button. Buffered -- see note_occurrence.

### `_log_problems_to_file()`

problems.log beside bot.log, wherever setup_logging() writes files.

### `attach_problem_reports()`

Log every problem this bot shows, and put a details button under the ones
that deserve it (offers_details). Call once in main(), after the application is built.

Done by giving the bot object a subclass of its own class whose send, edit
and pop-up methods look at the text and hand over. python-telegram-bot
objects refuse ordinary attribute assignment once built, so the class is
swapped with object.\__setattr_\_; if that is ever refused the codes still
show, and the log says the logging and the buttons are off.

#### `attach_problem_reports.answer_callback_query()`

- At `text = kwargs.get("text", args[0] if args else None)`:
  A pop-up cannot hold a button, but it is a problem shown all the
  same -- so it is logged and recorded like any other, and only the
  button is missing.

### `_send_report_to_owner()`

Somebody attached their own details to a problem.

\*\*Urgent problems only.\*\* Asked for in 1.7.0: when somebody reports with additional details, the highest-level errors get a new message saying details were provided, and the others go along with the daily report. A fault somebody bothered to sign is worth
reading; it is not worth a notification at four in the morning, and the
nightly report already lists it with a 🙋 beside it.

It says that details were attached; it does not say what they are. The standing rule is that nothing a bot pushes at the operator identifies a user, and somebody consenting to be identified does not repeal it -- what
the consent bought is the operator being \*able\* to look. /report &lt;incident>
in ManagerBot is the looking.

_At `_open_dialogs: "OrderedDict[tuple, int]" = OrderedDict()`:_

One open "send the details?" question per problem per chat. Two taps
on the same Report button used to put two of them in the chat, and answering
one left the other sitting there still offering to send -- which reads, a
minute later, as the bot having sent the report on its own.

### `problem_report_callback()`

Report -> what a report sends, with Send and Cancel -> sent, or not.

- At `when = getattr(query.message, "date", None)`:
  When the message with the problem was sent. A message too old for
  Telegram to hand back has a date of 1970, which is no use to anyone.
- At `await _close_open_dialog(context.bot, chat_id, incident)`:
  A second tap replaces the first question rather than adding one --
  see \_open_dialogs. Into the chat the button was in, so it works in a
  group as well as in private.

### `_tell_about_crash()`

A crash used to leave the person with no answer at all. Now they are
told it failed on the bot's side, with a code and a report button whose
incident is the one logged beside the traceback.

### `error_handler()`

Register with Application.add_error_handler in each bot's main() --
this is PTB's global hook for exceptions that escape a handler callback
uncaught (i.e. actual crashes, not the try/except'd, user-facing errors
already handled inline elsewhere).

A dropped long poll reaches here too, and is not a crash -- see
is_transient_network_error above. Nor is a single Conflict from the long
poll -- see note_conflict.

- At `if update is None and is_transient_network_error(context.error):`:
  Only a failure with no update behind it is the long poll. A network
  error raised while handling somebody's message means that person asked
  for something and did not get it, and that is not a blip however
  transient its cause: it is counted, logged with its traceback, and
  reported like any other failure.
- At `if update is None and isinstance(context.error, Conflict):`:
  The same reasoning for a Conflict: only the poll's own is counted, one
  raised while handling an update is reported like any other failure.

## Active-user tracking, buffered

This used to be one INSERT -- and, before the connection pool, one whole new
Postgres connection -- on \*every single update\*, purely so /status could say
how many people used the bot in the last hour.

Nothing reads an individual row. Both queries are COUNT(DISTINCT user_id)
over a window, so recording a user once per flush window is exactly as
accurate as recording them forty times, and writes a fraction as much. Ids
collect in a set here and go out as one multi-row INSERT per window.

_At `ERASED_SKIP_SECONDS = 600`:_

People who have just used /deletemydata, and when (time.monotonic()). The
confirming tap is itself an update, buffered above before the handler ran,
and the flush would otherwise write the person straight back: an activity
row and a settings row the erase had just removed. For
ERASED_SKIP_SECONDS after an erase the flush leaves them out. (Ten minutes is
far past one flush window; somebody who comes back after that is recorded
again, as anybody new would be.) Bounded like the flood tables.

_At `_activity_lock = threading.Lock()`:_

Held by a flush while it writes, and by note_erased while it records an erase:
so a flush that began before the erase has finished before the erase runs.

### `note_erased()`

Blocking; call through asyncio.to_thread, BEFORE the erase. Takes the
person out of the activity buffer and has the flush skip them for
ERASED_SKIP_SECONDS. Waits for a flush already writing, so that none can
land after the erase that follows.

### `_flush_activity_now()`

Blocking; call through asyncio.to_thread. Takes the whole buffer in one
swap so an update arriving mid-flush lands in the next window rather than
being lost. People erased within ERASED_SKIP_SECONDS are dropped from the
batch, not carried to the next one.

- At `_activity_buffer |= batch`:
  Put them back: a database blip should cost a delayed count, not a
  wrong one. Union rather than assignment, so ids that arrived while
  this was in flight survive too.

### `note_active()`

Count `user` as somebody the bot served: the active-user counts (and
through them ManagerBot's people counts), the usage window's people, and
the clock that frees their cached state once they go quiet. track_activity
does it for every update from a private chat; in a group chat the bot's
own handlers call it for the people they actually serve.

### `track_activity()`

Register as a TypeHandler(Update, track_activity, ...) in a group of
its OWN, above every other group, so /status can report active users
hourly / since this process started.

Its own group is not a style choice. python-telegram-bot runs at most one
handler per group -- the first match wins and the rest of that group is
skipped -- and this one matches every update there is. Anything sharing a
group with it therefore never runs at all, silently, which is exactly
what happened to the four bots' donate_custom_amount_received and to
AnonBot's edited_message handler until v1.2.2R.

Does no I/O at all now -- it adds an int to a set, and the job registered
by attach_maintenance() writes the window out. It also stamps this user's
per-user cache with the time, which is what lets that same job drop the
caches of people who have not been seen in a long while.

- At `_not_counted.set(is_not_counted(getattr(update.effective_user, "id", None)))`:
  Set for every update, so one person's never carries over to the next in the same task.
- At `if user:`:
  Most of a group's messages are not meant for the bot: DownloaderBot
  reads a whole group for its links. So they count nobody as having
  used it (its group handlers call note_active for the people they
  serve), and they do not bury its live messages: a progress message
  in a group is edited where it is, as a group bot's are, instead of
  being sent again under every reply and left behind half done.
- At `live_message.note_update(update)`:
  Which message is newest in this chat, so a live message knows whether
  it is still the last thing on screen. Before the user check on purpose:
  it is a property of the chat, not of who sent it.
- At `have = context.user_data.get(MENU_SIGNATURE_KEY)`:
  A per-chat menu is frozen when it is set; see refresh_chat_menu. Only
  people who have one are checked, and the signature is updated before the
  call goes out, so a burst of updates schedules it once.

## Not starting something an update is about to take away

Two different reasons a bot should decline to begin slow work, and they want
different sentences:

```text
A stop signal has already arrived. The container is going in seconds and
there is nothing to arrange -- "send it again in a moment" is the whole
truth, and lifecycle.busy() covers anyone already inside a job.
```

```text
An update has been announced from ManagerBot. That can last across
several deploys, so the honest answer carries an estimate and a promise:
the person is written down, and /finishupdates goes back to them when it
is over. Being told "try again in a moment" and finding it still shut
four times running is worse than being told to wait ten minutes once.
```

Every handler that starts something slow calls this first and shows what it
returns instead of starting.

### `refuse_new_work()`

The sentence to answer with instead of starting slow work, or None
when there is no reason not to start it.

Writes the person down when -- and only when -- an update has been announced, because that is the only case where somebody is coming back to
tell them it is finished.

## Bounding the process's own memory

python-telegram-bot keeps a user_data dict per user id for the lifetime of
the process and never evicts it. Each one is small (a cached language, a
menu's worth of state) but the count only ever goes up, so on a bot that
runs for months this is a genuine slow leak -- and on a container sized to
the smallest plan that fits, a slow leak is an eventual OOM restart.

Anyone idle for USER_DATA_TTL_HOURS gets theirs dropped. The only thing lost
is a cache: their language is re-read from the database on their next
message, and no bot in the family keeps anything in user_data that has to
outlive a conversation.

### `_prune_user_data()`

Drop what is kept for people idle for USER_DATA_TTL_HOURS. State with no
\_last_seen at all is not "idle forever": a handler that cleared user_data
had just run for them (2.1.5 dropped a pack session opened a minute before
the hourly pass that way) -- it is stamped now and kept.

### `attach_maintenance()`

One line in each bot's main(), next to family_link.attach().

Flushes the activity buffer on a timer and again on shutdown, and keeps
per-user memory from growing forever. Degrades to "no buffering, no
pruning" rather than failing if the bot has no job queue.

- At `app.job_queue.run_repeating(`:
  Offset by a minute so a redeploy does not have all five bots writing a
  usage row into one second, and so the first window is a real window
  rather than however long the process happened to have been up.

### `flush_on_shutdown()`

Register as Application.post_stop. Writes out whatever the last window
collected -- without this, a redeploy silently loses up to a minute of
activity counts -- and lets the connection pool's worker threads go.

### `detect_host_environment()`

Best-effort guess at where this process is running. Railway sets a
handful of its own env vars on every deploy, so their presence is a
reliable cloud signal; anything else is assumed to be a local machine.

## What this process actually costs to run

A usage-billed host charges for resident memory and CPU seconds, and until
this was on /status there was no way to tell whether a change to either had
helped, hurt, or done nothing. Every number here is read from the kernel,
free, and only when someone asks.

_At `_service_rss_seen = 0`:_

The largest memory reading this process has seen, for when the cgroup keeps no peak
of its own (kernels before 5.19 have no memory.peak).

### `footprint_numbers()`

The same four readings process_footprint() prints, as numbers, and "scope":
"service" when they are the whole service's (the cgroup's counters, workers
included) and "process" when they are only this process's.

Separated out because a sentence is what a person wants and a number is
what a threshold and a database row want, and building the sentence twice
to parse it back would be the kind of thing that breaks silently in one
language and not another.

Any of them may be None: /proc is Linux, the cgroup file is a container,
and `resource` is not on Windows. A missing number means "not measurable
here", never zero -- zero would read as "free" to every caller.

- At `scale = 1 if sys.platform == "darwin" else 1024`:
  ru_maxrss is kilobytes on Linux and bytes on macOS/BSD, and it
  is a high-water mark rather than a live reading -- so it stands
  in for the peak, and there is no live number to report.
- At `used = resources.memory_used_mb()`:
  Where the container's own counters can be read, they are the service's: this process, the
  conversion worker, ffmpeg and the big-file children, which is what the limit is held
  against. /proc/self is only the bots' process, a fraction of it.

### `process_footprint()`

One line: resident memory, its high-water mark, and CPU seconds burned
since startup. Read /proc where it exists (Linux, which is what the
deployed containers are) and fall back to getrusage elsewhere.

## What it costs over time, and when that stops being normal

/status answers "what is this process using right now", which on its own
tells nobody whether right now is unusual. This keeps a rolling window --
updates handled, distinct people, and the four kernel readings -- writes one
row per window into family.usage_samples, and raises an event when a window
is far enough from the others to be worth a message at 3am.

Three things are worth being told about, and they are different questions:

```text
memory headroom   the container is close to the limit it will be killed
                  for crossing. The only one of the three that is an
                  emergency.
an activity spike a window with far more updates than the recent norm.
                  Could be a launch, could be one script. Either way it is better heard from the bot than from the bill.
monthly reach     how many distinct people used it in thirty days, which
                  is the number that decides whether the plan it is on is
                  still the right one. Slow-moving, so it is checked once
                  a day and only ever mentioned when it crosses.
```

Everything here is a count. No user ids, no chat ids, no text.

_At `USAGE_MEMORY_WARN_RATIO = float(os.environ.get("USAGE_MEMORY_WARN_RATIO") or 0.85)`:_

How full the container has to be before it is worth saying so. 0.85 rather
than 0.95: the point is to arrive before the OOM kill, not with it.

_At `USAGE_CPU_WARN_RATIO = float(os.environ.get("USAGE_CPU_WARN_RATIO") or 0.85)`:_

The same for CPU: a window whose CPU seconds come to this share of the service's vCPUs.

_At `USAGE_SPIKE_FACTOR = float(os.environ.get("USAGE_SPIKE_FACTOR") or 6.0)`:_

A window counts as a spike when it is this many times the median of the
recent ones AND clears the floor. The floor is what stops "2 updates, then
12" from being an incident on a quiet bot -- which, on a bot this quiet, is
most of the time.

_At `USAGE_MONTHLY_USERS_WARN = int(os.environ.get("USAGE_MONTHLY_USERS_WARN") or 400)`:_

Distinct people in thirty days, past which the operator is told once. Not a
limit and nothing is refused; it is the number that means "the smallest
plan that fits may no longer be the smallest plan that fits".

_At `SLEEP_AFTER_SECONDS = int(os.environ.get("SLEEP_AFTER_SECONDS") or 300)`:_

How long a host waits for silence before it stops charging for a container.
Railway sleeps a service after roughly five minutes with no \*outbound\*
traffic, so a gap between updates is only worth anything from the five-minute
mark onwards -- which is why this is subtracted from every gap rather than
the gaps simply being added up. Nothing in the bots sleeps yet; this measures
what sleeping \*would\* have saved, so the decision is made on this family's
own traffic rather than on a guess.

_At `USAGE_FAILURE_WARN_RATIO = float(os.environ.get("USAGE_FAILURE_WARN_RATIO") or 0.5)`:_

A window where more than this fraction of the jobs failed is worth being told
about -- a job being a conversion, a download, a pack edit: whatever the bot
is for. The floor is again what stops one failure out of one being an
outage.

_At `_USAGE_WINDOW_MEMORY = 24`:_

How many recent windows the spike test compares against, and how long an
alarm of one kind stays quiet after firing.

_At `_usage_last_update = time.monotonic()`:_

The gap clock. monotonic() rather than time(): this measures a duration, and
a clock that can be stepped by NTP would make one negative. The last update
is the real one, never moved at a window's end; the window's own start is
what keeps each window to its own share of a long quiet stretch.

### `_asleep_in_window()`

How much of this window, up to `now`, the host would have been asleep
for: the quiet since the last update, from SLEEP_AFTER_SECONDS into it,
and only the part of that inside this window.

### `note_usage_update()`

One update happened. Called from track_activity, so it is on the path
of every update there is -- it adds an int to a set and increments a
couple of counters, and must never do anything more expensive than that.

### `note_job()`

One unit of the thing this bot is for finished -- a conversion, a
download, a pack edit. Two counters and nothing else.

What is deliberately NOT here: what was converted, which link, whose it
was, or why it failed. The question this answers is "is the bot still
working", and that needs a ratio, not a record. Per-route detail already
lives where it belongs -- DownloaderBot's provider health, and every
bot's error log.

## Button taps, counted

How often is each button tapped? Measuring every button that information might be needed about was asked for (2026-10-12). The answer is a count per button per
day for the whole bot, and nothing about who tapped it: no user id, no chat id,
no token, no link, no job id, no pack name, no conversation id, no amount is ever
part of a key, a log line or a row. track_activity sees every update before any
handler does, so one hook counts every button of the four public bots (ManagerBot
has its own track_activity in monitoring.py, which does not count: its buttons are
the operator's). The counts are written with the usage sample, every
USAGE_SAMPLE_MINUTES (family.button_taps, family_link.record_taps).

What a key may be is decided here and only here (tap_key): the first part of the
callback data if it looks like a button's name, and the second part only for the
buttons whose second part is a choice from a short fixed list. Anything else (a
token, an id, a pack name, a number) never gets in.

- `TAP_MAX_KEYS = 150` — distinct keys between two flushes, "other" included

### `tap_key()`

The key under which a tap of a button with this callback data is counted.
Pure. The button's name, plus its choice for the buttons that offer a fixed
list of them ("help:full", "setlang:uz"); "other" for anything that is not
shaped like a button's name.

- At `return f"donate:{second}" if second in {str(amount) for amount in DONATE_STAR_OPTIONS} e …`:
  The Stars menu is a fixed list of amounts; only those are kept.

_At `_not_counted: "contextvars.ContextVar[bool]" = contextvars.ContextVar("mumu_not_counted" …`:_

The operator's own accounts are left out of every count here (2.1.7) -- button taps, the daily
totals and the usage samples' updates and people: ADMIN_ID (and each bot's \*\_ADMIN_ID) plus
STATS_IGNORE_IDS, a comma-separated list for any other account of the operator's. track_activity, first for
every update, says whose update this is; tasks the update starts (a conversion) inherit that.
Activity records stay as they are: broadcasts to "recently active" people read them.

### `note_tap()`

Count one tap (or one event, for the bots that count things that are not
callbacks) under `key`. No I/O; never raises. At most TAP_MAX_KEYS distinct keys
between two flushes: past that a new key is counted as "other".

_At `STAT_MAX_KEYS = 300`:_

Anonymous counts for infographics (family_link.record_stats): what was done,
never who, never finer than a day. Written with the usage sample, like taps.

### `note_stat()`

Count one thing done -- note_stat("link_site", "tiktok") -- for today's
anonymous totals. No I/O; never raises. Past STAT_MAX_KEYS distinct keys
between two flushes, a new key is counted as its metric's "other".

### `take_taps()`

The counts so far, and a fresh start. Called on the event loop, where
note_tap runs, so no tap lands in the dict being written.

### `_count_tap()`

Count the button this update is a tap of, if it is one. Cannot raise and
cannot wait: it is on the path of every update.

_At `_usage_cpu_last: "tuple[float, float] | None" = None`:_

The CPU reading at the last sample: (cpu_seconds, time.monotonic()). cpu_seconds is a running
total since the process, or the service, started, so a window's own CPU is the difference
between two samples.

### `_cpu_window()`

(CPU seconds used, seconds elapsed) since the previous sample, or None for the first
sample, for a reading that is missing, or for a total that went down (a restart began
it again). Called once per window.

### `usage_trend_line()`

One line for /status: what the recent windows looked like, so the
number above it has something to be compared with.

### `_monthly_users()`

Distinct people in the last thirty days, if this bot's db can say.

### `_check_usage_alarms()`

Blocking; runs in the same thread as the sample write.

- At `window = numbers.get("cpu_window")`:
  CPU, only where the reading is the service's own: the cgroup's counters, or this process
  when it holds every bot. And only from the one bot that reports memory (the others
  have it blanked), so a service is not told about five times.

### `_sample_usage_now()`

One window: write the row, then decide whether to say anything.

The counters are taken and reset first, so a slow database cannot make
the next window count this one's updates twice. `taps` is the button counts
of the window, swapped out by the job on the event loop (take_taps); a
caller that has none takes them here.

- At `global _usage_window_start`:
  The window ends with a gap in progress. Its share of this window is
  counted now, and the next window counts from here -- so a bot quiet for
  six hours reports each window's own part of it, not six hours in one.
  The gap itself goes on: the host would have gone to sleep once, five
  minutes in, not again at the start of every window. (Until 2.0.0 the
  clock was restarted here, and a bot quiet for days read as 67% asleep.)
- At `if taps is None:`:
  The button counts ride with the sample. Like the job counts above they were
  taken before the write, and if the write fails they are not put back: one
  window of counts is lost, which a count of taps can afford. The log says
  what kind of failure and nothing else.

### `_usage_sample_job()`

- At `taps = take_taps()`:
  Swapped out here, on the event loop where the taps are counted, and written
  in the same thread hop as the usage row.

## One person's update never holds up another's

python-telegram-bot handles updates one at a time unless told otherwise, and a bot whose
handler downloads a 2000 MB file or waits for a probe makes everybody else wait for it.
This lets updates of different people run at the same time and keeps each person's own
in the order they arrived -- which an album, whose parts are separate messages that build
on each other, needs. One person may have PER_USER_WAITING_MAX updates waiting; past that
their new ones are dropped and counted (never a successful payment: must_keep), so one
person cannot queue up without end.

_At `UPDATES_IN_MEMORY_MAX = int(os.environ.get("UPDATES_IN_MEMORY_MAX") or 256)`:_

And a crowd never holds more than UPDATES_IN_MEMORY_MAX updates in one bot's memory -- in its
queue, waiting their person's turn, or being handled (UpdateQueue); past that, polling waits
and the rest stay at Telegram, which keeps them 24 hours and hands them over in order. Measured
2026-11-09, five bots in one process, 2,000 people sending 20,000 commands at once: without a
bound python-telegram-bot made a task of every update the moment it was fetched, the process
went from 94 to 399 MB, and stayed there once it was over -- Python's allocator keeps the arenas
the few surviving objects are scattered over. With 256: 170 MB, and the crowd was answered a
little sooner; 200 people at once cost the same as before (134 MB). 0 switches the bound off.

### `waiting_updates()`

The updates of the same person (or chat) as `update` still waiting their turn behind it,
when the bot runs PerUserUpdateProcessor; [] otherwise.

### `UpdateQueue`

python-telegram-bot's queue of fetched updates, which holds polling back while this bot
already has `limit` updates in memory: in the queue, or with `processor` (waiting their turn
or being handled). See UPDATES_IN_MEMORY_MAX for why.

python-telegram-bot's fetcher takes every update off this queue at once and makes a task of
it, so the queue alone cannot bound anything: the count that matters is queue + processor.
The polling loop puts the updates of one getUpdates answer (at most 100) one by one, so the
bound can be passed by at most one answer's worth. Only updates wait: python-telegram-bot's
stop signal, and anything else that is not an Update, goes in at once.

#### `UpdateQueue.put()`

- At `await asyncio.wait_for(self.processor.done.wait(), timeout=1.0)`:
  A timeout too, so that a missed signal costs a second, never a stall.

### `PerUserUpdateProcessor`

Updates with the same key run one at a time, in the order they arrived; updates with
different keys run at the same time. The key is the person (`effective_user.id`), else the
chat (`effective_chat.id`); an update with neither runs at once, unordered.

The order is the order python-telegram-bot started them in: it starts one task per update
in the order it takes them from its queue, and each task takes the lock of its key with
nothing awaited before it, so the locks (first come, first served) are taken in that order.
`max_concurrent_updates` is large on purpose: waiting is cheap, and a small number would let
one person's queued updates take every slot.

#### `PerUserUpdateProcessor.__init__()`

- At `self._locks: "dict[tuple, list]" = {}`:
  key -> [lock, number of updates holding or waiting for it]; dropped at zero.
- At `self._waiting: "dict[tuple, list]" = {}`:
  key -> the updates still waiting their turn, oldest first (waiting()); dropped when empty.
- At `self.in_flight = 0`:
  Updates between arriving here and being done, and a signal each time one is done:
  what UpdateQueue holds polling back by.

#### `PerUserUpdateProcessor._process_in_turn()`

- At `close = getattr(coroutine, "close", None)`:
  Stopped while waiting its turn: the update's coroutine was never awaited.

#### `PerUserUpdateProcessor.waiting()`

The updates of `key` still waiting their turn behind the one running, oldest first:
StickerBot counts a run of pictures from them (its \_queued_items).
