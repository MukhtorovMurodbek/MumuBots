"""Translation strings for DownloaderBot's end-user-facing text (English,
Uzbek, Russian). Deliberately duplicated per bot -- same "no shared files
between bots" independence as shared_features.py -- but the STRINGS content
here is specific to this bot's own commands and flows.

Admin-only output (/dbdump, /status) is intentionally NOT translated -- see
build_status_text/error_summary/detect_host_environment in
shared_features.py, left as plain English since only the bot owner reads
them. Text baked into rendered card images (cards.py) is also left in
English -- Pillow's built-in bitmap font (ImageFont.load_default) has no
Cyrillic glyphs, so translating it there would just draw tofu boxes.

The keys below split into two groups:
  - "Shared" keys (donate flow, sibling-bot blurb) exist under the exact
    same names in every bot's i18n.py, since shared_features.py is
    duplicated byte-identical across the family and calls t() with these
    names regardless of which bot it's running in.
  - Bot-specific keys, everything below the shared block, for this bot's
    own bot.py strings only.
"""
import asyncio

import db

SUPPORTED_LANGUAGES = ("en", "uz", "ru")
LANGUAGE_LABELS = {"en": "English 🇬🇧", "uz": "O'zbekcha 🇺🇿", "ru": "Русский 🇷🇺"}

# What every /start shows, whether or not the user already has a language.
# Deliberately not part of STRINGS: it is trilingual on purpose, so there
# is no single `lang` to look it up under.
LANGUAGE_PROMPT = (
    "👋 Welcome! / Xush kelibsiz! / Добро пожаловать!\n\n"
    "Please choose your language / Iltimos, tilni tanlang / "
    "Пожалуйста, выберите язык:"
)

STRINGS = {
    "en": {
        "transfer_waiting": "⏳ Another big file is being moved. Yours starts right after.",
        "quota_hour": (
            "You've reached the limit of {limit} downloads an hour ({used} used). The next one frees up in about {minutes} min."
        ),
        "quota_day": (
            "You've reached today's limit of {limit} downloads ({used} used). It frees up in about {minutes} min."
        ),
        "quota_donor_hint": (
            "Need more? Anyone who has ever donated, any amount, gets a much higher limit for good: /donate"
        ),
        "flood_wait": "You're going faster than I can keep up with — give it about {seconds} second(s) and carry on.",
        # ---- shared keys (same name in every bot's i18n.py) ----
        "sibling_blurb": "Also part of this bot family, see below \U0001f447",
        "donation_nudge": (
            "💙 Find this bot useful? /donate helps pay for hosting. Totally optional."
        ),
        "donate_unknown_currency": 'Unknown currency "{currency}" — try xtr or usd.',
        "donate_currency_not_configured": "{currency} donations aren't set up on this bot yet — try Stars instead.",
        "donate_invalid_amount": "That's not a valid amount — try e.g. /donate 500 or /donate 5 usd.",
        "donate_prompt": (
            "Thank you! It goes toward hosting costs. Pick an amount, or Custom for your own."
        ),
        "donate_custom_button": "✏️ Custom {symbol}",
        "donate_too_many_stars": "That's a lot of stars! Keep it under {max} ⭐ per donation.",
        "donate_out_of_range": "{currency} donations need to be between {lo} and {hi} {symbol}.",
        "donate_invoice_title": "Contribute to hosting",
        "donate_invoice_description": "Goes towards what this bot costs to run, and adds {credited} ⚡ of credit to your balance for conversions in ConvertBot.",
        "donate_invoice_label": "Hosting contribution",
        "donate_invoice_description_fiat": 'A one-time voluntary donation towards hosting costs. Thank you!',
        "donate_prompt_credit": 'Stars you pay become ⚡ credit for conversions in ConvertBot. Your next {left} ⭐ earn {each} ⚡ each ({mult}×): {rate} ⚡ of ordinary credit plus a bonus that expires {days} days after payment. ⚡ can NOT be withdrawn or turned back into Stars.',
        "donate_prompt_credit_base": 'Stars you pay become ⚡ credit for conversions in ConvertBot, {rate} ⚡ per ⭐. ⚡ can NOT be withdrawn or turned back into Stars.',
        "donate_invoice_error": "⚠️ Telegram wouldn't create that invoice: {error}",
        "stars_unit": "Stars",
        "donate_custom_ask": "How many {unit} would you like to donate? Reply with a number.",
        "donate_invalid_amount_retry": "That's not a valid amount — send /donate to try again.",
        "donate_thanks": "🙏 Thank you for the {amount} ⭐ — genuinely appreciated!",
        "topup_thanks": "🙏 Thank you for donating {stars} ⭐ — it helps keep the bots running.\n\nAs a thank-you, you've received {total} ⚡ of credit in {convert_bot} to use on file conversions. Your credit there: {balance} ⚡.",
        "topup_thanks_bonus": 'Of that, {bonus} ⚡ is bonus credit and expires on {date}.',
        "credit_cannot_be_withdrawn": '⚡ is credit for conversions in ConvertBot, and can NOT be withdrawn or turned back into Stars. A problem with a payment? /paysupport',
        "topup_refunded": "⚠️ Your payment of {stars} ⭐ went through, but a problem on my side kept the ⚡ from reaching your balance, so I've refunded it: the {stars} ⭐ are back in your account. Please try again in a few minutes.",
        "topup_failed": "⚠️ Your payment of {stars} ⭐ went through, but a problem on my side kept the ⚡ from reaching your balance. Nothing is lost: the owner has been told and will add it by hand. To follow up: /paysupport",
        "paysupport_text": '💳 Help with a payment\n\nPayments are FINAL: Stars are NOT refunded, and ⚡ credit can NOT be withdrawn or turned back into Stars. The one exception: a payment the bot could not turn into credit is refunded to you automatically, in full.\n\nIf a payment went wrong — you were charged and no credit arrived, or you were charged twice — write to {contact} with the date, the amount and your Telegram id, {user_id}. It will be checked and put right with credit.\n\n/balance lists every payment and what it added.',
        "report_button": '🐞 Add my details',
        "problem_logged_note": "Anonymous report is saved",
        "report_disclaimer": "📨 Add your details to this report?\n\nAlready saved, without you: the bot, error code {code}, incident {incident}, the time and the version.\n\nSend adds your Telegram ID, @username, language, and whether this is a private chat or a group. Never your messages or files.\n\nIt helps find your case in the logs. Your choice.",
        "report_send": '📨 Send my details',
        "report_cancel": '✖️ Cancel',
        "report_sent": "✅ Thank you — added to incident {incident}.",
        "report_already": "Your details are already on incident {incident}.",
        "report_cancelled": "Cancelled — nothing about you was sent.",
        "report_failed": "⚠️ The report couldn't be sent right now. Please try again later.",
        "report_invalid": 'This button no longer works.',
        "crash_notice": "⚠️ Something went wrong on my side, so that wasn't done. Please try again.",
        "sandbox_notice": "🧪 Test mode — no real Stars were charged for this.",
        "balance_header": "⚡ Your balance: {balance}",
        "balance_totals": "Paid {paid} ⭐ in total · credited {credited} ⚡ · spent {spent} ⚡",
        "balance_rate": 'Your next {left} ⭐ earn {each} ⚡ each ({mult}×).',
        "balance_rate_base": '1 ⭐ buys {rate} ⚡.',
        "balance_bonus_line": 'Of that, {bonus} ⚡ is bonus credit — {soon} ⚡ of it expires on {date}.',
        "balance_recent": "Recent:",
        "balance_empty_hint": "/donate adds credit whenever you want some.",
        "bot_short_description": (
            "Paste a public Instagram, TikTok, Pinterest, Reddit or X link."
        ),
        "bot_description": (
            "Paste a link to a public post and the media comes back. No command needed.\n"
            "\n"
            "Instagram, TikTok, Pinterest, Reddit and X. Carousels arrive whole, and a text post "
            "comes back as a rendered card.\n"
            "\n"
            "It fetches what is already public — it is not a way around a private account. "
            "English, Uzbek and Russian. /privacy says what it keeps."
        ),
        # ---- shared policy keys (/privacy, /terms, /deletemydata) ----
        "privacy_heading": "🔒 Privacy",
        "privacy_kept_heading": "What this bot keeps:",
        "privacy_stored": (
            "• your Telegram user id, your language, and your caption and quality settings\n"
            "• the links you send: each one while it is fetched, then the last five, so the "
            "buttons under those downloads work. They are dropped after 12 hours without using "
            "the bot\n"
            "• the last thing it sent you, for 10 minutes, so the buttons under it and the same "
            "link sent again are answered without fetching it twice. Your next link, /deletemydata "
            "or a restart deletes it sooner, and it is deleted no later than 12 hours after the "
            "download\n"
            "• references to files the bot has already sent (Telegram's file IDs), filed under a "
            "scrambled name of the post and not under who asked for it. They let the bot send the "
            "same post again without downloading it. Before re-using one, the bot checks at least "
            "every 12 hours that the post is still public. References nobody uses are deleted "
            "after 90 days\n"
            "• one row per download — only when, which is what the daily allowance counts. "
            "Rows are deleted after 2 days\n"
            "• a timestamp each time you use the bot, so its owner can tell whether anyone is "
            "using it\n"
            "• how many times each button is tapped, counted per day for the whole bot, with nothing about who tapped it\n"
            "• a record of any donation: the amount and Telegram's payment id\n"
            "• whatever the bot is in the middle of doing with you, until it is finished\n"
            "• in a group chat it was added to: the group's id and its /settings, until the bot "
            "leaves the group. It looks at every message there for a link and keeps nothing from "
            "the others; a link it downloads counts as the poster's, like one sent here (the "
            "allowance row, and the last download for 10 minutes). Nothing else about the group's "
            "members\n"
            "\n"
            "Apart from those 10 minutes the media itself is not kept. It is fetched, sent to you, "
            "and deleted."
        ),
        "privacy_problems_heading": "When something goes wrong:",
        "privacy_problems": (
            "Every error this bot shows anybody is written down by itself: the error code, an "
            "incident number, when it happened and the bot's version. Nothing in that is about "
            "you — not your id, not your name, not what you sent. A fault nobody reports is "
            "still a fault, which is why it does not wait to be asked.\n"
            "\n"
            "\"Add my details\" under an error offers to attach four things to that one "
            "incident: your Telegram user id, your @username if you have one, the language you "
            "picked, and whether the chat is private or a group. All four are named on screen "
            "first, and nothing is sent unless you tap Send. What you wrote and the files you "
            "sent are never part of it.\n"
            "\n"
            "Those four are cleared after 30 days on their own, and /deletemydata clears them "
            "straight away. The record of the error stays — it still happened — it just stops "
            "saying who hit it. The records themselves are deleted after 180 days."
        ),
        "privacy_seen_by_heading": "Who else sees it:",
        "privacy_seen_by": (
            "• Telegram, which carries every message both ways and sets its own terms\n"
            "• the hosting provider this bot runs on, and the database it writes to"
        ),
        "privacy_others": (
            "• the site you linked to, and the download services this bot falls back on when a "
            "site will not answer it directly. They get the link and see a request from this "
            "bot's server rather than from you; what they log is their business, under their own "
            "policies and not this one."
        ),
        "privacy_kept_for_heading": "How long it stays:",
        "privacy_kept_for": 'Settings and anything the bot is holding for you stay until you erase them or stop using it. Counted use is dropped after about three months. Payment records and your ⚡ balance are kept longer, because a dispute about a payment is settled against them.\n\nNothing here is sold, rented or used for advertising, and nothing goes to anyone not named above.',
        "privacy_your_choices": (
            "What you can do:\n"
            "/deletemydata — erase what this bot holds on you\n"
            "/terms — what the bot may be used for\n"
            "\n"
            "Blocking the bot in Telegram stops it talking to you but erases nothing, so send "
            "/deletemydata first if you want both."
        ),
        "terms_heading": "📜 Terms",
        "terms_use": (
            "Use it for what it is for, within the law and within Telegram's own terms. Do not "
            "use it to harass anyone, and do not drive it past the limits it sets — an account "
            "doing either is blocked."
        ),
        "terms_specific": (
            "Downloads: this fetches what is already public and hands it to you. It is not a way "
            "around a private account and does not try to be one. What you do afterwards with "
            "someone else's photo, video or post is between you, them and the law — downloading "
            "something does not make it yours. There is a daily allowance per person, so that one "
            "person cannot make the bot unusable for everybody else."
        ),
        "terms_money": 'Money: /donate is voluntary and goes towards what the bots cost to run. A payment in Stars also adds ⚡ credit for conversions in ConvertBot: 2 ⚡ per ⭐, or 6 for your first 500 Stars ever and 4 for the next 500. The part above 2 is bonus credit and expires 90 days after the payment. Payments are FINAL: Stars are NOT refunded, and credit can NOT be withdrawn or turned back into Stars. The one exception: a payment the bot could not turn into credit is refunded automatically, in full. Telegram handles every payment and the bot never sees a card number. A payment that went wrong: /paysupport.',
        "terms_no_warranty": (
            "No promises: one person runs this, it is free, and it can be slow, wrong, or off "
            "entirely without warning. Keep your own copy of anything that matters."
        ),
        "policy_full_text": "Full text: {url}",
        "policy_contact": "Questions, complaints or a data request: {contact}",
        "delete_data_confirm": "⚠️ This erases what this bot holds on you. There is no undo.",
        "delete_data_consequences": 'Your language, your caption and quality settings, and every record of what you downloaded and when all go. Your daily allowance resets with them. It also deletes the last download if it is still being held; nothing older was ever kept here.\n\nDonation records stay, without your username, because a dispute about a payment is settled against them. Your ⚡ balance stays too, and is still yours if you come back.',
        "delete_data_button_yes": "🗑 Erase it",
        "delete_data_button_no": "↩️ Keep my data",
        "delete_data_kept": "Nothing was erased.",
        "delete_data_done": (
            "🗑 Done — {rows} record(s) erased.\n"
            "\n"
            "Send /start whenever you like; the bot will treat you as new."
        ),
        "delete_data_failed": (
            "Couldn't erase that just now — something went wrong at my end. Please try again in "
            "a few minutes."
        ),
        "language_set_confirmation": "✅ Language set to English.",
        "cancel_header": "\u274c Cancelled:",
        "cancel_nothing": "Nothing to cancel — I wasn't waiting on anything from you.",
        "cancel_ask": "What should I stop? Here's what I'm waiting on:",
        "cancel_kept": "Alright — nothing cancelled.",
        "cancel_reply_box_freed": "Your reply box is free again.",
        "cancel_button_all": "❌ All of it",
        "cancel_button_none": "↩️ Nothing, keep going",
        "cancel_button_donation": "💸 Donation amount",
        "cancel_item_donation": "the donation amount I asked you for",
        "cancel_item_stale_prompt": "a leftover prompt that was still waiting on an answer",
        "feedback_ask": (
            "💬 What would you like to tell the person who runs these bots? Reply to this message.\n\nIt's sent without your name, so they can't answer. /cancel to stop."
        ),
        "feedback_placeholder": "Your feedback",
        "feedback_text_only": "Please send text as a reply to this message, or /cancel.",
        "feedback_thanks": "✅ Thank you — sent anonymously.",
        "feedback_limit": "That's {per_day} today, the daily limit. Thank you — write again tomorrow.",
        "feedback_failed": "Not sent — something went wrong on my side. Please try again later.",
        "cancel_button_feedback": "💬 Feedback",
        "cancel_item_feedback": "the feedback I asked you for",
        "help_brief": (
            "How it works:\n\n• Paste a link and I send back what's behind it — no command needed. Instagram, TikTok, X, Pinterest and Reddit.\n• /settings: the caption, the file quality, and large files (over 48 MB, for ⚡ credit).\n• Ideas or problems: /feedback\n\nMain commands: /settings · /balance · /cancel\nEvery command: the full guide, below or /fullhelp.\n\n"
        ),
        "help_full_button": "📖 Full guide",
        "help_brief_button": "↩️ Short version",
        "help_page_everything": "The full guide: /fullhelp",
        "help_page_gone": "That help page no longer exists. Send /help to see the current ones.",
        "help_btn_download": "🔗 Sending a link",
        "helppage_download": (
            "🔗 Sending a link — how downloading works\n"
            "\n"
            "Paste a link to a post — no command needed — and I send back what's in it:\n"
            "  - Instagram: reels, photos, whole carousels\n"
            "  - TikTok: videos without the watermark, photo slideshows\n"
            "  - Twitter/X: video and photos, or a clean picture of a text post\n"
            "  - Pinterest: the pin, at full size\n"
            "  - Reddit: the media of a media post, or a picture of a text post\n"
            "\n"
            "{quota}\n"
            "• A download counts when it starts, even if it then fails. The 📄/🖼 button and the 📄 PDF / 🗜 ZIP buttons under a delivery count as one download each. The same link sent again while I'm still fetching it gets a 👌 and doesn't count.\n"
            "• Up to {waiting} of your links can be on the way at once; I fetch {at_once} at a time, and the rest wait their turn.\n"
            "\n"
            "Size: free up to {free_mb} MB. {large}\n"
            "\n"
            "Under a delivery:\n"
            "• \"📄 Get it as an uncompressed file\" (or \"🖼 Get it compressed instead\") sends the same post again in the other quality; each button works once.\n"
            "• Under an album: 📄 PDF puts its photos in one PDF, 🗜 ZIP puts everything in one archive. Up to {merge_items} files and {merge_mb} MB.\n"
            "• /settings: the caption, the quality, and large files.\n"
            "\n"
            "Example: paste a TikTok link like https://www.tiktok.com/@name/video/123 — the video comes back without the watermark."
        ),
        "helppage_download_quota": "It's free, up to {per_day} downloads in any 24 hours and {per_hour} in any hour. The count looks back from now; it doesn't reset at midnight. When you reach it, I say when the next one frees up. Anyone who has ever paid through /donate, any amount, gets {donor_day} a day and {donor_hour} an hour, for good.",
        "helppage_download_quota_off": "It's free.",
        "helppage_download_large": "Bigger, up to {max} MB, only if you turn on large files in /settings, in a private chat: ⚡{first} up to 100 MB, and each 100 MB after that adds less (⚡{top} for {max} MB). The ⚡ is taken only once the file has arrived, never if it fails.",
        "helppage_download_large_off": "Anything bigger is refused.",
        "helppage_settings": (
            "⚙️ /settings — how your downloads arrive\n"
            "\n"
            "/settings shows your switches and how each is set. Tap one to change it; it stays that way for every later download.\n"
            "• Caption (on at first): a \"via @{username}\" line under each download. Also /caption on or /caption off.\n"
            "• Lossless (off at first). On: downloads come as files, exactly as the source had them, with no Telegram compression — but they don't play or show in the chat until you open them. Off: they come as photos and videos that play in the chat, compressed by Telegram. Also /lossless on or /lossless off. For one download only, use the 📄/🖼 button under it instead.\n"
            "{large}\n"
            "Example: send /lossless on, then an Instagram link: the photos come as files, at full quality."
        ),
        "helppage_settings_large": "• Large files (off at first). On: a download over {free_mb} MB is still fetched, up to {max} MB, and sent as a file for ⚡ credit — ⚡{first} up to 100 MB, ⚡{top} for {max} MB — taken only once it has arrived. Off: anything over {free_mb} MB is refused, and nothing is charged. In a private chat only.",
        "privacy_feedback_heading": "If you send /feedback:",
        "privacy_feedback": (
            "What you write reaches the person who runs these bots as a Telegram message, with "
            "which bot it came from and your language — not your name, your username or your id. "
            "Only they read it, and they cannot reply to it. A copy is kept in the bots' "
            "database for 30 days and then deleted."
        ),
        # ---- downloader_bot-specific keys ----
        "start_greeting": (
            "Hey! Send me a link (Instagram, TikTok, Pinterest, Reddit, or "
            "Twitter/X) and I'll grab it for you.\n\n"
        ),
        "help_text": "Paste a link any time — no command needed:\n  - Instagram: reels, photos, whole carousels\n  - TikTok: videos without the watermark, and photo slideshows\n  - Twitter/X: video and photos, or a picture of a text post\n  - Pinterest: the pin, at full size\n  - Reddit: the media, or a picture of a text post\n\nUnder an album: 📄 PDF puts its photos into one PDF, 🗜 ZIP puts everything into one archive.\n\nCommands:\n/settings - caption, quality and large files (over 48 MB, for ⚡)\n/caption on|off - the \"via @{username}\" caption\n/lossless on|off - downloads as uncompressed files\n/cancel - stop something I'm waiting for\n/feedback - write to whoever runs these bots (anonymous)\n/donate - help pay for hosting (optional)\n/balance - your ⚡ credit\n/paysupport - trouble with a payment\n/en, /uz, /rus - switch language (or /language)\n/privacy - what I keep about you\n/terms - what I may be used for\n/deletemydata - delete everything I hold on you\n/start - start over\n/fullhelp - this guide (/help - the short one)\n\n⚠️ Payments are final — Stars are NOT refunded, and ⚡ credit can NOT be withdrawn.\n\n",
        "caption_state_on": "ON",
        "caption_state_off": "OFF",
        "settings_heading": "⚙️ Your settings. Tap to change any of them.",
        "caption_status": "Caption is currently {state}.",
        "caption_turned": "Download caption turned {state}.",
        "caption_toggle_answer": "Caption turned {state}.",
        "lossless_state_on": "ON",
        "lossless_state_off": "OFF",
        "lossless_status": "Lossless is {state}.\n\nON: downloads come as original files — no compression, but no preview in the chat.\nOFF: photos and videos that play in the chat, compressed by Telegram.",
        "lossless_turned": "Lossless downloads turned {state}.",
        "lossless_toggle_answer": "Lossless turned {state}.",
        "large_state_on": "ON",
        "large_state_off": "OFF",
        "large_status": "📦 Large files are {state}.\n\nON: downloads over {free} MB, up to {max} MB, come as files — ⚡{per} up to 100 MB, ⚡{top} for {max} MB. Charged only once the file arrives.\nOFF: anything over {free} MB is refused; nothing is charged.",
        "large_toggle_answer": "Large files: {state}.",
        "large_files_turned_on": "📦 Large files are on. Send the link again — ⚡{per} up to 100 MB, charged once it arrives. /settings turns it off.",
        "large_files_turn_on_button": "📦 Turn on large files",
        "large_files_off": "📦 This one is over {free} MB. Switch on large files below (or in /settings), then send the link again — ⚡{per} up to 100 MB.",
        "large_files_private_only": "📦 This one is bigger than {free} MB. Files that big only come in a private chat with me — send me the link there.",
        "large_files_no_credit": "📦 This one is bigger than {free} MB, which costs at least ⚡{per}, and your balance is ⚡{balance}. Top up with /donate, then send the link again.",
        "large_files_fetching": "📦 Over {free} MB — getting it as a large file (⚡{per} up to 100 MB, charged once it arrives).",
        "large_files_too_big": "📦 This one is bigger than {max} MB, the most Telegram lets me send. Try a shorter clip.",
        "large_files_short": "📦 It's {mb} MB (⚡{price}) and your balance is ⚡{balance}, so nothing was charged. Top up with /donate and send the link again.",
        "large_files_sending": "📦 {mb} MB is here. Sending it...",
        "large_files_send_failed": "📦 I got it, but couldn't send it to you. The ⚡{price} is back — your balance is ⚡{balance}.",
        "large_files_charged": "📦 {mb} MB, sent. ⚡{price} for it — your balance is now ⚡{balance}.",
        "download_credit_caption": "⬇️ via @{username}",
        "downloading": "Downloading...",
        "queued": "⏳ Busy with another download — yours starts in a moment.",
        "download_queue_full": 'You already have {count} links on the way. Send more when some of them arrive.',
        "download_short_on_space": "I'm short on temporary space right now — send that link again in a few minutes.",
        "restarting_send_again": "🔄 I'm being updated right now — give me a few seconds and send that again.",
        "update_soon_try_later": "🔧 I'm being updated in a moment, so I can't start anything new right now — please try again in about {minutes} minute(s). I'll message you when I'm back.",
        "update_soon_try_later_soon": "🔧 I'm being updated right now, so I can't start anything new — please try again shortly. I'll message you when I'm back.",
        "update_will_reset": "🔧 Heads up: I'm about to be updated, and what you have going right now will be reset. You'll be able to start it again in a few minutes.",
        "update_done_try_now": '✅ The update is done — go ahead and try again now.',
        "gift_granted": '🎁 We have passed 100 users, thank you! You get {amount} ⚡ of free credit, good until {date}. It pays for about {conversions} paid conversions in {convert} or big-file downloads in {download}. Your balance: /balance',
        "gift_claimed": "🎁 Gift received: {amount} ⚡ of free credit! Mumu bots have passed {users} users, and this is our thank-you.\nIt is good until {date} (UTC) and pays for about {conversions} paid conversions in {convert} or big-file downloads in {download}.\nYour balance: /balance",
        "download_failed": "Couldn't download that: {error}",
        "download_blocked": "🚫 The site turned me away — it's not your link. Try again in a bit.",
        "download_missing": "🔍 Couldn't find that post — it may be deleted or private.",
        "download_too_big": "📦 That one is bigger than Telegram lets me send. Try a shorter clip.",
        "download_all_routes_failed": "😕 That didn't work. Try again in a minute — if it keeps failing, it's the site, not the link.",
        "fetching": "Fetching...",
        "reddit_fetch_failed": "Couldn't fetch that Reddit post: {error}",
        "twitter_fetch_failed_link": "Couldn't fetch that post's content right now — here's the link: {url}",
        "unrecognized_message": (
            "That's not a link I know. Paste an Instagram, TikTok, Pinterest, Reddit or X link, or see /help."
        ),
        "redeliver_as_file": "📄 Get it as an uncompressed file",
        "redeliver_as_compressed": "🖼 Get it compressed instead",
        "redeliver_expired": (
            "This button has expired — send the link again."
        ),
        "album_delivered": "{count} files above.",
        "merge_too_big": "📦 Too much for one file (up to {items} files, {mb} MB). Everything is in the chat above.",
        "merge_picture_too_big": "📦 One picture is too big for a PDF (over {mp} MP). The pictures are in the chat above.",
        "merge_no_photos": "📄 A PDF needs at least two photos. The files are in the chat above.",
        "merge_failed": "😕 Couldn't pack them into one file. The files above are fine — send the link again to retry.",
        "unknown_command": "I don't recognize that command. Send /help to see what I can do.",
        # 2.2.0: the quality picker and group chats (bot.py).
        "quality_heading": '📹 {title}',
        "quality_paid_note": '⚡ — over {free} MB: paid with ⚡ credit (/settings, large files).',
        "quality_choose": 'Choose a quality ↓',
        "quality_more_button": '🎵 MP3 · ✂️ Trim · more ↗️',
        "quality_expired": 'That choice has expired — send the link again.',
        "quality_picked": '⏳ {height}p — fetching...',
        "group_hello": "👋 Hi! Post an Instagram, TikTok, Pinterest, Reddit or X link here and I'll send the video.\n\n/settings — what I download here, and how.",
        "group_cant_see": '⚠️ Right now I only see messages addressed to me. To see the links people post, make me an admin of this group.',
        "group_settings_text": '⚙️ What I do in this group — tap to change.\n\nQuality applies where the site offers a choice; other links come as the site sends them. Files over {free} MB are not sent in groups.',
        "group_on": '✅ Downloading links',
        "group_paused": '⏸ Paused — tap to resume',
        "group_other_videos": '🎬 Other video sites',
        "group_quality_best": 'Best',
        "group_as_video": '📹 Sent as videos',
        "group_as_file": '📄 Sent as files',
        "group_credit_on": '🏷 Credit line: on',
        "group_credit_off": '🏷 Credit line: off',
        "group_anyone": '🔓 Anyone can change these',
        "group_admins_only": '🔒 Only admins can change these',
        "group_admins_alert": "Only this group's admins can change that.",
        "group_saved": 'Saved ✅',
    },
    "uz": {
        "transfer_waiting": "⏳ Boshqa katta fayl ko'chirilmoqda. Sizniki undan keyin boshlanadi.",
        "quota_hour": "Soatlik limitga yetdingiz: {limit} ta ({used} ta ishlatildi). Keyingisi taxminan {minutes} daqiqadan keyin.",
        "quota_day": "Bugungi limitga yetdingiz: {limit} ta ({used} ta ishlatildi). Taxminan {minutes} daqiqadan keyin yana mumkin.",
        "quota_donor_hint": "Ko'proq kerakmi? Bir marta bo'lsa ham xayriya qilgan har kimga limit doimiy ravishda ancha oshiriladi: /donate",
        "flood_wait": 'Juda tez yuboryapsiz — {seconds} soniyacha kutib, keyin davom eting.',
        "sibling_blurb": 'Oilamizdagi boshqa botlar pastda 👇',
        "donation_nudge": "💙 Bot foydali bo'ldimi? /donate server xarajatlariga yordam beradi. Mutlaqo ixtiyoriy.",
        "donate_unknown_currency": '"{currency}" degan valyuta yo\'q — xtr yoki usd deb yozing.',
        "donate_currency_not_configured": "Bu botda hozircha {currency} bilan xayriya qilib bo'lmaydi — Stars'dan foydalaning.",
        "donate_invalid_amount": "Miqdor noto'g'ri — masalan, /donate 500 yoki /donate 5 usd deb yozing.",
        "donate_prompt": "Rahmat! Bu server xarajatlariga ketadi. Miqdorni tanlang yoki o'zingiz yozish uchun «Boshqa»ni bosing.",
        "donate_custom_button": "✏️ Boshqa {symbol}",
        "donate_too_many_stars": "Bu juda ko'p! Bir martalik xayriya {max} ⭐ dan oshmasin.",
        "donate_out_of_range": "{currency} bilan xayriya {lo} dan {hi} {symbol} gacha bo'lishi kerak.",
        "donate_invoice_title": 'Server xarajatlariga hissa',
        "donate_invoice_description": "Botlar xarajatlariga ketadi va ConvertBot'dagi konvertatsiyalar uchun balansingizga {credited} ⚡ kredit qo'shadi.",
        "donate_invoice_label": 'Xayriya',
        "donate_invoice_description_fiat": 'Server xarajatlari uchun bir martalik ixtiyoriy xayriya. Rahmat!',
        "donate_prompt_credit": "To'lagan Stars'ingiz ConvertBot'da konvertatsiyalarga sarflanadigan ⚡ kreditga aylanadi. Keyingi {left} ⭐ ning har biri {each} ⚡ beradi ({mult}×): {rate} ⚡ oddiy kredit, qolgani esa to'lovdan {days} kun o'tgach muddati tugaydigan bonus. ⚡ ni yechib olib ham, Stars'ga aylantirib ham BO'LMAYDI.",
        "donate_prompt_credit_base": "To'lagan har bir ⭐ ConvertBot'da konvertatsiyalarga sarflanadigan {rate} ⚡ kreditga aylanadi. ⚡ ni yechib olib ham, Stars'ga aylantirib ham BO'LMAYDI.",
        "donate_invoice_error": "⚠️ Telegram to'lov hisobini yaratmadi: {error}",
        "stars_unit": "Stars (yulduzcha)",
        "donate_custom_ask": 'Qancha {unit} xayriya qilmoqchisiz? Faqat sonni yozib yuboring.',
        "donate_invalid_amount_retry": "Miqdor noto'g'ri — qaytadan urinish uchun /donate yuboring.",
        "donate_thanks": '🙏 {amount} ⭐ uchun katta rahmat!',
        "topup_thanks": "🙏 {stars} ⭐ xayriyangiz uchun rahmat — bu botlarning ishlashiga yordam beradi.\n\nMinnatdorchilik sifatida {convert_bot} botida fayllarni o'girish uchun {total} ⚡ kredit oldingiz. U yerdagi balansingiz: {balance} ⚡.",
        "topup_thanks_bonus": 'Shundan {bonus} ⚡ — bonus kredit, uning muddati {date} kuni tugaydi.',
        "credit_cannot_be_withdrawn": "⚡ — ConvertBot'dagi konvertatsiyalar uchun kredit: uni yechib olib ham, Stars'ga aylantirib ham BO'LMAYDI. To'lovda muammo bo'lsa: /paysupport",
        "topup_refunded": "⚠️ {stars} ⭐ to'lovingiz o'tdi, lekin men tomondagi muammo tufayli ⚡ balansingizga tushmadi, shuning uchun to'lovni qaytardim: {stars} ⭐ hisobingizga qaytdi. Iltimos, bir necha daqiqadan keyin qayta urinib ko'ring.",
        "topup_failed": "⚠️ {stars} ⭐ to'lovingiz o'tdi, lekin men tomondagi muammo tufayli ⚡ balansingizga tushmadi. Hech narsa yo'qolmaydi: egasiga xabar berildi, u qo'lda qo'shib qo'yadi. Murojaat uchun: /paysupport",
        "paysupport_text": "💳 To'lov bo'yicha yordam\n\nTo'lovlar QAYTARILMAYDI: Stars qaytarib berilmaydi, ⚡ kreditni esa yechib olib ham, Stars'ga aylantirib ham BO'LMAYDI. Yagona istisno: bot kreditga aylantira olmagan to'lov sizga avtomatik ravishda to'liq qaytariladi.\n\nTo'lovda xatolik bo'lgan bo'lsa — pul yechilgan-u, kredit tushmagan bo'lsa yoki ikki marta yechilgan bo'lsa — {contact} manziliga to'lov sanasi, miqdori va Telegram ID raqamingizni ({user_id}) yozib yuboring. Tekshirilib, kredit bilan to'g'rilab beriladi.\n\n/balance har bir to'lovni va u qancha kredit qo'shganini ko'rsatadi.",
        "report_button": "🐞 Ma'lumotlarimni qo'shish",
        "problem_logged_note": "Anonim hisobot saqlandi",
        "report_disclaimer": "📨 Bu xabarga ma'lumotlaringizni qo'shasizmi?\n\nSizsiz saqlangan: bot, xato kodi {code}, hodisa {incident}, vaqt va versiya.\n\n«Yuborish» Telegram ID, @username, til va chat shaxsiy yoki guruh ekanini qo'shadi. Xabarlaringiz va fayllaringiz hech qachon qo'shilmaydi.\n\nBu sizning holatingizni loglardan topishga yordam beradi. Tanlov sizda.",
        "report_send": "📨 Ma'lumotlarimni yuborish",
        "report_cancel": '✖️ Bekor qilish',
        "report_sent": "✅ Rahmat — {incident} hodisasiga qo'shildi.",
        "report_already": "Ma'lumotlaringiz {incident} hodisasida allaqachon bor.",
        "report_cancelled": "Bekor qilindi — siz haqingizda hech narsa yuborilmadi.",
        "report_failed": "⚠️ Hozir xabarni yuborib bo'lmadi. Keyinroq qayta urinib ko'ring.",
        "report_invalid": 'Bu tugma endi ishlamaydi.',
        "crash_notice": "⚠️ Men tomonda xatolik yuz berdi, shuning uchun bajarilmadi. Qaytadan urinib ko'ring.",
        "sandbox_notice": '🧪 Test rejimi — haqiqiy Stars yechilmadi.',
        "balance_header": "⚡ Balansingiz: {balance}",
        "balance_totals": "Jami {paid} ⭐ to'landi · {credited} ⚡ qo'shildi · {spent} ⚡ sarflandi",
        "balance_rate": 'Keyingi {left} ⭐ ning har biri {each} ⚡ beradi ({mult}×).',
        "balance_rate_base": '1 ⭐ = {rate} ⚡.',
        "balance_bonus_line": 'Shundan {bonus} ⚡ — bonus kredit; {soon} ⚡ ning muddati {date} kuni tugaydi.',
        "balance_recent": 'Oxirgi amallar:',
        "balance_empty_hint": '/donate orqali istalgan paytda kredit olishingiz mumkin.',
        "bot_short_description": (
            "Instagram, TikTok, Pinterest, Reddit yoki X havolasini tashlang."
        ),
        "bot_description": "Ochiq postning havolasini yuboring — fayl qaytib keladi. Buyruq shart emas.\n\nInstagram, TikTok, Pinterest, Reddit va X. Karusel to'liq keladi, matnli post esa chiroyli kartochka bo'lib qaytadi.\n\nBot faqat ochiq bo'lgan narsani oladi — yopiq akkauntlarni chetlab o'tmaydi. Ingliz, o'zbek va rus tillarida. Nima saqlanishi: /privacy",
        # ---- shared policy keys (/privacy, /terms, /deletemydata) ----
        "privacy_heading": "🔒 Maxfiylik",
        "privacy_kept_heading": "Bu bot nimalarni saqlaydi:",
        "privacy_stored": "• Telegram ID raqamingiz, tilingiz, izoh va sifat sozlamalaringiz\n• siz yuborgan havolalar: har biri u bo'yicha fayl olinayotgan paytda, keyin esa oxirgi beshtasi — o'sha yuklamalar ostidagi tugmalar ishlashi uchun. Botdan 12 soat foydalanmasangiz, ular o'chiriladi\n• bot sizga oxirgi yuborgan narsa — 10 daqiqa davomida, uning ostidagi tugmalar va qayta yuborilgan o'sha havola faylni qaytadan yuklamasdan javob berishi uchun. Keyingi havolangiz, /deletemydata yoki botning qayta ishga tushishi uni oldinroq o'chiradi; u yuklab olinganidan keyin 12 soatdan kechiktirmay o'chiriladi\n• bot avval yuborgan fayllarning Telegram fayl ID'lari — postning qaytarib bo'lmaydigan kodi ostida saqlanadi, kim so'ragani esa saqlanmaydi. Ular yordamida bot o'sha postni qayta yuklab olmasdan yana yubora oladi. Fayl ID'sidan qayta foydalanishdan oldin bot post hali ham ochiq ekanini kamida har 12 soatda tekshiradi. Hech kim foydalanmagan fayl ID'lari 90 kundan keyin o'chiriladi\n• har bir yuklab olish uchun bitta yozuv: faqat qachonligi; kunlik limit shu asosida hisoblanadi. Yozuvlar 2 kundan keyin o'chiriladi\n• botdan foydalangan vaqtingiz — bot egasi botdan umuman foydalanilayotganini bilishi uchun\n• har bir tugma necha marta bosilgani — butun bot bo'yicha kunlik umumiy hisob, kim bosgani haqida hech qanday ma'lumotsiz\n• xayriya qilsangiz, uning yozuvi: miqdori va Telegram to'lov raqami\n• bot siz uchun bajarayotgan ish — u tugagunicha\n• bot qo'shilgan guruh chatida: guruh ID raqami va uning /settings sozlamalari — bot guruhdan chiqquncha. U yerdagi har bir xabarda havola bor-yo'qligini qaraydi va boshqa xabarlardan hech narsa saqlamaydi; yuklab olingan havola uni yuborgan kishiniki hisoblanadi, xuddi shu yerda yuborilgandek (kunlik limit yozuvi va 10 daqiqalik oxirgi yuklab olish). Guruh a'zolari haqida boshqa hech narsa\n\nShu 10 daqiqadan tashqari faylning o'zi saqlanmaydi: u olinadi, sizga yuboriladi va o'chiriladi.",
        "privacy_problems_heading": "Nimadir noto'g'ri ketganda:",
        "privacy_problems": "Bot kimgadir xato ko'rsatsa, uni o'zi yozib qo'yadi: xato kodi, hodisa raqami, qachon yuz bergani va bot versiyasi. Bularda siz haqingizda hech narsa yo'q — ID raqamingiz ham, ismingiz ham, nima yuborganingiz ham. Hech kim xabar bermagan nosozlik ham nosozligicha qoladi, shuning uchun bot so'ralishini kutmaydi.\n\nXato ostidagi \"Ma'lumotlarimni qo'shish\" tugmasi o'sha bitta hodisaga to'rt narsani biriktirishni taklif qiladi: Telegram ID raqamingiz, bo'lsa @username'ingiz, siz tanlagan til va bu suhbat shaxsiymi yoki guruhmi. To'rttasi ham avval ekranda aytiladi va \"Yuborish\"ni bosmasangiz hech narsa yuborilmaydi. Yozganlaringiz va yuborgan fayllaringiz esa bunga hech qachon kirmaydi.\n\nBu to'rttasi 30 kundan keyin o'z-o'zidan o'chiriladi, /deletemydata esa darhol o'chiradi. Xatolik yozuvining o'zi qoladi — u haqiqatan yuz bergan — shunchaki endi kim duch kelgani yozilmaydi. Yozuvlarning o'zi 180 kundan keyin o'chiriladi.",
        "privacy_seen_by_heading": "Yana kim ko'ra oladi:",
        "privacy_seen_by": "• Telegram — barcha xabarlar u orqali o'tadi va u o'z qoidalari asosida ishlaydi\n• bot joylashgan hosting va bot foydalanadigan ma'lumotlar bazasi",
        "privacy_others": "• siz havolasini yuborgan sayt, hamda sayt to'g'ridan-to'g'ri javob bermaganda bot murojaat qiladigan yuklab olish xizmatlari. Ular havolani oladi va so'rov sizdan emas, bot serveridan kelganini ko'radi; ular nimani qayd etishi o'z qoidalariga bog'liq, bu botning qoidalariga emas.",
        "privacy_kept_for_heading": "Qancha vaqt saqlanadi:",
        "privacy_kept_for": "Sozlamalaringiz va bot siz uchun saqlab turgan narsalar ularni o'chirmaguningizcha yoki botdan foydalanishni to'xtatmaguningizcha turadi. Foydalanish qaydlari taxminan uch oydan keyin o'chiriladi. To'lov yozuvlari va ⚡ balansingiz esa uzoqroq saqlanadi, chunki to'lov bo'yicha nizolar ular asosida hal qilinadi.\n\nBu ma'lumotlar sotilmaydi, ijaraga berilmaydi, reklamada ishlatilmaydi va yuqorida aytilganlardan boshqa hech kimga berilmaydi.",
        "privacy_your_choices": "Nima qilishingiz mumkin:\n/deletemydata — bot siz haqingizda saqlagan ma'lumotlarni o'chirish\n/terms — botdan foydalanish shartlari\n\nBotni Telegramda bloklasangiz, u sizga yozmay qo'yadi, lekin hech narsa o'chmaydi. Ikkalasini ham xohlasangiz, avval /deletemydata yuboring.",
        "terms_heading": "📜 Shartlar",
        "terms_use": "Botdan maqsadiga ko'ra, qonun va Telegram qoidalari doirasida foydalaning. Uni boshqalarni bezovta qilish uchun ishlatmang va belgilangan cheklovlardan oshirib yuklamang — aks holda akkaunt bloklanadi.",
        "terms_specific": "Yuklab olish haqida: bot faqat hammaga ochiq bo'lgan narsani olib beradi. Yopiq akkauntlarni chetlab o'tmaydi va bunga urinmaydi ham. Birovning surati, videosi yoki posti bilan keyin nima qilishingiz — siz, o'sha odam va qonun o'rtasidagi masala; yuklab olganingiz uni sizniki qilmaydi. Bitta odam botni boshqalar uchun band qilib qo'ymasligi uchun har bir kishiga kunlik limit bor.",
        "terms_money": "Pul haqida: /donate — ixtiyoriy, mablag' botlar xarajatlariga ketadi. Stars'dagi to'lov ConvertBot'da konvertatsiyalar uchun ⚡ kredit ham beradi: har ⭐ uchun 2 ⚡, umumiy hisobda birinchi 500 ta Stars uchun esa 6 ⚡ dan, keyingi 500 tasi uchun 4 ⚡ dan. 2 ⚡ dan ortig'i bonus kredit bo'lib, to'lovdan 90 kun o'tgach muddati tugaydi. To'lovlar QAYTARILMAYDI: Stars qaytarib berilmaydi, kreditni esa yechib olib ham, Stars'ga aylantirib ham BO'LMAYDI. Yagona istisno: bot kreditga aylantira olmagan to'lov avtomatik ravishda to'liq qaytariladi. Har bir to'lovni Telegram o'tkazadi, bot karta raqamingizni ko'rmaydi. To'lovda muammo bo'lsa: /paysupport.",
        "terms_no_warranty": "Kafolat yo'q: botni bir kishi yuritadi va u oldindan ogohlantirmasdan sekinlashishi, xato qilishi yoki butunlay to'xtab qolishi mumkin. Siz uchun muhim narsalarning nusxasini o'zingizda saqlang.",
        "policy_full_text": "To'liq matn: {url}",
        "policy_contact": "Savol, shikoyat yoki ma'lumot so'rovi uchun: {contact}",
        "delete_data_confirm": "⚠️ Bot siz haqingizda saqlagan ma'lumotlar o'chiriladi. Buni ortga qaytarib bo'lmaydi.",
        "delete_data_consequences": "Til, izoh va sifat sozlamalaringiz hamda nimani qachon yuklab olganingiz haqidagi qaydlar o'chiriladi. Kunlik limitingiz ham yangilanadi. Oxirgi yuklama hali saqlanayotgan bo'lsa, u ham o'chiriladi; undan oldingilari bu yerda umuman saqlanmagan.\n\nXayriya yozuvlari foydalanuvchi nomingizsiz saqlanib qoladi, chunki to'lov bo'yicha nizolar ular asosida hal qilinadi. ⚡ balansingiz ham saqlanadi — qaytib kelsangiz, u o'z joyida bo'ladi.",
        "delete_data_button_yes": "🗑 O'chirilsin",
        "delete_data_button_no": "↩️ Ma'lumotlarim qolsin",
        "delete_data_kept": "Hech narsa o'chirilmadi.",
        "delete_data_done": "🗑 Bajarildi — {rows} ta yozuv o'chirildi.\n\nIstalgan payt /start yuborsangiz, bot sizni yangi foydalanuvchi sifatida kutib oladi.",
        "delete_data_failed": "Hozir o'chirib bo'lmadi — bot tomonida xatolik yuz berdi. Bir necha daqiqadan keyin qayta urinib ko'ring.",
        "language_set_confirmation": "✅ Til o'zbekchaga o'zgartirildi.",
        "cancel_header": "\u274c Bekor qilindi:",
        "cancel_nothing": "Bekor qilinadigan amal yo'q — hozir hech narsa kutilmayapti.",
        "cancel_ask": 'Qaysi birini bekor qilay? Hozir quyidagilar kutilmoqda:',
        "cancel_kept": "Yaxshi — hech narsa bekor qilinmadi.",
        "cancel_reply_box_freed": "Xabar yozish maydoni endi bo'sh.",
        "cancel_button_all": "❌ Hammasini",
        "cancel_button_none": '↩️ Hech birini, davom etamiz',
        "cancel_button_donation": "💸 Xayriya miqdori",
        "cancel_item_donation": 'kiritilishi kutilayotgan xayriya miqdori',
        "cancel_item_stale_prompt": "javobsiz qolgan eski so'rov",
        "feedback_ask": (
            "💬 Bu botlarni yuritayotgan odamga nima demoqchisiz? Shu xabarga javob qilib yozing.\n\nXabar ismingizsiz yuboriladi, shuning uchun sizga javob yoza olmaydi. To'xtatish: /cancel"
        ),
        "feedback_placeholder": "Fikringiz",
        "feedback_text_only": "Shu xabarga javob qilib matn yuboring yoki /cancel.",
        "feedback_thanks": "✅ Rahmat — anonim yuborildi.",
        "feedback_limit": "Bugun {per_day} ta bo'ldi, bu kunlik chegara. Rahmat — ertaga yana yozing.",
        "feedback_failed": "Yuborilmadi — men tomonda xatolik yuz berdi. Keyinroq qayta urinib ko'ring.",
        "cancel_button_feedback": "💬 Fikr-mulohaza",
        "cancel_item_feedback": "men so'ragan fikr-mulohaza",
        "help_brief": (
            "Bot qanday ishlaydi:\n\n• Havola yuboring — ichidagini yuboraman, buyruq kerak emas. Instagram, TikTok, X, Pinterest va Reddit.\n• /settings: izoh, fayl sifati va katta fayllar (48 MB dan katta, ⚡ evaziga).\n• Taklif yoki muammo: /feedback\n\nAsosiy buyruqlar: /settings · /balance · /cancel\nBarcha buyruqlar: to'liq qo'llanma, pastda yoki /fullhelp.\n\n"
        ),
        "help_full_button": "📖 To'liq qo'llanma",
        "help_brief_button": "↩️ Qisqa variant",
        "help_page_everything": "To'liq qo'llanma: /fullhelp",
        "help_page_gone": "Bu yordam sahifasi endi yo'q. Hozirgilarini ko'rish uchun /help yuboring.",
        "help_btn_download": "🔗 Havola yuborish",
        "helppage_download": (
            "🔗 Havola yuborish — yuklab olish qanday ishlaydi\n"
            "\n"
            "Postning havolasini yuboring — buyruq kerak emas — ichidagisini sizga yuboraman:\n"
            "  - Instagram: reels, rasmlar, butun karusellar\n"
            "  - TikTok: suv belgisisiz videolar, rasmli slayd-shoular\n"
            "  - Twitter/X: video va rasmlar yoki matnli post uchun toza rasm-karta\n"
            "  - Pinterest: pin, to'liq o'lchamda\n"
            "  - Reddit: media post bo'lsa media, matnli bo'lsa rasm-karta\n"
            "\n"
            "{quota}\n"
            "• Yuklab olish boshlanganda hisoblanadi, keyin xato bo'lsa ham. Yetkazilgan fayl ostidagi 📄/🖼 tugmasi hamda 📄 PDF / 🗜 ZIP tugmalari har biri bitta yuklab olish hisoblanadi. Hali yuklab olayotgan havolani qayta yuborsangiz, 👌 olasiz va u hisoblanmaydi.\n"
            "• Bir vaqtda {waiting} tagacha havolangiz yo'lda bo'lishi mumkin; bir paytda {at_once} tasini yuklayman, qolganlari navbat kutadi.\n"
            "\n"
            "Hajm: {free_mb} MB gacha bepul. {large}\n"
            "\n"
            "Yetkazilgan fayl ostida:\n"
            "• \"📄 Siqilmagan fayl sifatida olish\" (yoki \"🖼 Siqilgan holda olish\") o'sha postni boshqa sifatda yana yuboradi; har bir tugma bir marta ishlaydi.\n"
            "• Albom ostida: 📄 PDF uning rasmlarini bitta PDF'ga, 🗜 ZIP hammasini bitta arxivga joylaydi. {merge_items} tagacha fayl va {merge_mb} MB gacha.\n"
            "• /settings: izoh, sifat va katta fayllar.\n"
            "\n"
            "Misol: https://www.tiktok.com/@name/video/123 kabi TikTok havolasini yuboring — video suv belgisisiz keladi."
        ),
        "helppage_download_quota": "Bepul: istalgan 24 soatda {per_day} ta va istalgan bir soatda {per_hour} ta yuklab olish. Hisob hozirdan orqaga qarab yuritiladi, yarim tunda nolga tushmaydi. Chegaraga yetsangiz, keyingisi qachon bo'shashini aytaman. /donate orqali bir marta bo'lsa ham, istalgan miqdorda to'lagan har kim uchun chegara butunlay kuniga {donor_day} ta va soatiga {donor_hour} ta bo'ladi.",
        "helppage_download_quota_off": "Bepul.",
        "helppage_download_large": "Kattarog'i, {max} MB gacha, faqat /settings da katta fayllarni yoqsangiz va shaxsiy chatda: 100 MB gacha ⚡{first}, keyingi har 100 MB arzonroq ({max} MB uchun ⚡{top}). ⚡ fayl yetib kelgandan keyingina yechiladi, xato bo'lsa umuman yechilmaydi.",
        "helppage_download_large_off": "Undan kattasi rad etiladi.",
        "helppage_settings": (
            "⚙️ /settings — yuklamalaringiz qanday kelishi\n"
            "\n"
            "/settings kalitlaringizni va ularning holatini ko'rsatadi. O'zgartirish uchun birini bosing; keyingi barcha yuklamalar uchun shunday qoladi.\n"
            "• Izoh (dastlab yoniq): har bir yuklama ostida \"@{username} orqali\" qatori. Yana: /caption on yoki /caption off.\n"
            "• Siqilmagan rejim (dastlab o'chiq). Yoniq: fayllar asl sifatida, hujjat ko'rinishida keladi, Telegram ularni siqmaydi — lekin ochmaguningizcha chatda ko'rinmaydi va ijro etilmaydi. O'chiq: chatda ko'rinadigan rasm va video bo'lib keladi, Telegram ularni siqadi. Yana: /lossless on yoki /lossless off. Faqat bitta yuklama uchun uning ostidagi 📄/🖼 tugmasidan foydalaning.\n"
            "{large}\n"
            "Misol: /lossless on yuboring, keyin Instagram havolasini: rasmlar to'liq sifatda, fayl bo'lib keladi."
        ),
        "helppage_settings_large": "• Katta fayllar (dastlab o'chiq). Yoniq: {free_mb} MB dan katta yuklama ham {max} MB gacha olinadi va ⚡ kredit evaziga fayl sifatida yuboriladi — 100 MB gacha ⚡{first}, {max} MB uchun ⚡{top} — fayl yetib kelgandan keyingina yechiladi. O'chiq: {free_mb} MB dan kattasi rad etiladi va hech narsa yechilmaydi. Faqat shaxsiy chatda.",
        "privacy_feedback_heading": "/feedback yuborsangiz:",
        "privacy_feedback": (
            "Yozganingiz bu botlarni yuritayotgan odamga Telegram xabari sifatida yetib boradi — "
            "qaysi botdan kelgani va tilingiz bilan birga, lekin ismingiz, foydalanuvchi nomingiz "
            "va ID raqamingizsiz. Uni faqat o'sha odam o'qiydi va unga javob yoza olmaydi. "
            "Nusxasi botlar bazasida 30 kun saqlanadi, keyin o'chiriladi."
        ),
        "start_greeting": (
            "Salom! Menga havola yuboring (Instagram, TikTok, Pinterest, Reddit "
            "yoki Twitter/X) va men uni siz uchun yuklab beraman.\n\n"
        ),
        "help_text": "Istalgan vaqt havola yuboring — buyruq kerak emas:\n  - Instagram: reels, rasmlar, butun karusel\n  - TikTok: suv belgisiz video va rasm-slaydlar\n  - Twitter/X: video va rasmlar yoki matnli post rasmi\n  - Pinterest: pin, asl o'lchamda\n  - Reddit: media yoki matnli post rasmi\n\nAlbom ostida: 📄 PDF rasmlarni bitta PDF ga jamlaydi, 🗜 ZIP hammasini bitta arxivga soladi.\n\nBuyruqlar:\n/settings - izoh, sifat va katta fayllar (48 MB dan katta, ⚡ evaziga)\n/caption on|off - \"via @{username}\" yozuvi\n/lossless on|off - fayllarni siqilmagan holda olish\n/cancel - men kutayotgan narsani to'xtatish\n/feedback - botlarni yuritayotgan odamga yozish (anonim)\n/donate - server xarajatlariga yordam (ixtiyoriy)\n/balance - ⚡ kreditingiz\n/paysupport - to'lov bilan muammo\n/en, /uz, /rus - tilni almashtirish (yoki /language)\n/privacy - siz haqingizda nimalar saqlanadi\n/terms - botdan nima uchun foydalanish mumkin\n/deletemydata - siz haqingizdagi hamma narsani o'chirish\n/start - boshidan boshlash\n/fullhelp - shu qo'llanma (/help - qisqasi)\n\n⚠️ To'lovlar qaytarilmaydi — Stars qaytarilMAYDI, ⚡ kreditni yechib olib BO'LMAYDI.\n\n",
        "caption_state_on": "YONIQ",
        "caption_state_off": "O'CHIQ",
        "settings_heading": "⚙️ Sozlamalaringiz. O'zgartirish uchun bosing.",
        "caption_status": "Izoh hozir {state}.",
        "caption_turned": 'Fayl izohi endi {state}.',
        "caption_toggle_answer": 'Izoh endi {state}.',
        "lossless_state_on": "YONIQ",
        "lossless_state_off": "O'CHIQ",
        "lossless_status": "Siqilmagan rejim {state}.\n\nYONIQ: fayllar asl holida keladi — siqilmaydi, lekin chatda ko'rinmaydi.\nO'CHIQ: chatda ko'rinadigan rasm va video, Telegram siqadi.",
        "lossless_turned": 'Siqilmagan holda yuklash endi {state}.',
        "lossless_toggle_answer": 'Siqilmagan rejim endi {state}.',
        "large_state_on": "YONIQ",
        "large_state_off": "O'CHIQ",
        "large_status": "📦 Katta fayllar {state}.\n\nYONIQ: {free} MB dan katta, {max} MB gacha yuklamalar fayl bo'lib keladi — 100 MB gacha ⚡{per}, {max} MB uchun ⚡{top}. Kredit fayl yetib kelgandan keyingina yechiladi.\nO'CHIQ: {free} MB dan kattasi rad etiladi; hech narsa yechilmaydi.",
        "large_toggle_answer": "Katta fayllar: {state}.",
        "large_files_turned_on": "📦 Katta fayllar yoqildi. Havolani qayta yuboring — 100 MB gacha ⚡{per}, fayl yetib kelgach yechiladi. O'chirish: /settings",
        "large_files_turn_on_button": "📦 Katta fayllarni yoqish",
        "large_files_off": "📦 Bu fayl {free} MB dan katta. Pastda (yoki /settings da) katta fayllarni yoqing, keyin havolani qayta yuboring — 100 MB gacha ⚡{per}.",
        "large_files_private_only": "📦 Bu fayl {free} MB dan katta. Bunday fayllarni faqat men bilan shaxsiy chatda yuboraman — havolani o'sha yerga tashlang.",
        "large_files_no_credit": "📦 Bu fayl {free} MB dan katta, narxi kamida ⚡{per}, balansingizda esa ⚡{balance} bor. /donate orqali to'ldiring, keyin havolani yana yuboring.",
        "large_files_fetching": "📦 {free} MB dan katta — katta fayl sifatida olinmoqda (100 MB gacha ⚡{per}, yetib kelgach yechiladi).",
        "large_files_too_big": "📦 Bu fayl {max} MB dan katta — Telegram menga bundan kattasini yuborishga ruxsat bermaydi. Qisqaroq videoni sinab ko'ring.",
        "large_files_short": "📦 Fayl {mb} MB (⚡{price}), balansingiz esa ⚡{balance}, shuning uchun hech narsa yechilmadi. /donate bilan to'ldiring va havolani qayta yuboring.",
        "large_files_sending": "📦 {mb} MB tayyor. Yuboryapman...",
        "large_files_send_failed": "📦 Faylni oldim, lekin sizga yubora olmadim. ⚡{price} qaytarildi — balansingiz ⚡{balance}.",
        "large_files_charged": "📦 {mb} MB yuborildi. Buning uchun ⚡{price} yechildi — balansingiz endi ⚡{balance}.",
        "download_credit_caption": "⬇️ @{username} orqali",
        "downloading": "Yuklanmoqda...",
        "queued": '⏳ Boshqa fayl yuklanmoqda — sizniki birozdan keyin boshlanadi.',
        "download_queue_full": 'Sizning {count} ta havolangiz yuklanmoqda. Ulardan biri kelgach, yana yuboring.',
        "download_short_on_space": 'Hozir vaqtinchalik joy yetishmayapti — havolani bir necha daqiqadan keyin qayta yuboring.',
        "restarting_send_again": '🔄 Bot hozir yangilanmoqda — bir necha soniyadan keyin qaytadan yuboring.',
        "update_soon_try_later": "🔧 Bot tez orada yangilanadi, shuning uchun yangi ishni boshlab bo'lmaydi — taxminan {minutes} daqiqadan keyin qayta urinib ko'ring. Ishga tushgach, o'zim xabar beraman.",
        "update_soon_try_later_soon": "🔧 Bot hozir yangilanmoqda, shuning uchun yangi ishni boshlab bo'lmaydi — birozdan keyin qayta urinib ko'ring. Ishga tushgach, o'zim xabar beraman.",
        "update_will_reset": "🔧 Diqqat: bot yangilanadi va hozir bajarilayotgan ishingiz to'xtab qoladi. Bir necha daqiqadan keyin qaytadan boshlashingiz mumkin.",
        "update_done_try_now": "✅ Yangilanish tugadi — endi qaytadan urinib ko'rishingiz mumkin.",
        "gift_granted": "🎁 Bizda 100 dan ortiq foydalanuvchi bo'ldi, rahmat! Sizga {amount} ⚡ bepul kredit berildi, u {date} gacha amal qiladi. Bu {convert} orqali taxminan {conversions} ta pullik konvertatsiya yoki {download} orqali katta fayllarni yuklab olish uchun yetadi. Balansingiz: /balance",
        "gift_claimed": "🎁 Sovg'a: {amount} ⚡ bepul kredit! Mumu botlarida {users} dan ortiq foydalanuvchi bo'ldi, bu bizning minnatdorchiligimiz.\nU {date} gacha (UTC) amal qiladi va {convert} orqali taxminan {conversions} ta pullik konvertatsiya yoki {download} orqali katta fayllarni yuklab olish uchun yetadi.\nBalansingiz: /balance",
        "download_failed": "Buni yuklab bo'lmadi: {error}",
        "download_blocked": "🚫 Sayt meni qaytardi — havolangizda muammo yo'q. Birozdan keyin urinib ko'ring.",
        "download_missing": "🔍 Bu post topilmadi — o'chirilgan yoki yopiq bo'lishi mumkin.",
        "download_too_big": '📦 Bu fayl Telegram ruxsat beradigan hajmdan katta. Qisqaroq video sinab ko‘ring.',
        "download_all_routes_failed": "😕 Bo'lmadi. Bir daqiqadan keyin urinib ko'ring — takrorlanaversa, muammo saytda, havolada emas.",
        "fetching": "Olinmoqda...",
        "reddit_fetch_failed": "Bu Reddit postini olib bo'lmadi: {error}",
        "twitter_fetch_failed_link": "Hozircha bu post mazmunini olib bo'lmadi — mana havola: {url}",
        "unrecognized_message": "Bu men biladigan havola emas. Instagram, TikTok, Pinterest, Reddit yoki X havolasini yuboring yoki /help ni ko'ring.",
        "redeliver_as_file": "📄 Siqilmagan fayl sifatida olish",
        "redeliver_as_compressed": "🖼 Siqilgan holda olish",
        "redeliver_expired": "Bu tugmaning muddati o'tgan — havolani qayta yuboring.",
        "album_delivered": "Yuqorida {count} ta fayl.",
        "merge_too_big": "📦 Bitta fayl uchun juda ko'p ({items} ta fayl, {mb} MB gacha). Hammasi yuqoridagi chatda.",
        "merge_picture_too_big": "📦 Rasmlardan biri PDF uchun juda katta ({mp} MP dan ortiq). Rasmlar yuqoridagi chatda.",
        "merge_no_photos": "📄 PDF uchun kamida ikkita rasm kerak. Fayllar yuqoridagi chatda.",
        "merge_failed": "😕 Ularni bitta faylga jamlab bo'lmadi. Yuqoridagi fayllar joyida — qayta urinish uchun havolani yana yuboring.",
        "unknown_command": "Bunday buyruq yo'q. Bot nimalar qila olishini bilish uchun /help yuboring.",
        # 2.2.0: the quality picker and group chats (bot.py).
        "quality_heading": '📹 {title}',
        "quality_paid_note": '⚡ — {free} MB dan katta: ⚡ kredit bilan pullik (/settings, katta fayllar).',
        "quality_choose": 'Sifatni tanlang ↓',
        "quality_more_button": '🎵 MP3 · ✂️ Kesish · boshqalar ↗️',
        "quality_expired": 'Bu tanlov eskirgan — havolani qaytadan yuboring.',
        "quality_picked": '⏳ {height}p — olinmoqda...',
        "group_hello": "👋 Salom! Bu yerga Instagram, TikTok, Pinterest, Reddit yoki X havolasini yuboring — videoni yuboraman.\n\n/settings — bu yerda nimani va qanday yuklashim.",
        "group_cant_see": "⚠️ Hozir faqat menga yozilgan xabarlarni ko'raman. Odamlar yuborgan havolalarni ko'rishim uchun meni guruh admini qiling.",
        "group_settings_text": "⚙️ Bu guruhda nima qilaman — o'zgartirish uchun bosing.\n\nSifat sayt tanlov bergan joyda ishlaydi; boshqa havolalar sayt qanday bersa, shunday keladi. {free} MB dan katta fayllar guruhlarda yuborilmaydi.",
        "group_on": '✅ Havolalarni yuklayapman',
        "group_paused": "⏸ To'xtatilgan — davom ettirish uchun bosing",
        "group_other_videos": '🎬 Boshqa video saytlar',
        "group_quality_best": 'Eng yaxshi',
        "group_as_video": '📹 Video sifatida',
        "group_as_file": '📄 Fayl sifatida',
        "group_credit_on": '🏷 Bot nomi yozuvi: yoqilgan',
        "group_credit_off": "🏷 Bot nomi yozuvi: o'chirilgan",
        "group_anyone": "🔓 Har kim o'zgartira oladi",
        "group_admins_only": "🔒 Faqat adminlar o'zgartiradi",
        "group_admins_alert": "Buni faqat guruh adminlari o'zgartira oladi.",
        "group_saved": 'Saqlandi ✅',
    },
    "ru": {
        "transfer_waiting": "⏳ Сейчас переносится другой большой файл. Ваш начнётся сразу после.",
        "quota_hour": (
            "Достигнут предел: {limit} загрузок в час (использовано {used}). Следующая — примерно через {minutes} мин."
        ),
        "quota_day": (
            "Достигнут дневной предел: {limit} загрузок (использовано {used}). Снова можно примерно через {minutes} мин."
        ),
        "quota_donor_hint": (
            "Нужно больше? Все, кто хоть раз сделал пожертвование (любое), получают намного больший предел навсегда: /donate"
        ),
        "flood_wait": "Вы отправляете быстрее, чем я успеваю — подождите примерно {seconds} секунд(ы) и продолжайте.",
        "sibling_blurb": "Тоже часть этой семьи ботов, смотрите ниже \U0001f447",
        "donation_nudge": (
            "💙 Бот полезен? /donate помогает оплачивать хостинг. Совершенно необязательно."
        ),
        "donate_unknown_currency": 'Неизвестная валюта "{currency}" — попробуйте xtr или usd.',
        "donate_currency_not_configured": "Пожертвования в {currency} на этом боте пока не настроены — попробуйте Stars.",
        "donate_invalid_amount": "Это некорректная сумма — попробуйте, например, /donate 500 или /donate 5 usd.",
        "donate_prompt": (
            "Спасибо! Это идёт на хостинг. Выберите сумму или «Другое», чтобы ввести свою."
        ),
        "donate_custom_button": "✏️ Другое {symbol}",
        "donate_too_many_stars": "Это очень много звёзд! Пусть будет меньше {max} ⭐ за одно пожертвование.",
        "donate_out_of_range": "Пожертвования в {currency} должны быть в диапазоне от {lo} до {hi} {symbol}.",
        "donate_invoice_title": "Поддержать хостинг",
        "donate_invoice_description": "Идёт на расходы по работе бота и добавляет {credited} ⚡ на ваш баланс для конвертаций в ConvertBot.",
        "donate_invoice_label": "Вклад в хостинг",
        "donate_invoice_description_fiat": 'Разовое добровольное пожертвование на хостинг. Спасибо!',
        "donate_prompt_credit": 'Оплаченные Stars превращаются в ⚡ кредит на конвертации в ConvertBot. Следующие {left} ⭐ дают по {each} ⚡ ({mult}×): {rate} ⚡ обычного кредита и бонус, который сгорает через {days} дней после оплаты. ⚡ НЕЛЬЗЯ вывести или обменять обратно на Stars.',
        "donate_prompt_credit_base": 'Оплаченные Stars превращаются в ⚡ кредит на конвертации в ConvertBot, {rate} ⚡ за ⭐. ⚡ НЕЛЬЗЯ вывести или обменять обратно на Stars.',
        "donate_invoice_error": "⚠️ Telegram не смог создать этот счёт: {error}",
        "stars_unit": "Stars (звёзды)",
        "donate_custom_ask": "Сколько {unit} вы хотите пожертвовать? Ответьте числом.",
        "donate_invalid_amount_retry": "Это некорректная сумма — отправьте /donate, чтобы попробовать снова.",
        "donate_thanks": "🙏 Спасибо за {amount} ⭐ — это по-настоящему ценно!",
        "topup_thanks": '🙏 Спасибо за пожертвование в {stars} ⭐ — это помогает ботам работать.\n\nВ благодарность вы получили {total} ⚡ кредита в {convert_bot} — его можно тратить на конвертацию файлов. Ваш баланс там: {balance} ⚡.',
        "topup_thanks_bonus": 'Из них {bonus} ⚡ — бонусный кредит, он сгорит {date}.',
        "credit_cannot_be_withdrawn": '⚡ — это кредит на конвертации в ConvertBot, его НЕЛЬЗЯ вывести или обменять обратно на Stars. Проблема с платежом? /paysupport',
        "topup_refunded": "⚠️ Ваш платёж на {stars} ⭐ прошёл, но из-за проблемы на моей стороне ⚡ не поступили на баланс, поэтому я вернул платёж: {stars} ⭐ снова на вашем счёте. Пожалуйста, попробуйте ещё раз через несколько минут.",
        "topup_failed": "⚠️ Ваш платёж на {stars} ⭐ прошёл, но из-за проблемы на моей стороне ⚡ не поступили на баланс. Ничего не потеряно: владелец уже знает и начислит их вручную. Уточнить: /paysupport",
        "paysupport_text": '💳 Помощь с платежом\n\nПлатежи ОКОНЧАТЕЛЬНЫЕ: Stars НЕ возвращаются, а ⚡ кредит НЕЛЬЗЯ вывести или обменять обратно на Stars. Единственное исключение: платёж, который бот не смог зачислить как кредит, возвращается вам автоматически и полностью.\n\nЕсли с платежом что-то пошло не так — деньги списали, а кредит не пришёл, или списали дважды, — напишите на {contact}: дату, сумму и ваш Telegram ID, {user_id}. Это проверят и исправят кредитом.\n\n/balance показывает каждый платёж и что он добавил.',
        "report_button": '🐞 Добавить мои данные',
        "problem_logged_note": 'Анонимный отчёт сохранён',
        "report_disclaimer": "📨 Добавить ваши данные к этому отчёту?\n\nУже сохранено без вас: бот, код ошибки {code}, инцидент {incident}, время и версия.\n\n«Отправить» добавит ваш Telegram ID, @username, язык и то, личный это чат или группа. Никогда — ваши сообщения или файлы.\n\nЭто помогает найти ваш случай в логах. Решать вам.",
        "report_send": '📨 Отправить мои данные',
        "report_cancel": '✖️ Отмена',
        "report_sent": "✅ Спасибо — добавлено к инциденту {incident}.",
        "report_already": "Ваши данные уже есть в инциденте {incident}.",
        "report_cancelled": "Отменено — ничего о вас не отправлено.",
        "report_failed": '⚠️ Сейчас не удалось отправить сообщение. Попробуйте позже.',
        "report_invalid": 'Эта кнопка больше не работает.',
        "crash_notice": "⚠️ Что-то пошло не так с моей стороны, поэтому это не сделано. Попробуйте ещё раз.",
        "sandbox_notice": "🧪 Тестовый режим — настоящие Stars не списывались.",
        "balance_header": "⚡ Ваш баланс: {balance}",
        "balance_totals": "Всего оплачено {paid} ⭐ · начислено {credited} ⚡ · потрачено {spent} ⚡",
        "balance_rate": 'Следующие {left} ⭐ дают по {each} ⚡ ({mult}×).',
        "balance_rate_base": '1 ⭐ даёт {rate} ⚡.',
        "balance_bonus_line": 'Из них {bonus} ⚡ — бонусный кредит; {soon} ⚡ сгорит {date}.',
        "balance_recent": "Последние операции:",
        "balance_empty_hint": "/donate добавит кредит в любой момент.",
        "bot_short_description": (
            "Пришлите ссылку на пост в Instagram, TikTok, Pinterest, Reddit или X."
        ),
        "bot_description": (
            "Пришлите ссылку на открытый пост — вернётся сам файл. Команда не нужна.\n"
            "\n"
            "Instagram, TikTok, Pinterest, Reddit и X. Карусель приходит целиком, а текстовый "
            "пост возвращается аккуратной карточкой.\n"
            "\n"
            "Бот достаёт то, что и так открыто, — это не обход закрытого аккаунта. Английский, "
            "узбекский и русский. /privacy — что бот хранит."
        ),
        # ---- shared policy keys (/privacy, /terms, /deletemydata) ----
        "privacy_heading": "🔒 Конфиденциальность",
        "privacy_kept_heading": "Что бот хранит:",
        "privacy_stored": (
            "• ваш числовой id в Telegram, язык и настройки подписи и качества\n"
            "• ссылки, которые вы присылаете: каждую — пока загружается то, что по ней, затем "
            "последние пять, чтобы работали кнопки под этими загрузками. Они удаляются, если вы "
            "не пользуетесь ботом 12 часов\n"
            "• последний файл, который бот вам отправил, — на 10 минут, чтобы кнопки под ним и та "
            "же ссылка, присланная снова, срабатывали без повторной загрузки. Ваша следующая "
            "ссылка, /deletemydata или перезапуск бота удаляют его раньше; он удаляется не позже "
            "чем через 12 часов после скачивания\n"
            "• file ID Telegram для файлов, которые бот уже отправлял, — под необратимым кодом "
            "поста, без сведений о том, кто его запрашивал. Они позволяют отправить тот же пост "
            "ещё раз, не скачивая его заново. Прежде чем снова использовать file ID, бот не "
            "реже чем раз в 12 часов проверяет, что пост всё ещё публичный. File ID, которыми "
            "никто не пользуется, удаляются через 90 дней\n"
            "• по одной записи на скачивание: только когда — по ним считается дневной лимит. "
            "Записи удаляются через 2 дня\n"
            "• отметку времени на каждое обращение к боту — чтобы владелец видел, пользуется ли "
            "ботом хоть кто-нибудь\n"
            "• сколько раз нажимают каждую кнопку — общий счёт за день по всему боту, без сведений о том, кто нажимал\n"
            "• запись о пожертвовании: сумму и платёжный id Telegram\n"
            "• то, что бот в этот момент для вас делает — пока не закончит\n"
            "• в группе, куда бота добавили: id группы и её настройки /settings — пока бот не "
            "покинет группу. Бот просматривает там каждое сообщение в поисках ссылки и ничего не "
            "сохраняет из остальных; скачанная ссылка считается ссылкой того, кто её прислал, как "
            "если бы её прислали сюда (запись для дневного лимита и последняя загрузка на 10 минут). "
            "Больше об участниках группы — ничего\n"
            "\n"
            "Кроме этих 10 минут, сами файлы не хранятся: они загружаются, отправляются вам и "
            "удаляются."
        ),
        "privacy_problems_heading": "Когда что-то ломается:",
        "privacy_problems": "Каждую ошибку, которую бот кому-то показывает, он записывает сам: код ошибки, номер случая, когда это произошло и версию бота. О вас там нет ничего — ни вашего id, ни имени, ни того, что вы отправили. Сбой, о котором никто не сообщил, остаётся сбоем, поэтому бот не ждёт, пока его попросят.\n\nКнопка «Добавить мои данные» под ошибкой предлагает привязать к этому случаю четыре вещи: ваш Telegram id, ваш @username, если он есть, выбранный вами язык и то, личный это чат или группа. Все четыре сначала названы на экране, и ничего не отправляется, пока вы не нажмёте «Отправить». Того, что вы написали, и отправленных файлов там не бывает никогда.\n\nЭти четыре стираются сами через 30 дней, а /deletemydata стирает их сразу. Запись об ошибке остаётся — она действительно произошла — просто перестаёт говорить, кто на неё наткнулся. Сами записи удаляются через 180 дней.",
        "privacy_seen_by_heading": "Кто ещё это видит:",
        "privacy_seen_by": (
            "• Telegram — он передаёт каждое сообщение в обе стороны и действует по своим "
            "правилам\n"
            "• хостинг, на котором работает бот, и база данных, в которую он пишет"
        ),
        "privacy_others": (
            "• сайт, на который ведёт ссылка, и сервисы скачивания, к которым бот обращается, "
            "когда сайт не отвечает ему напрямую. Они получают ссылку и видят запрос с сервера "
            "бота, а не от вас; что они у себя пишут в логи — их дело и их правила, а не эти."
        ),
        "privacy_kept_for_heading": "Сколько это хранится:",
        "privacy_kept_for": 'Настройки и всё, что бот держит для вас, остаются, пока вы их не сотрёте или не перестанете пользоваться ботом. Отметки об использовании удаляются примерно через три месяца. Записи о платежах и баланс ⚡ хранятся дольше — по ним разбираются споры о платежах.\n\nНичего из этого не продаётся, не сдаётся в аренду и не используется для рекламы, и никому кроме перечисленных выше не передаётся.',
        "privacy_your_choices": (
            "Что можно сделать:\n"
            "/deletemydata — стереть всё, что бот хранит о вас\n"
            "/terms — для чего ботом можно пользоваться\n"
            "\n"
            "Блокировка бота в Telegram остановит его сообщения, но ничего не сотрёт — если "
            "нужно и то и другое, сначала отправьте /deletemydata."
        ),
        "terms_heading": "📜 Условия",
        "terms_use": (
            "Пользуйтесь ботом по назначению, в рамках закона и правил самого Telegram. Не "
            "используйте его, чтобы кого-то донимать, и не нагружайте сверх заданных лимитов — за то "
            "и другое аккаунт блокируется."
        ),
        "terms_specific": (
            "О скачивании: бот достаёт то, что и так открыто, и отдаёт вам. Это не обход "
            "закрытого аккаунта, и он даже не пытается им быть. Что вы потом сделаете с чужим "
            "фото, видео или постом — это между вами, автором и законом: скачать не значит "
            "присвоить. На каждого есть дневной лимит, чтобы один человек не сделал бота "
            "бесполезным для всех остальных."
        ),
        "terms_money": 'О деньгах: /donate — дело добровольное, деньги идут на то, во что обходятся боты. Платёж в Stars ещё и добавляет ⚡ кредит на конвертации в ConvertBot: 2 ⚡ за ⭐, а за ваши первые 500 Stars — 6 и за следующие 500 — 4. Всё сверх 2 — бонусный кредит, он сгорает через 90 дней после платежа. Платежи ОКОНЧАТЕЛЬНЫЕ: Stars НЕ возвращаются, а кредит НЕЛЬЗЯ вывести или обменять обратно на Stars. Единственное исключение: платёж, который бот не смог зачислить как кредит, возвращается автоматически и полностью. Все платежи проводит Telegram, бот никогда не видит номер карты. Проблема с платежом — /paysupport.',
        "terms_no_warranty": (
            "Без обещаний: бота ведёт один человек, он бесплатный и может тормозить, ошибаться "
            "или вовсе не работать без предупреждения. Держите свою копию всего, что вам важно."
        ),
        "policy_full_text": "Полный текст: {url}",
        "policy_contact": "Вопросы, жалобы или запрос по данным: {contact}",
        "delete_data_confirm": "⚠️ Это сотрёт всё, что бот хранит о вас. Отменить будет нельзя.",
        "delete_data_consequences": 'Язык, настройки подписи и качества и все записи о том, что и когда вы скачивали, будут стёрты. Дневной лимит вместе с ними обнулится. Заодно удаляется последняя загрузка, если бот ещё её хранит; более ранние загрузки здесь никогда не хранились.\n\nЗаписи о пожертвованиях останутся, без username, потому что по ним разбираются споры о платежах. Баланс ⚡ тоже останется — он ваш, если вернётесь.',
        "delete_data_button_yes": "🗑 Стереть",
        "delete_data_button_no": "↩️ Оставить мои данные",
        "delete_data_kept": "Ничего не стёрто.",
        "delete_data_done": (
            "🗑 Готово — стёрто записей: {rows}.\n"
            "\n"
            "Отправьте /start когда захотите; бот примет вас как нового."
        ),
        "delete_data_failed": (
            "Сейчас стереть не получилось — что-то сломалось на моей стороне. Попробуйте ещё раз "
            "через несколько минут."
        ),
        "language_set_confirmation": "✅ Язык изменён на русский.",
        "cancel_header": "\u274c \u041e\u0442\u043c\u0435\u043d\u0435\u043d\u043e:",
        "cancel_nothing": "\u041e\u0442\u043c\u0435\u043d\u044f\u0442\u044c \u043d\u0435\u0447\u0435\u0433\u043e — \u044f \u043d\u0438\u0447\u0435\u0433\u043e \u043e\u0442 \u0432\u0430\u0441 \u043d\u0435 \u0436\u0434\u0430\u043b.",
        "cancel_ask": "Что остановить? Вот что я жду:",
        "cancel_kept": "Хорошо — ничего не отменено.",
        "cancel_reply_box_freed": "Поле ответа снова свободно.",
        "cancel_button_all": "❌ Всё",
        "cancel_button_none": "↩️ Ничего, продолжаем",
        "cancel_button_donation": "💸 Сумма пожертвования",
        "cancel_item_donation": "\u0441\u0443\u043c\u043c\u0430 \u043f\u043e\u0436\u0435\u0440\u0442\u0432\u043e\u0432\u0430\u043d\u0438\u044f, \u043a\u043e\u0442\u043e\u0440\u0443\u044e \u044f \u0437\u0430\u043f\u0440\u043e\u0441\u0438\u043b",
        "cancel_item_stale_prompt": "старый запрос, который всё ещё ждал ответа",
        "feedback_ask": (
            "💬 Что вы хотите сказать тому, кто ведёт этих ботов? Ответьте на это сообщение.\n\nОно уходит без вашего имени, поэтому ответить вам не смогут. Остановить: /cancel"
        ),
        "feedback_placeholder": "Ваш отзыв",
        "feedback_text_only": "Отправьте текст в ответ на это сообщение или /cancel.",
        "feedback_thanks": "✅ Спасибо — отправлено анонимно.",
        "feedback_limit": "Сегодня уже {per_day} — это дневной предел. Спасибо — напишите завтра.",
        "feedback_failed": "Не отправлено — что-то пошло не так с моей стороны. Попробуйте позже.",
        "cancel_button_feedback": "💬 Отзыв",
        "cancel_item_feedback": "отзыв, который я просил написать",
        "help_brief": (
            "Как это устроено:\n\n• Пришлите ссылку — я верну то, что по ней, команда не нужна. Instagram, TikTok, X, Pinterest и Reddit.\n• /settings: подпись, качество файлов и большие файлы (больше 48 МБ, за ⚡).\n• Идеи или проблемы: /feedback\n\nОсновные команды: /settings · /balance · /cancel\nВсе команды: полное руководство, ниже или /fullhelp.\n\n"
        ),
        "help_full_button": "📖 Полное руководство",
        "help_brief_button": "↩️ Кратко",
        "help_page_everything": "Полное руководство: /fullhelp",
        "help_page_gone": "Этой страницы справки больше нет. Отправьте /help, чтобы увидеть актуальные.",
        "help_btn_download": "🔗 Отправка ссылки",
        "helppage_download": (
            "🔗 Отправка ссылки — как устроено скачивание\n"
            "\n"
            "Пришлите ссылку на пост — команда не нужна, — и я пришлю, что в нём:\n"
            "  - Instagram: рилсы, фото, карусели целиком\n"
            "  - TikTok: видео без водяного знака, фото-слайдшоу\n"
            "  - Twitter/X: видео и фото или аккуратная картинка текстового поста\n"
            "  - Pinterest: пин в полном размере\n"
            "  - Reddit: медиа из медиапоста или картинка текстового поста\n"
            "\n"
            "{quota}\n"
            "• Загрузка засчитывается, когда начинается, даже если потом не удалась. Кнопка 📄/🖼 и кнопки 📄 PDF / 🗜 ZIP под доставкой — тоже по одной загрузке. Ту же ссылку, отправленную снова, пока я её ещё качаю, я отмечу 👌 и не засчитаю.\n"
            "• В пути одновременно может быть до {waiting} ваших ссылок; качаю я по {at_once} за раз, остальные ждут очереди.\n"
            "\n"
            "Размер: бесплатно до {free_mb} МБ. {large}\n"
            "\n"
            "Под доставкой:\n"
            "• «📄 Получить несжатым файлом» (или «🖼 Получить сжатым») присылает тот же пост ещё раз в другом качестве; каждая кнопка срабатывает один раз.\n"
            "• Под альбомом: 📄 PDF собирает его фото в один PDF, 🗜 ZIP — всё в один архив. До {merge_items} файлов и {merge_mb} МБ.\n"
            "• /settings: подпись, качество и большие файлы.\n"
            "\n"
            "Пример: пришлите ссылку TikTok вроде https://www.tiktok.com/@name/video/123 — видео придёт без водяного знака."
        ),
        "helppage_download_quota": "Бесплатно — до {per_day} загрузок за любые 24 часа и до {per_hour} за любой час. Счёт идёт назад от текущего момента и не обнуляется в полночь. Когда упрётесь в лимит, я скажу, когда освободится следующая загрузка. Каждый, кто хоть раз заплатил через /donate, любую сумму, получает навсегда {donor_day} в день и {donor_hour} в час.",
        "helppage_download_quota_off": "Бесплатно.",
        "helppage_download_large": "Больше — до {max} МБ — только если включить большие файлы в /settings, в личном чате: ⚡{first} до 100 МБ, каждые следующие 100 МБ дешевле ({max} МБ — ⚡{top}). ⚡ списывается, только когда файл уже скачан, и никогда — если не вышло.",
        "helppage_download_large_off": "Больше — не принимаю.",
        "helppage_settings": (
            "⚙️ /settings — в каком виде приходят загрузки\n"
            "\n"
            "/settings показывает ваши переключатели и как они стоят. Нажмите любой, чтобы изменить; так и останется для всех следующих загрузок.\n"
            "• Подпись (сначала вкл): строка «через @{username}» под каждой загрузкой. Ещё: /caption on или /caption off.\n"
            "• Режим без потерь (сначала выкл). Вкл: загрузки приходят файлом, ровно такими, как в источнике, без сжатия Telegram, — но не проигрываются в чате, пока вы их не откроете. Выкл: приходят фото и видео, которые играют прямо в чате, сжатые Telegram. Ещё: /lossless on или /lossless off. Только для одной загрузки — кнопка 📄/🖼 под ней.\n"
            "{large}\n"
            "Пример: отправьте /lossless on, потом ссылку на Instagram — фото придут файлами, в полном качестве."
        ),
        "helppage_settings_large": "• Большие файлы (сначала выкл). Вкл: загрузку больше {free_mb} МБ я всё равно скачаю, до {max} МБ, и пришлю файлом за ⚡ кредит — ⚡{first} до 100 МБ, ⚡{top} за {max} МБ, — списываю, только когда файл уже скачан. Выкл: всё больше {free_mb} МБ отклоняется, и ничего не списывается. Только в личном чате.",
        "privacy_feedback_heading": "Если вы отправляете /feedback:",
        "privacy_feedback": (
            "Написанное уходит тому, кто ведёт этих ботов, сообщением в Telegram — вместе с "
            "названием бота и вашим языком, но без имени, username и id. Копия хранится в базе "
            "ботов 30 дней, потом удаляется. Читает его только он, и ответить на него он не может."
        ),
        "start_greeting": (
            "Здравствуйте! Пришлите мне ссылку (Instagram, TikTok, Pinterest, Reddit "
            "или Twitter/X), и я скачаю это для вас.\n\n"
        ),
        "help_text": "Присылайте ссылку в любое время — команда не нужна:\n  - Instagram: reels, фото, карусели целиком\n  - TikTok: видео без водяного знака и фото-слайдшоу\n  - Twitter/X: видео и фото или картинка текстового поста\n  - Pinterest: пин в полном размере\n  - Reddit: медиа или картинка текстового поста\n\nПод альбомом: 📄 PDF собирает фото в один PDF, 🗜 ZIP — всё в один архив.\n\nКоманды:\n/settings - подпись, качество и большие файлы (больше 48 МБ, за ⚡)\n/caption on|off - подпись \"via @{username}\"\n/lossless on|off - загрузки несжатыми файлами\n/cancel - остановить то, чего я жду\n/feedback - написать тому, кто ведёт ботов (анонимно)\n/donate - помочь с хостингом (необязательно)\n/balance - ваш ⚡ кредит\n/paysupport - проблема с оплатой\n/en, /uz, /rus - сменить язык (или /language)\n/privacy - что я храню о вас\n/terms - для чего можно использовать бота\n/deletemydata - удалить всё, что я о вас храню\n/start - начать сначала\n/fullhelp - это руководство (/help - краткое)\n\n⚠️ Платежи окончательны — Stars НЕ возвращаются, ⚡ кредит НЕЛЬЗЯ вывести.\n\n",
        "caption_state_on": "ВКЛ",
        "caption_state_off": "ВЫКЛ",
        "settings_heading": "⚙️ Ваши настройки. Нажмите, чтобы изменить.",
        "caption_status": "Подпись сейчас {state}.",
        "caption_turned": "Подпись к загрузкам теперь {state}.",
        "caption_toggle_answer": "Подпись теперь {state}.",
        "lossless_state_on": "ВКЛ",
        "lossless_state_off": "ВЫКЛ",
        "lossless_status": "Режим без потерь {state}.\n\nВКЛ: загрузки приходят исходными файлами — без сжатия, но без превью в чате.\nВЫКЛ: фото и видео, которые играют в чате, со сжатием Telegram.",
        "lossless_turned": "Загрузка без потерь теперь {state}.",
        "lossless_toggle_answer": "Режим без потерь теперь {state}.",
        "large_state_on": "ВКЛ",
        "large_state_off": "ВЫКЛ",
        "large_status": "📦 Большие файлы {state}.\n\nВКЛ: загрузки больше {free} МБ, до {max} МБ, приходят файлом — ⚡{per} до 100 МБ, ⚡{top} за {max} МБ. Списывается, только когда файл пришёл.\nВЫКЛ: всё больше {free} МБ отклоняется; ничего не списывается.",
        "large_toggle_answer": "Большие файлы: {state}.",
        "large_files_turned_on": "📦 Большие файлы включены. Пришлите ссылку снова — ⚡{per} до 100 МБ, списывается, когда файл придёт. Выключить: /settings",
        "large_files_turn_on_button": "📦 Включить большие файлы",
        "large_files_off": "📦 Этот файл больше {free} МБ. Включите большие файлы кнопкой ниже (или в /settings) и пришлите ссылку снова — ⚡{per} до 100 МБ.",
        "large_files_private_only": "📦 Этот файл больше {free} МБ. Такие файлы я присылаю только в личном чате — отправьте ссылку туда.",
        "large_files_no_credit": "📦 Этот файл больше {free} МБ, это стоит минимум ⚡{per}, а на балансе ⚡{balance}. Пополните через /donate и пришлите ссылку ещё раз.",
        "large_files_fetching": "📦 Больше {free} МБ — скачиваю как большой файл (⚡{per} до 100 МБ, списывается, когда придёт).",
        "large_files_too_big": "📦 Этот файл больше {max} МБ — больше Telegram мне отправлять не позволяет. Попробуйте ролик покороче.",
        "large_files_short": "📦 Файл на {mb} МБ (⚡{price}), а на балансе ⚡{balance}, поэтому ничего не списано. Пополните через /donate и пришлите ссылку снова.",
        "large_files_sending": "📦 {mb} МБ скачано. Отправляю...",
        "large_files_send_failed": "📦 Я скачал файл, но не смог его вам отправить. ⚡{price} вернулись — на балансе ⚡{balance}.",
        "large_files_charged": "📦 {mb} МБ, отправлено. За это ⚡{price} — на балансе теперь ⚡{balance}.",
        "download_credit_caption": "⬇️ через @{username}",
        "downloading": "Скачивание...",
        "queued": "⏳ Занят другой загрузкой — ваша начнётся через мгновение.",
        "download_queue_full": 'У вас уже {count} ссылок в работе. Присылайте ещё, когда какие-то придут.',
        "download_short_on_space": 'Сейчас не хватает временного места — пришлите ссылку снова через пару минут.',
        "restarting_send_again": "🔄 Сейчас обновляюсь — подождите несколько секунд и отправьте ещё раз.",
        "update_soon_try_later": '🔧 Сейчас меня обновляют, поэтому я не могу начать ничего нового — попробуйте снова примерно через {minutes} мин. Я напишу, когда вернусь.',
        "update_soon_try_later_soon": '🔧 Сейчас меня обновляют, поэтому я не могу начать ничего нового — попробуйте снова чуть позже. Я напишу, когда вернусь.',
        "update_will_reset": '🔧 Внимание: меня скоро обновят, и то, что вы сейчас начали, будет сброшено. Через несколько минут сможете начать заново.',
        "update_done_try_now": '✅ Обновление завершено — можете пробовать снова.',
        "gift_granted": '🎁 Нас уже больше 100 — спасибо! Вам начислено {amount} ⚡ бесплатного кредита, он действует до {date}. Этого хватит примерно на {conversions} платных конвертаций в {convert} или на скачивание больших файлов в {download}. Ваш баланс: /balance',
        "gift_claimed": "🎁 Подарок: {amount} ⚡ бесплатного кредита! У Mumu ботов уже больше {users} пользователей — это наше спасибо.\nОн действует до {date} (UTC). Этого хватит на платные конвертации (около {conversions}) в {convert} или на скачивание больших файлов в {download}.\nВаш баланс: /balance",
        "download_failed": "Не удалось это скачать: {error}",
        "download_blocked": "🚫 Сайт меня не пустил — дело не в вашей ссылке. Попробуйте чуть позже.",
        "download_missing": "🔍 Не нашёл этот пост — возможно, он удалён или закрыт.",
        "download_too_big": '📦 Это больше, чем Telegram позволяет мне отправить. Попробуйте ролик покороче.',
        "download_all_routes_failed": "😕 Не получилось. Попробуйте через минуту — если не выходит снова, дело в сайте, а не в ссылке.",
        "fetching": "Загрузка...",
        "reddit_fetch_failed": "Не удалось получить этот пост Reddit: {error}",
        "twitter_fetch_failed_link": "Не удалось получить содержимое поста прямо сейчас — вот ссылка: {url}",
        "unrecognized_message": (
            "Такую ссылку я не знаю. Пришлите ссылку из Instagram, TikTok, Pinterest, Reddit или X, или загляните в /help."
        ),
        "redeliver_as_file": "📄 Получить несжатым файлом",
        "redeliver_as_compressed": "🖼 Получить сжатым",
        "redeliver_expired": (
            "Срок этой кнопки истёк — пришлите ссылку снова."
        ),
        "album_delivered": "Выше {count} файл(ов).",
        "merge_too_big": "📦 Слишком много для одного файла (до {items} файлов, {mb} МБ). Всё есть в чате выше.",
        "merge_picture_too_big": "📦 Одна из картинок слишком большая для PDF (больше {mp} МП). Картинки — в чате выше.",
        "merge_no_photos": "📄 Для PDF нужно хотя бы два фото. Файлы — в чате выше.",
        "merge_failed": "😕 Не удалось собрать их в один файл. Файлы выше в порядке — пришлите ссылку снова, чтобы повторить.",
        "unknown_command": "Я не знаю такую команду. Отправьте /help, чтобы увидеть, что я умею.",
        # 2.2.0: the quality picker and group chats (bot.py).
        "quality_heading": '📹 {title}',
        "quality_paid_note": '⚡ — больше {free} МБ: платно, ⚡ кредитом (/settings, большие файлы).',
        "quality_choose": 'Выберите качество ↓',
        "quality_more_button": '🎵 MP3 · ✂️ Обрезка · другое ↗️',
        "quality_expired": 'Этот выбор устарел — отправьте ссылку ещё раз.',
        "quality_picked": '⏳ {height}p — загружаю...',
        "group_hello": "👋 Привет! Отправьте сюда ссылку из Instagram, TikTok, Pinterest, Reddit или X — и я пришлю видео.\n\n/settings — что и как я скачиваю здесь.",
        "group_cant_see": '⚠️ Сейчас я вижу только сообщения, адресованные мне. Чтобы видеть ссылки участников, сделайте меня админом группы.',
        "group_settings_text": '⚙️ Что я делаю в этой группе — нажмите, чтобы изменить.\n\nКачество работает там, где сайт даёт выбор; остальные ссылки приходят такими, какими их отдаёт сайт. Файлы больше {free} МБ в группах не отправляются.',
        "group_on": '✅ Скачиваю ссылки',
        "group_paused": '⏸ На паузе — нажмите, чтобы продолжить',
        "group_other_videos": '🎬 Другие видеосайты',
        "group_quality_best": 'Лучшее',
        "group_as_video": '📹 Отправляю видео',
        "group_as_file": '📄 Отправляю файлом',
        "group_credit_on": '🏷 Подпись бота: вкл',
        "group_credit_off": '🏷 Подпись бота: выкл',
        "group_anyone": '🔓 Менять может любой',
        "group_admins_only": '🔒 Менять могут только админы',
        "group_admins_alert": 'Это могут менять только админы группы.',
        "group_saved": 'Сохранено ✅',
    },
}


# Every problem a person can run into ends with its code, and the code is what
# puts a "Report the issue" button under it -- see problems.py. Imported here,
# below the tables, because it is pure data and nothing above needs it.
import problems  # noqa: E402

_BOT = "downloader_bot"


def t(lang: str | None, key: str, **kwargs) -> str:
    table = STRINGS.get(lang) or STRINGS["en"]
    template = table.get(key) or STRINGS["en"].get(key, key)
    text = template.format(**kwargs) if kwargs else template
    return text + problems.code_line(problems.code_for(_BOT, key))


async def get_lang(user_id: int, context) -> str:
    """Cached in context.user_data to avoid a DB round-trip on every handler
    call. Falls back to "en" for a user who hasn't chosen a language yet
    (only reachable outside /start's first-run gate, e.g. someone who sends
    a link before ever running /start)."""
    cached = context.user_data.get("lang")
    if cached:
        return cached
    lang = await asyncio.to_thread(db.get_user_language, user_id) or "en"
    context.user_data["lang"] = lang
    return lang


# ---------------------------------------------------------------------------
# The slash menu, in the other two languages
# ---------------------------------------------------------------------------
# English lives in BOT_COMMANDS in bot.py, where the menu can be read by
# reading the file. These are the same commands for a client whose language is
# Uzbek or Russian; shared_features.publish_commands() sends one list per
# language and Telegram picks the matching one.
#
# Deliberately absent: /language, /en, /uz and /rus. Each of the three is
# written in the language it switches TO, and /language is written in all
# three at once, because they are the way back for somebody who chose the
# wrong one. Translating them would make the menu of a Russian client offer
# three lines of Russian, one of which is the only route out.
#
# A command missing from here keeps its English description rather than
# vanishing from that language's menu -- a half-translated menu is a menu
# with commands missing, and a missing command reads as a bot that cannot do
# the thing. Kept honest by tests/test_menu.py, which fails on a command that
# has no entry here and on an entry naming a command that no longer exists.

COMMAND_MENU = {
    "uz": {
        "start": "Bu bot nima qiladi va qanday boshlash kerak",
        "settings": "Izoh va fayl sifati sozlamalari",
        "cancel": "Kutayotgan amalimni to'xtatish",
        "help": "Nimalar qila olaman",
        "feedback": "Taklif yoki muammo haqida yozish",
        "balance": "⚡ kreditingiz",
        "donate": "Server xarajatlariga hissa qo'shish",
        "paysupport": "To'lov bilan muammo",
        "privacy": "Siz haqingizda nimalarni saqlayman",
        "terms": "Botdan foydalanish shartlari",
        "deletemydata": "Siz haqingizdagi hamma narsani o'chirish",
    },
    "ru": {
        "start": "Что этот бот делает и с чего начать",
        "settings": "Подпись и качество файлов",
        "cancel": "Остановить то, чего я жду",
        "help": "Что я умею",
        "feedback": "Предложить идею или сообщить о проблеме",
        "balance": "Ваш ⚡ кредит",
        "donate": "Помочь с расходами на хостинг",
        "paysupport": "Проблема с оплатой",
        "privacy": "Что я о вас храню",
        "terms": "Для чего можно пользоваться ботом",
        "deletemydata": "Удалить всё, что я о вас храню",
    },
}

# The group-chat menu (bot.py's GROUP_COMMANDS), in the other two languages.
GROUP_COMMAND_MENU = {
    "uz": {"settings": "Bu guruhda nimani yuklayman", "help": "Guruhda qanday ishlayman"},
    "ru": {"settings": "Что я скачиваю в этой группе", "help": "Как я работаю в группе"},
}
