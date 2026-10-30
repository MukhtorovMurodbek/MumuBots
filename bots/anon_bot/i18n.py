"""Translation strings for AnonBot's end-user-facing text (English, Uzbek,
Russian). Deliberately duplicated per bot -- same "no shared files between
bots" independence as shared_features.py -- but the STRINGS content here is
specific to this bot's own commands and flows.

Admin-only output (/dbdump, /status) is intentionally NOT translated -- see
build_status_text/error_summary/detect_host_environment in
shared_features.py, and bot.py's dbdump_command/status_command, left as
plain English since only the bot owner reads them.

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
        "maintenance_note": "🔧 AnonBot is closed for a short repair. Your conversations are safe, but nothing sent now is delivered or kept. Please write again later.",
        "flood_wait": "Too fast — wait about {seconds} s and carry on.",
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
        "donate_invoice_description": "Helps pay for hosting, and adds {credited} ⚡ of credit for ConvertBot.",
        "donate_invoice_label": "Hosting contribution",
        "donate_invoice_description_fiat": 'A one-time voluntary donation towards hosting costs. Thank you!',
        "donate_prompt_credit": "Each ⭐ gives ⚡ credit for ConvertBot. Your next {left} ⭐ give {each} ⚡ each ({mult}×): {rate} ⚡ plus a bonus that expires {days} days after payment. ⚡ can NOT be withdrawn or turned back into Stars.",
        "donate_prompt_credit_base": "Each ⭐ gives {rate} ⚡ of credit for ConvertBot. ⚡ can NOT be withdrawn or turned back into Stars.",
        "donate_invoice_error": "⚠️ Telegram wouldn't create that invoice: {error}",
        "stars_unit": "Stars",
        "donate_custom_ask": "How many {unit} would you like to donate? Reply with a number.",
        "donate_invalid_amount_retry": "That's not a valid amount — send /donate to try again.",
        "donate_thanks": "🙏 Thank you for the {amount} ⭐ — genuinely appreciated!",
        "topup_thanks": "🙏 Thank you for {stars} ⭐!\n\nYou got {total} ⚡ of credit in {convert_bot} for conversions. Balance there: {balance} ⚡.",
        "topup_thanks_bonus": 'Of that, {bonus} ⚡ is bonus credit and expires on {date}.',
        "credit_cannot_be_withdrawn": "⚡ is credit for ConvertBot conversions and can NOT be withdrawn or turned back into Stars. Payment problem? /paysupport",
        "topup_refunded": "⚠️ Your {stars} ⭐ payment went through, but the ⚡ didn't reach your balance, so the {stars} ⭐ were refunded. Please try again in a few minutes.",
        "topup_failed": "⚠️ Your {stars} ⭐ payment went through, but the ⚡ didn't reach your balance. Nothing is lost — the owner will add it by hand. Follow up: /paysupport",
        "paysupport_text": "💳 Help with a payment\n\nPayments are FINAL: Stars are NOT refunded, and ⚡ can NOT be withdrawn or turned back into Stars. Only a payment that couldn't become credit is refunded, automatically and in full.\n\nCharged with no credit, or charged twice? Write to {contact} with the date, the amount and your Telegram ID, {user_id}. It's put right with credit.\n\n/balance lists your payments.",
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
            "A permanent link that lets anyone write to you anonymously."
        ),
        "bot_description": (
            "Get a link, post it anywhere, and people can write to you anonymously. You answer in threaded conversations, several at once.\n\nThe bot knows who talks to whom, so replies reach the right thread, but never stores what anyone writes. /privacy\n\nEnglish, Uzbek and Russian."
        ),
        # ---- shared policy keys (/privacy, /terms, /deletemydata) ----
        "privacy_heading": "🔒 Privacy",
        "privacy_kept_heading": "What this bot keeps:",
        "privacy_stored": (
            "• your Telegram ID and language\n• your inbox link, if you made one\n• who talks to whom: each conversation as the pair of IDs behind it plus a number, so replies reach the right thread\n• which message belongs to which conversation, by message ID, and when it was relayed\n• who you blocked\n• that you were shown a one-time announcement, so it shows only once\n• a timestamp each time you use the bot, to count users\n• daily button-tap counts for the whole bot, not per person\n• donations: the amount and Telegram's payment ID\n\nMessages and reactions are never stored: they pass straight through and live only in the two Telegram chats, where each side can delete their copy. The one exception is /feedback, below.\n\nAnonymous means anonymous to the person you write to: they never see your name, username or ID. The bot itself knows who is paired — that is how replies work."
        ),
        "privacy_problems_heading": "When something goes wrong:",
        "privacy_problems": (
            "Every error is logged without you: its code, an incident number, the time and the bot's version.\n\n\"Add my details\" under an error attaches your Telegram ID, @username, language and chat type (private or group) to that one incident — only if you tap Send. Never your messages or files. They are cleared after 30 days, or at once with /deletemydata. Error records are deleted after 180 days."
        ),
        "privacy_seen_by_heading": "Who else sees it:",
        "privacy_seen_by": (
            "• Telegram, which carries every message\n• the hosting provider and the database the bot uses"
        ),
        "privacy_others": (
            "• nobody else — messages go through Telegram only, and no outside service is used"
        ),
        "privacy_kept_for_heading": "How long it stays:",
        "privacy_kept_for": "Settings stay until you erase them or stop using the bot. Usage times go after about three months. Payment records and your ⚡ balance stay longer, for payment disputes.\n\nNothing is sold, used for ads, or given to anyone not named above.",
        "privacy_your_choices": (
            "What you can do:\n/deletemydata — erase what this bot holds on you\n/terms — the rules\n\nBlocking the bot erases nothing, so send /deletemydata first."
        ),
        "terms_heading": "📜 Terms",
        "terms_use": (
            "Use it within the law and Telegram's terms. Harassing anyone or pushing past the bot's limits gets an account blocked."
        ),
        "terms_specific": (
            "Messages: being anonymous doesn't make threats, harassment or anything illegal OK. Inbox owners can block senders for good, and are responsible for what they invite people to send."
        ),
        "terms_money": "Money: /donate is voluntary and helps pay for hosting. Stars also add ⚡ credit for ConvertBot: 2 ⚡ per ⭐, or 6 for your first 500 Stars and 4 for the next 500; the part above 2 is bonus that expires 90 days after payment. Payments are FINAL: Stars are NOT refunded and credit can NOT be withdrawn — except a payment that couldn't become credit, which is refunded automatically. Telegram handles payments; the bot never sees a card. Problems: /paysupport",
        "terms_no_warranty": (
            "No promises: one person runs this for free, and it can be slow, wrong or down without warning. Keep your own copies of what matters."
        ),
        "policy_full_text": "Full text: {url}",
        "policy_contact": "Questions, complaints or a data request: {contact}",
        "delete_data_confirm": "⚠️ This erases what this bot holds on you. There is no undo.",
        "delete_data_consequences": "Your inbox link stops working for good, open conversations can't be replied to any more, and your block list goes.\n\nSent messages stay in the Telegram chats; they were never stored here.\n\nPayment records stay, without your username, for payment disputes. Your ⚡ balance stays too.",
        "delete_data_button_yes": "🗑 Erase it",
        "delete_data_button_no": "↩️ Keep my data",
        "delete_data_kept": "Nothing was erased.",
        "delete_data_done": (
            "🗑 Done — {rows} record(s) erased. Send /start any time to begin fresh."
        ),
        "delete_data_failed": (
            "Couldn't erase it — something went wrong on my side. Please try again in a few minutes."
        ),
        "language_set_confirmation": "✅ Language set to English.",
        "cancel_header": "\u274c Cancelled:",
        "cancel_nothing": "Nothing to cancel.",
        "cancel_ask": "What should I stop? Here's what I'm waiting on:",
        "cancel_kept": "OK — nothing cancelled.",
        "cancel_reply_box_freed": "Your reply box is free again.",
        "cancel_button_all": "❌ All of it",
        "cancel_button_none": "↩️ Nothing, keep going",
        "cancel_button_donation": "💸 Donation amount",
        "cancel_item_donation": "the donation amount I asked you for",
        "cancel_item_stale_prompt": "an old unanswered question",
        "cancel_item_anon_session": "the anonymous chat you had open — tap the link again to start a new one",
        "cancel_button_anon_session": "💬 The anonymous chat you're in",
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
        "privacy_feedback_heading": "If you send /feedback:",
        "privacy_feedback": (
            "It reaches the person who runs these bots with the bot's name and your language — not your name, username or ID. They can't reply. A copy is kept for 30 days."
        ),
        # ---- anon_bot-specific keys ----
        "start_greeting": "Hey! This bot runs anonymous-question inboxes.\n\n",
        "help_brief": (
            "How it works:\n\n• /link gives you a permanent link. Whoever taps it can write to you here anonymously.\n• To answer, reply to their message (swipe it).\n• Got someone's link? Tap it and write.\n• Reactions reach the other person (standard emoji only).\n• Ideas or problems: /feedback\n\nMain commands: /link · /conversations · /cancel\nEverything else: the full guide, below or /fullhelp.\n\n"
        ),
        "help_full_button": "📖 Full guide",
        "help_brief_button": "↩️ Short version",
        "help_page_everything": "The full guide: /fullhelp",
        "help_page_gone": "That help page is gone. Send /help.",
        "helppage_link": (
            "🔗 /link — your anonymous inbox link\n\nPost it anywhere: whoever opens it can write to you here anonymously. Each open starts a new conversation. The link stays the same until you make a new one.\nButtons under it:\n• ⏸️ Pause: no new conversations until you resume (/pause, /resume).\n• 🔄 New link: the old one stops starting conversations (/newlink).\n• Counts of guests and conversations (/stats).\nCurrent conversations keep working either way.\n\nExample: send /link and put the link in your Instagram bio."
        ),
        "helppage_which": (
            "❓ /which — which conversation is this message from?\n\nReply to any message here with /which.\n• Inbox owner: I say the conversation's number.\n• Guest: I point to where it starts.\n\nExample: \"{example}\""
        ),
        "reactions_update_note": "🆕 You can now react to messages here — the other person sees it. /help for more.",
        "gift_ad": "🎉 Mumu bots have passed {users} unique users, so we decided to give out {amount} ⚡ of free credit! You can claim it and use it in {download} and {convert}.\n\n<i>To claim it, send any message to either bot: the gift is added automatically. Claim it by {claim_by}; once claimed, it lasts {days} days.</i>",
        "reaction_not_passed": "⚠️ The other person won't see that reaction — only standard emoji are passed on.",
        "reaction_one_only": "⚠️ The other person sees only your newest reaction — one per message is passed on.",
        "help_text": "Two ways to use this bot:\n\nYour own inbox — /link gives you a permanent link. Post it anywhere (bio, channel, story). Whoever taps it can message you here anonymously.\nTo answer, reply to their message: swipe it, or press and hold it and choose Reply. Every answer must be a reply, so it reaches the right conversation.\n\nSomeone else's link — tap it and write. Your first message goes straight through; after that, reply to the message you're answering. Tapping the link again starts a new conversation.\n\nReactions: react to a message and the other person sees the same reaction, shown as mine. One per message, only these:\n{reactions}\n⚠️ Telegram Premium (custom emoji) reactions can NOT be passed on.\n\nCommands:\n/link - your inbox link\n/conversations - your conversations, open and archived\n/which - reply with it to a message: which conversation is it from?\n/archive - reply with it to a message to archive that conversation\n/newlink, /pause, /resume - also buttons under /link\n/blocked - who you've blocked\n/stats - your counts\n/cancel - stop something I'm waiting for\n/feedback - write to whoever runs these bots (anonymous)\n/donate - help pay for hosting (optional)\n/balance - your ⚡ credit\n/paysupport - trouble with a payment\n/en, /uz, /rus - switch language (or /language)\n/privacy - what I keep about you\n/terms - what I may be used for\n/deletemydata - delete everything I hold on you\n/start - start over\n/fullhelp - this guide (/help - the short one)\n\n⚠️ Payments are final — Stars are NOT refunded, and ⚡ credit can NOT be withdrawn.\n\n",
        "follow_link_invalid": "That link isn't valid — it may have been reset by whoever shared it. Ask them for a fresh one.",
        "follow_link_own": "That's your own link — share it with others: /link",
        "follow_link_blocked": "You're not able to message this person.",
        "follow_link_paused": "This person isn't accepting new anonymous messages right now — try again later.",
        "follow_link_started": (
            "You're writing to someone anonymously — they won't see who you are.\n\nType your first message below. After that, reply to (swipe) the message you're answering."
        ),
        "follow_cancel_button": "✖️ Cancel",
        "follow_link_cancelled": "Cancelled.",
        "link_status_paused": "paused — not accepting new conversations",
        "link_status_active": "accepting messages",
        "link_message": (
            "Your anonymous inbox link ({status}):\n{url}\n\nPost it anywhere — whoever opens it can message you here anonymously."
        ),
        "link_button_pause": "⏸️ Pause",
        "link_button_resume": "▶️ Resume",
        "link_button_newlink": "🔄 New link",
        "anonlink_no_link_yet": "No link yet — send /link first.",
        "anonlink_paused_answer": "Paused.",
        "anonlink_resumed_answer": "Resumed.",
        "anonlink_newlink_answer": "New link made — the old one no longer starts conversations.",
        "newlink_message": "New link (the old one no longer starts new conversations):\n{url}",
        "pause_message": (
            "Paused — no new conversations until /resume. Current ones still work."
        ),
        "resume_message": "Resumed — your link takes new conversations again.",
        "stats_message": "{followers} distinct guest(s) have messaged you, across {conversations} conversation(s) total.",
        # ---- /conversations: the owner's own view of their own inbox ----
        "convs_title": "\U0001f4e5 Your inbox",
        "convs_title_archived": "\U0001f5c4 Archived conversations",
        "convs_summary": "{open} open \u00b7 {archived} archived",
        "convs_summary_blocked": "{open} open \u00b7 {archived} archived \u00b7 {blocked} blocked (see /blocked)",
        "convs_empty_open": (
            "Nothing open right now. Share your /link to get messages."
        ),
        "convs_empty_archived": (
            "Nothing archived yet."
        ),
        "convs_row_button": "#{conv_number} \u00b7 {when}",
        "convs_show_archived": "\U0001f5c4 Archived",
        "convs_show_open": "\U0001f4e5 Open",
        "convs_page": "{page}/{pages}",
        "convs_prev": "\u2039",
        "convs_next": "\u203a",
        "conv_card": "Conversation #{conv_number}\n\nOpened {started}\nLast message {when}",
        "conv_card_archived": "Archived {archived}",
        "conv_back_button": "\u2039 Back",
        "conv_jump_button": "\u2934\ufe0f Go to the start",
        "conv_archive_button": "\U0001f5c4 Archive",
        "conv_unarchive_button": "\u21a9\ufe0f Unarchive",
        "conv_archived_answer": "Archived — it comes back if they write again.",
        "conv_unarchived_answer": "Back in your open list.",
        "conv_gone": "That conversation is no longer on record.",
        "jump_bookmark": (
            "📍 Conversation #{conv_number} starts here. Reply to this to write in it."
        ),
        "jump_no_anchor": (
            "I can't find the start of conversation #{conv_number} any more — it was silent for over {days} days."
        ),
        # ---- /which: which conversation does this message belong to ----
        "which_needs_reply": (
            "Reply to a message with /which and I'll say which conversation it's from."
        ),
        "which_unknown": (
            "That message isn't in an open conversation (it may be yours, or archived)."
        ),
        "which_owner": "That message is in conversation #{conv_number}.",
        "which_follower": "That message is in the conversation that starts here.",
        "which_follower_no_anchor": "That message is in a conversation you opened {started}.",
        # ---- how long ago, without a date and without a timezone ----
        "rel_now": "just now",
        "rel_minutes": "{n} min ago",
        "rel_hours": "{n} h ago",
        "rel_days": "{n} d ago",
        "blocked_none": "You haven't blocked anyone.",
        "blocked_header": "Blocked guests:",
        "blocked_guest_line": "  \U0001f6ab Guest from conversation(s) {conversations}",
        "blocked_guest_line_unknown": "  \U0001f6ab Guest (no conversation on record)",
        "blocked_unblock_button": "↩️ Unblock (conv. {conversations})",
        "blocked_unblock_button_unknown": "↩️ Unblock",
        "reply_button": "↩️ How to reply",
        "block_button": "\U0001f6ab Block",
        "not_your_conversation": "Not your conversation.",
        "blocked_answer": "Blocked.",
        "unblock_button_labelled": "↩️ Unblock conversation #{conv_number}",
        "too_fast": "Too fast — wait {seconds} s and try again.",
        "edit_not_relayed": (
            "Edits don't go through — they still see your original message. Send the correction as a new reply."
        ),
        "unblocked_answer": "Unblocked.",
        "delivery_blocked": "Your message couldn't be delivered.",
        "inbox_gone": "This inbox no longer exists.",
        "incoming_header": "\U0001f4e9 Conversation #{conv_number}",
        "incoming_header_follower": "\U0001f4ec New message",
        "delivery_forbidden": (
            "Not sent — this person hasn't started the bot yet."
        ),
        "delivery_failed": "Couldn't deliver that message: {error}",
        "sent_confirmation": "Sent ✅",
        "reply_no_match": "Your message was not sent — I couldn't match that reply to a conversation.",
        "must_reply": (
            "Not sent. Reply to the message above (swipe it) to send it there."
        ),
        "reply_button_hint": (
            "Swipe this message to the right, or press and hold it and choose Reply."
        ),
        "must_reply_opening": (
            "Not sent yet. Reply to the message above (swipe it) to make this your first message."
        ),
        "must_reply_ambiguous": (
            "Not sent — several conversations are open here. Reply to the message you're answering (swipe it)."
        ),
        "restarting_send_again": (
            "🔄 I'm being updated — send that again in a few seconds."
        ),
        "update_soon_try_later": "🔧 I'm about to be updated — please try again in about {minutes} min. I'll message you when I'm back.",
        "update_soon_try_later_soon": "🔧 I'm being updated — please try again shortly. I'll message you when I'm back.",
        "update_will_reset": "🔧 I'm about to be updated, and what you're doing now will be reset. You can start again in a few minutes.",
        "update_done_try_now": "✅ Update done — try again now.",
        "gift_granted": "🎁 We passed 100 users — thank you! Here's {amount} ⚡ of free credit, good until {date}: about {conversions} paid conversions in {convert} or big downloads in {download}. /balance",
        "reply_forbidden": "Couldn't deliver your reply — they may have blocked or left the bot.",
        "reply_failed": "Couldn't deliver your reply: {error}",
        "delivered_confirmation": "Delivered ↩️",
        "reply_stale": (
            "Not sent — that message isn't in an open conversation. Reply to a message I delivered here."
        ),
        "generic_nudge": "Got a link from someone? Tap it first. Want your own inbox? /link",
        "export_removed": "📦 /export has been removed: the bot now keeps less about your conversations. Your messages stay in your Telegram chat.",
        "archive_needs_reply": (
            "Reply to a message from the conversation with /archive, or pick one in /conversations."
        ),
        "archive_not_owner": (
            "Only an inbox owner can archive conversations."
        ),
        "archive_already": "Conversation #{conv_number} is already archived.",
        "archive_confirm": (
            "Archive conversation #{conv_number}?\n\nNothing is deleted and they aren't told. It comes back if they write again."
        ),
        "archive_button_yes": "🗂 Archive #{conv_number}",
        "archive_button_no": "Keep it open",
        "archive_done": "🗂 Conversation #{conv_number} archived. /conversations to see it.",
        "archive_kept": "Left open.",
        "unknown_command": "I don't recognize that command. Send /help to see what I can do.",
    },
    "uz": {
        "maintenance_note": "🔧 AnonBot qisqa ta'mirda. Suhbatlaringiz joyida, lekin hozir yuborilgan narsa yetkazilmaydi va saqlanmaydi. Keyinroq yana yozing.",
        "flood_wait": "Juda tez — taxminan {seconds} soniya kutib, davom eting.",
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
        "donate_invoice_description": "Server xarajatlariga yordam beradi va ConvertBot uchun {credited} ⚡ kredit qo'shadi.",
        "donate_invoice_label": 'Xayriya',
        "donate_invoice_description_fiat": 'Server xarajatlari uchun bir martalik ixtiyoriy xayriya. Rahmat!',
        "donate_prompt_credit": "Har bir ⭐ ConvertBot uchun ⚡ kredit beradi. Keyingi {left} ⭐ ning har biri {each} ⚡ beradi ({mult}×): {rate} ⚡ va to'lovdan {days} kun o'tgach yo'qoladigan bonus. ⚡ ni yechib olib yoki Stars'ga aylantirib BO'LMAYDI.",
        "donate_prompt_credit_base": "Har bir ⭐ ConvertBot uchun {rate} ⚡ kredit beradi. ⚡ ni yechib olib yoki Stars'ga aylantirib BO'LMAYDI.",
        "donate_invoice_error": "⚠️ Telegram to'lov hisobini yaratmadi: {error}",
        "stars_unit": "Stars (yulduzcha)",
        "donate_custom_ask": 'Qancha {unit} xayriya qilmoqchisiz? Faqat sonni yozib yuboring.',
        "donate_invalid_amount_retry": "Miqdor noto'g'ri — qaytadan urinish uchun /donate yuboring.",
        "donate_thanks": '🙏 {amount} ⭐ uchun katta rahmat!',
        "topup_thanks": "🙏 {stars} ⭐ uchun rahmat!\n\n{convert_bot} da konvertatsiyalar uchun {total} ⚡ kredit oldingiz. U yerdagi balans: {balance} ⚡.",
        "topup_thanks_bonus": 'Shundan {bonus} ⚡ — bonus kredit, uning muddati {date} kuni tugaydi.',
        "credit_cannot_be_withdrawn": "⚡ — ConvertBot'dagi konvertatsiyalar uchun kredit, uni yechib olib yoki Stars'ga aylantirib BO'LMAYDI. To'lov bilan muammo? /paysupport",
        "topup_refunded": "⚠️ {stars} ⭐ to'lovingiz o'tdi, lekin ⚡ balansingizga tushmadi, shuning uchun {stars} ⭐ qaytarildi. Bir necha daqiqadan keyin qayta urinib ko'ring.",
        "topup_failed": "⚠️ {stars} ⭐ to'lovingiz o'tdi, lekin ⚡ balansingizga tushmadi. Hech narsa yo'qolmaydi — egasi uni qo'lda qo'shib qo'yadi. Kuzatish: /paysupport",
        "paysupport_text": "💳 To'lov bo'yicha yordam\n\nTo'lovlar QAYTARILMAYDI: Stars qaytarilMAYDI, ⚡ ni yechib olib yoki Stars'ga aylantirib BO'LMAYDI. Faqat kreditga aylanmagan to'lov avtomatik ravishda to'liq qaytariladi.\n\nPul yechildi-yu kredit kelmadimi yoki ikki marta yechildimi? {contact} ga sana, miqdor va Telegram ID raqamingizni ({user_id}) yozing. Kredit bilan to'g'rilab beriladi.\n\n/balance to'lovlaringizni ko'rsatadi.",
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
            "Sizga anonim yozish imkonini beruvchi doimiy havola."
        ),
        "bot_description": "Havola oling, uni istalgan joyga joylang — odamlar sizga anonim yoza oladi. Siz bir nechta suhbatda javob berasiz.\n\nBot javoblar to'g'ri suhbatga borishi uchun kim kim bilan gaplashayotganini biladi, lekin yozilgan narsani hech qachon saqlamaydi. /privacy\n\nO'zbek, ingliz va rus tillarida.",
        # ---- shared policy keys (/privacy, /terms, /deletemydata) ----
        "privacy_heading": "🔒 Maxfiylik",
        "privacy_kept_heading": "Bu bot nimalarni saqlaydi:",
        "privacy_stored": "• Telegram ID raqamingiz va tilingiz\n• quti havolangiz, agar yaratgan bo'lsangiz\n• kim kim bilan gaplashayotgani: har bir suhbat ortidagi ikki ID va raqam sifatida — javoblar to'g'ri suhbatga borishi uchun\n• qaysi xabar qaysi suhbatga tegishli (xabar ID si bo'yicha) va qachon yetkazilgani\n• kimlarni bloklaganingiz\n• bir martalik e'lonni ko'rganingiz — u faqat bir marta ko'rsatilishi uchun\n• har safar botdan foydalanganingiz vaqti — foydalanuvchilarni sanash uchun\n• butun bot bo'yicha kunlik tugma bosishlar soni, odamlar bo'yicha emas\n• xayriyalar: miqdor va Telegram to'lov ID si\n\nXabarlar va reaksiyalar hech qachon saqlanmaydi: ular to'g'ridan-to'g'ri o'tadi va faqat ikki Telegram chatida qoladi, har kim o'z nusxasini o'chira oladi. Yagona istisno — /feedback, quyida.\n\nAnonim — siz yozayotgan odam uchun anonim degani: u ismingiz, username yoki ID raqamingizni hech qachon ko'rmaydi. Botning o'zi esa kim kim bilan bog'langanini biladi — javoblar shunday ishlaydi.",
        "privacy_problems_heading": "Nimadir noto'g'ri ketganda:",
        "privacy_problems": "Har bir xato sizsiz yoziladi: kodi, hodisa raqami, vaqti va bot versiyasi.\n\nXato ostidagi «Ma'lumotlarimni qo'shish» o'sha hodisaga Telegram ID, @username, til va chat turini (shaxsiy yoki guruh) qo'shadi — faqat «Yuborish»ni bossangiz. Xabarlar va fayllar hech qachon qo'shilmaydi. Ular 30 kundan keyin yoki /deletemydata bilan darhol o'chiriladi. Xato yozuvlari 180 kundan keyin o'chiriladi.",
        "privacy_seen_by_heading": "Yana kim ko'ra oladi:",
        "privacy_seen_by": "• Telegram — barcha xabarlarni u yetkazadi\n• hosting provayderi va bot ishlatadigan ma'lumotlar bazasi",
        "privacy_others": "• boshqa hech kim — xabarlar faqat Telegram orqali o'tadi, tashqi xizmatlar ishlatilmaydi",
        "privacy_kept_for_heading": "Qancha vaqt saqlanadi:",
        "privacy_kept_for": "Sozlamalar siz o'chirmaguningizcha yoki botdan foydalanishni to'xtatmaguningizcha saqlanadi. Foydalanish vaqtlari taxminan uch oydan keyin o'chiriladi. To'lov yozuvlari va ⚡ balansingiz to'lov nizolari uchun uzoqroq saqlanadi.\n\nHech narsa sotilmaydi, reklamaga ishlatilmaydi va yuqorida aytilmaganlarga berilmaydi.",
        "privacy_your_choices": "Nima qila olasiz:\n/deletemydata — bot siz haqingizda saqlagan narsalarni o'chirish\n/terms — qoidalar\n\nBotni bloklash hech narsani o'chirmaydi, shuning uchun avval /deletemydata yuboring.",
        "terms_heading": "📜 Shartlar",
        "terms_use": "Botdan qonun va Telegram shartlari doirasida foydalaning. Kimnidir bezovta qilgan yoki bot chegaralarini buzgan akkaunt bloklanadi.",
        "terms_specific": "Xabarlar: anonimlik tahdid, bezovtalik yoki noqonuniy narsalarni oqlamaydi. Quti egalari yuboruvchini butunlay bloklay oladi va odamlarni nima yuborishga chaqirganliklari uchun o'zlari javob beradi.",
        "terms_money": "Pul: /donate ixtiyoriy va server xarajatlariga yordam beradi. Stars ConvertBot uchun ⚡ kredit ham beradi: har ⭐ uchun 2 ⚡, ilk 500 Stars uchun 6 va keyingi 500 uchun 4; 2 dan ortig'i bonus bo'lib, to'lovdan 90 kun o'tgach yo'qoladi. To'lovlar QAYTARILMAYDI: Stars qaytarilMAYDI, kreditni yechib olib BO'LMAYDI — kreditga aylanmagan to'lov bundan mustasno, u avtomatik qaytariladi. To'lovlarni Telegram amalga oshiradi; bot karta raqamini ko'rmaydi. Muammo: /paysupport",
        "terms_no_warranty": "Kafolat yo'q: buni bir kishi bepul yuritadi, u sekin ishlashi, xato qilishi yoki ogohlantirishsiz to'xtashi mumkin. Muhim narsalarning nusxasini o'zingizda saqlang.",
        "policy_full_text": "To'liq matn: {url}",
        "policy_contact": "Savol, shikoyat yoki ma'lumot so'rovi uchun: {contact}",
        "delete_data_confirm": "⚠️ Bot siz haqingizda saqlagan ma'lumotlar o'chiriladi. Buni ortga qaytarib bo'lmaydi.",
        "delete_data_consequences": "Quti havolangiz butunlay ishlamay qoladi, ochiq suhbatlarga endi javob yozib bo'lmaydi, bloklar ro'yxati ham o'chadi.\n\nYuborilgan xabarlar Telegram chatlarida qoladi; ular bu yerda hech qachon saqlanmagan.\n\nTo'lov yozuvlari to'lov nizolari uchun username'ingizsiz qoladi. ⚡ balansingiz ham qoladi.",
        "delete_data_button_yes": "🗑 O'chirilsin",
        "delete_data_button_no": "↩️ Ma'lumotlarim qolsin",
        "delete_data_kept": "Hech narsa o'chirilmadi.",
        "delete_data_done": "🗑 Tayyor — {rows} ta yozuv o'chirildi. Yangidan boshlash uchun istalgan vaqt /start yuboring.",
        "delete_data_failed": "O'chirib bo'lmadi — men tomonda xatolik yuz berdi. Bir necha daqiqadan keyin qayta urinib ko'ring.",
        "language_set_confirmation": "✅ Til o'zbekchaga o'zgartirildi.",
        "cancel_header": "\u274c Bekor qilindi:",
        "cancel_nothing": "Bekor qilinadigan narsa yo'q.",
        "cancel_ask": 'Qaysi birini bekor qilay? Hozir quyidagilar kutilmoqda:',
        "cancel_kept": "Yaxshi — hech narsa bekor qilinmadi.",
        "cancel_reply_box_freed": "Xabar yozish maydoni endi bo'sh.",
        "cancel_button_all": "❌ Hammasini",
        "cancel_button_none": '↩️ Hech birini, davom etamiz',
        "cancel_button_donation": "💸 Xayriya miqdori",
        "cancel_item_donation": 'kiritilishi kutilayotgan xayriya miqdori',
        "cancel_item_stale_prompt": "javobsiz qolgan eski savol",
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
        "privacy_feedback_heading": "/feedback yuborsangiz:",
        "privacy_feedback": (
            "U botlarni yuritayotgan odamga bot nomi va tilingiz bilan yetib boradi — ismingiz, username yoki ID raqamingiz bilan emas. U javob yoza olmaydi. Nusxasi 30 kun saqlanadi."
        ),
        "cancel_item_anon_session": "ochiq turgan anonim suhbat — yangisini boshlash uchun havolani qayta bosing",
        "cancel_button_anon_session": "💬 Siz turgan anonim suhbat",
        "start_greeting": "Salom! Bu bot anonim savol-javob qutilarini boshqaradi.\n\n",
        "help_brief": (
            "Bot qanday ishlaydi:\n\n• /link sizga doimiy havola beradi. Uni bosgan har kim sizga shu yerda anonim yoza oladi.\n• Javob berish uchun xabarga javob qiling (o'ngga suring).\n• Birovning havolasi keldimi? Uni bosing va yozing.\n• Reaksiyalar ikkinchi tomonga yetib boradi (faqat oddiy emoji).\n• Taklif yoki muammo: /feedback\n\nAsosiy buyruqlar: /link · /conversations · /cancel\nQolgani: to'liq qo'llanma, pastda yoki /fullhelp.\n\n"
        ),
        "help_full_button": "📖 To'liq qo'llanma",
        "help_brief_button": "↩️ Qisqa variant",
        "help_page_everything": "To'liq qo'llanma: /fullhelp",
        "help_page_gone": "Bu yordam sahifasi endi yo'q. /help yuboring.",
        "helppage_link": (
            "🔗 /link — anonim qutingiz havolasi\n\nUni istalgan joyga joylang: ochgan har kim sizga shu yerda anonim yoza oladi. Har ochilishda yangi suhbat boshlanadi. Yangisini yaratmaguningizcha havola o'zgarmaydi.\nOstidagi tugmalar:\n• ⏸️ To'xtatish: qayta yoqmaguningizcha yangi suhbat yo'q (/pause, /resume).\n• 🔄 Yangi havola: eskisi endi suhbat boshlamaydi (/newlink).\n• Mehmonlar va suhbatlar soni (/stats).\nBoshlangan suhbatlar har holda ishlayveradi.\n\nMisol: /link yuboring va havolani Instagram bio'ngizga qo'ying."
        ),
        "helppage_which": (
            "❓ /which — bu xabar qaysi suhbatdan?\n\nShu yerdagi istalgan xabarga /which bilan javob qiling.\n• Quti egasi: suhbat raqamini aytaman.\n• Mehmon: suhbat boshlangan joyni ko'rsataman.\n\nMisol: \"{example}\""
        ),
        "reactions_update_note": "🆕 Endi bu yerda xabarlarga reaksiya qoldirish mumkin — ikkinchi tomon uni ko'radi. Batafsil: /help",
        "gift_ad": "🎉 Mumu botlarida {users} dan ortiq foydalanuvchi bo'ldi, shuning uchun hammaga {amount} ⚡ bepul kredit berishga qaror qildik! Uni {download} va {convert} botlarida olib, ishlatishingiz mumkin.\n\n<i>Olish uchun ikki botdan biriga istalgan xabar yuboring: sovg'a avtomatik qo'shiladi. Uni {claim_by} gacha oling; olingandan keyin {days} kun amal qiladi.</i>",
        "reaction_not_passed": "⚠️ Suhbatdoshingiz bu reaksiyani ko'rmaydi — faqat oddiy emojilar yetkaziladi.",
        "reaction_one_only": "⚠️ Suhbatdoshingiz faqat oxirgi reaksiyangizni ko'radi — har bir xabarga bittasi yetkaziladi.",
        "help_text": "Botdan ikki xil foydalanish mumkin:\n\nO'z qutingiz — /link sizga doimiy havola beradi. Uni istalgan joyga joylang (bio, kanal, story). Uni bosgan har kim sizga shu yerda anonim yoza oladi.\nJavob berish uchun xabarga javob qiling: o'ngga suring yoki bosib turib «Javob berish»ni tanlang. Har bir javob shunday yozilishi shart, shunda u to'g'ri suhbatga boradi.\n\nBirovning havolasi — uni bosing va yozing. Birinchi xabaringiz darhol yetib boradi; keyin javob berayotgan xabaringizga javob qiling. Havolani yana bossangiz, yangi suhbat boshlanadi.\n\nReaksiyalar: xabarga reaksiya qoldirsangiz, ikkinchi tomon ham xuddi shu reaksiyani ko'radi, men nomimdan. Har bir xabarga bittadan, faqat quyidagilar:\n{reactions}\n⚠️ Telegram Premium (maxsus emoji) reaksiyalari o'tkazilMAYDI.\n\nBuyruqlar:\n/link - quti havolangiz\n/conversations - suhbatlaringiz, ochiq va arxivdagi\n/which - xabarga javob qilib yuboring: u qaysi suhbatdan?\n/archive - xabarga javob qilib yuboring: o'sha suhbat arxivlanadi\n/newlink, /pause, /resume - /link ostidagi tugmalar ham bor\n/blocked - bloklaganlaringiz\n/stats - hisoblaringiz\n/cancel - men kutayotgan narsani to'xtatish\n/feedback - botlarni yuritayotgan odamga yozish (anonim)\n/donate - server xarajatlariga yordam (ixtiyoriy)\n/balance - ⚡ kreditingiz\n/paysupport - to'lov bilan muammo\n/en, /uz, /rus - tilni almashtirish (yoki /language)\n/privacy - siz haqingizda nimalar saqlanadi\n/terms - botdan nima uchun foydalanish mumkin\n/deletemydata - siz haqingizdagi hamma narsani o'chirish\n/start - boshidan boshlash\n/fullhelp - shu qo'llanma (/help - qisqasi)\n\n⚠️ To'lovlar qaytarilmaydi — Stars qaytarilMAYDI, ⚡ kreditni yechib olib BO'LMAYDI.\n\n",
        "follow_link_invalid": "Bu havola yaroqsiz — uni ulashgan kishi qayta tiklagan bo'lishi mumkin. Ulardan yangisini so'rang.",
        "follow_link_own": "Bu o'zingizning havolangiz — uni boshqalarga ulashing: /link",
        "follow_link_blocked": 'Bu odamga xabar yubora olmaysiz.',
        "follow_link_paused": "Bu odam hozircha yangi anonim xabarlar qabul qilmayapti — keyinroq urinib ko'ring.",
        "follow_link_started": "Siz kimgadir anonim yozyapsiz — kimligingizni ko'rishmaydi.\n\nBirinchi xabaringizni pastga yozing. Keyin javob berayotgan xabaringizga javob qiling (o'ngga suring).",
        "follow_cancel_button": "✖️ Bekor qilish",
        "follow_link_cancelled": "Bekor qilindi.",
        "link_status_paused": "to'xtatilgan — yangi suhbatlar qabul qilinmaydi",
        "link_status_active": 'xabarlar qabul qilinmoqda',
        "link_message": "Anonim qutingiz havolasi ({status}):\n{url}\n\nUni istalgan joyga joylang — ochgan har kim sizga shu yerda anonim yoza oladi.",
        "link_button_pause": "⏸️ To'xtatish",
        "link_button_resume": '▶️ Qayta yoqish',
        "link_button_newlink": "🔄 Yangi havola",
        "anonlink_no_link_yet": "Hali havola yo'q — avval /link yuboring.",
        "anonlink_paused_answer": "To'xtatildi.",
        "anonlink_resumed_answer": 'Qayta yoqildi.',
        "anonlink_newlink_answer": "Yangi havola yaratildi — eskisi endi suhbat boshlamaydi.",
        "newlink_message": "Yangi havola (eskisi endi yangi suhbat boshlamaydi):\n{url}",
        "pause_message": "To'xtatildi — /resume yubormaguningizcha yangi suhbat boshlanmaydi. Boshlanganlari ishlayveradi.",
        "resume_message": "Qayta yoqildi — havolangiz yana yangi suhbatlarni qabul qiladi.",
        "stats_message": 'Sizga {followers} nafar mehmon yozgan, jami {conversations} ta suhbat.',
        # ---- /conversations ----
        "convs_title": '📥 Qutingiz',
        "convs_title_archived": "\U0001f5c4 Arxivlangan suhbatlar",
        "convs_summary": "{open} ta ochiq \u00b7 {archived} ta arxivda",
        "convs_summary_blocked": "{open} ta ochiq \u00b7 {archived} ta arxivda \u00b7 {blocked} ta bloklangan (/blocked)",
        "convs_empty_open": "Hozircha ochiq suhbat yo'q. Xabar olish uchun /link havolangizni ulashing.",
        "convs_empty_archived": (
            "Arxivda hali hech narsa yo'q."
        ),
        "convs_row_button": "#{conv_number} \u00b7 {when}",
        "convs_show_archived": "\U0001f5c4 Arxiv",
        "convs_show_open": "\U0001f4e5 Ochiq",
        "convs_page": "{page}/{pages}",
        "convs_prev": "\u2039",
        "convs_next": "\u203a",
        "conv_card": "#{conv_number}-suhbat\n\nOchilgan: {started}\nOxirgi xabar: {when}",
        "conv_card_archived": "Arxivlangan: {archived}",
        "conv_back_button": "\u2039 Orqaga",
        "conv_jump_button": "\u2934\ufe0f Boshiga o'tish",
        "conv_archive_button": "\U0001f5c4 Arxivga",
        "conv_unarchive_button": "\u21a9\ufe0f Arxivdan chiqarish",
        "conv_archived_answer": "Arxivlandi — yana yozishsa, qaytib keladi.",
        "conv_unarchived_answer": "Ochiqlar ro'yxatiga qaytdi.",
        "conv_gone": "Bu suhbat haqida endi ma'lumot yo'q.",
        "jump_bookmark": (
            "📍 #{conv_number}-suhbat shu yerdan boshlanadi. Unda yozish uchun shu xabarga javob qiling."
        ),
        "jump_no_anchor": "#{conv_number}-suhbat boshini endi topolmayman — u {days} kundan ortiq jim turgan.",
        # ---- /which ----
        "which_needs_reply": (
            "Xabarga /which bilan javob qiling — u qaysi suhbatdan ekanini aytaman."
        ),
        "which_unknown": (
            "Bu xabar ochiq suhbatlarning hech birida yo'q (o'zingizniki yoki arxivdagi bo'lishi mumkin)."
        ),
        "which_owner": "Bu xabar #{conv_number}-suhbatga tegishli.",
        "which_follower": "Bu xabar shu yerdan boshlanadigan suhbatga tegishli.",
        "which_follower_no_anchor": "Bu xabar siz {started} ochgan suhbatga tegishli.",
        # ---- qancha vaqt oldin ----
        "rel_now": "hozir",
        "rel_minutes": "{n} daq. oldin",
        "rel_hours": "{n} soat oldin",
        "rel_days": "{n} kun oldin",
        "blocked_none": "Siz hech kimni bloklamagansiz.",
        "blocked_header": "Bloklangan mehmonlar:",
        "blocked_guest_line": "  \U0001f6ab {conversations}-suhbatdagi mehmon",
        "blocked_guest_line_unknown": "  \U0001f6ab Mehmon (suhbat qayd etilmagan)",
        "blocked_unblock_button": "↩️ Blokdan chiqarish ({conversations}-suhbat)",
        "blocked_unblock_button_unknown": "↩️ Blokdan chiqarish",
        "reply_button": '↩️ Qanday javob beraman?',
        "block_button": "\U0001f6ab Bloklash",
        "not_your_conversation": "Bu sizning suhbatingiz emas.",
        "blocked_answer": "Bloklandi.",
        "unblock_button_labelled": "↩️ #{conv_number}-suhbatni blokdan chiqarish",
        "too_fast": "Juda tez — {seconds} soniya kutib, qayta urinib ko'ring.",
        "edit_not_relayed": "Tahrirlar yetib bormaydi — ular asl xabaringizni ko'rishadi. Tuzatishni yangi javob qilib yuboring.",
        "unblocked_answer": "Blokdan chiqarildi.",
        "delivery_blocked": "Xabaringiz yetkazilmadi.",
        "inbox_gone": "Bu quti endi mavjud emas.",
        "incoming_header": "\U0001f4e9 #{conv_number}-suhbat",
        "incoming_header_follower": "\U0001f4ec Yangi xabar",
        "delivery_forbidden": "Yuborilmadi — bu odam hali botni ishga tushirmagan.",
        "delivery_failed": "Bu xabarni yetkazib bo'lmadi: {error}",
        "sent_confirmation": "Yuborildi ✅",
        "reply_no_match": "Xabaringiz yuborilmadi — bu javobni biror suhbatga moslab bo'lmadi.",
        "must_reply": "Yuborilmadi. Yuqoridagi xabarga javob qiling (o'ngga suring), shunda u o'sha yerga boradi.",
        "reply_button_hint": "Bu xabarni o'ngga suring yoki bosib turib «Javob berish»ni tanlang.",
        "must_reply_opening": "Hali yuborilmadi. Yuqoridagi xabarga javob qiling (o'ngga suring) — bu birinchi xabaringiz bo'ladi.",
        "must_reply_ambiguous": "Yuborilmadi — bu yerda bir nechta suhbat ochiq. Javob berayotgan xabaringizga javob qiling (o'ngga suring).",
        "restarting_send_again": "🔄 Bot yangilanmoqda — bir necha soniyadan keyin qayta yuboring.",
        "update_soon_try_later": "🔧 Bot hozir yangilanadi — taxminan {minutes} daqiqadan keyin qayta urinib ko'ring. Ishga tushgach, xabar beraman.",
        "update_soon_try_later_soon": "🔧 Bot yangilanmoqda — birozdan keyin qayta urinib ko'ring. Ishga tushgach, xabar beraman.",
        "update_will_reset": "🔧 Bot hozir yangilanadi va hozirgi ishingiz to'xtaydi. Bir necha daqiqadan keyin qaytadan boshlashingiz mumkin.",
        "update_done_try_now": "✅ Yangilanish tugadi — endi qayta urinib ko'ring.",
        "gift_granted": "🎁 100 foydalanuvchidan oshdik — rahmat! Sizga {amount} ⚡ bepul kredit, {date} gacha amal qiladi: {convert} da taxminan {conversions} ta pullik konvertatsiya yoki {download} da katta fayllar. /balance",
        "reply_forbidden": "Javobingizni yetkazib bo'lmadi — ular sizni bloklagan yoki botni tark etgan bo'lishi mumkin.",
        "reply_failed": "Javobingizni yetkazib bo'lmadi: {error}",
        "delivered_confirmation": "Yetkazildi ↩️",
        "reply_stale": "Yuborilmadi — bu xabar ochiq suhbatda emas. Men shu yerga yetkazgan xabarlardan biriga javob qiling.",
        "generic_nudge": "Kimdandir havola oldingizmi? Avval uni bosing. O'z qutingiz kerakmi? /link",
        "export_removed": "📦 /export olib tashlandi: bot endi suhbatlaringiz haqida kamroq ma'lumot saqlaydi. Xabarlaringiz Telegram chatingizda qoladi.",
        "archive_needs_reply": (
            "Suhbatdagi xabarga /archive bilan javob qiling yoki /conversations dan tanlang."
        ),
        "archive_not_owner": "Suhbatlarni faqat quti egasi arxivlay oladi.",
        "archive_already": "#{conv_number}-suhbat allaqachon arxivlangan.",
        "archive_confirm": (
            "#{conv_number}-suhbat arxivlansinmi?\n\nHech narsa o'chirilmaydi va ularga aytilmaydi. Yana yozishsa, qaytib keladi."
        ),
        "archive_button_yes": "🗂 #{conv_number} ni arxivlash",
        "archive_button_no": "Ochiq qolsin",
        "archive_done": "🗂 #{conv_number}-suhbat arxivlandi. Ko'rish uchun /conversations.",
        "archive_kept": "Ochiq qoldirildi.",
        "unknown_command": "Bunday buyruq yo'q. Bot nimalar qila olishini bilish uchun /help yuboring.",
    },
    "ru": {
        "maintenance_note": "🔧 AnonBot ненадолго закрыт на ремонт. Беседы в сохранности, но отправленное сейчас не доставляется и не сохраняется. Напишите позже.",
        "flood_wait": "Слишком быстро — подождите около {seconds} с и продолжайте.",
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
        "donate_invoice_description": "Помогает оплачивать хостинг и добавляет {credited} ⚡ кредита для ConvertBot.",
        "donate_invoice_label": "Вклад в хостинг",
        "donate_invoice_description_fiat": 'Разовое добровольное пожертвование на хостинг. Спасибо!',
        "donate_prompt_credit": "Каждая ⭐ даёт ⚡ кредит для ConvertBot. Следующие {left} ⭐ дают по {each} ⚡ ({mult}×): {rate} ⚡ плюс бонус, который сгорает через {days} дней после оплаты. ⚡ НЕЛЬЗЯ вывести или обменять обратно на Stars.",
        "donate_prompt_credit_base": "Каждая ⭐ даёт {rate} ⚡ кредита для ConvertBot. ⚡ НЕЛЬЗЯ вывести или обменять обратно на Stars.",
        "donate_invoice_error": "⚠️ Telegram не смог создать этот счёт: {error}",
        "stars_unit": "Stars (звёзды)",
        "donate_custom_ask": "Сколько {unit} вы хотите пожертвовать? Ответьте числом.",
        "donate_invalid_amount_retry": "Это некорректная сумма — отправьте /donate, чтобы попробовать снова.",
        "donate_thanks": "🙏 Спасибо за {amount} ⭐ — это по-настоящему ценно!",
        "topup_thanks": "🙏 Спасибо за {stars} ⭐!\n\nВы получили {total} ⚡ кредита в {convert_bot} на конвертации. Баланс там: {balance} ⚡.",
        "topup_thanks_bonus": 'Из них {bonus} ⚡ — бонусный кредит, он сгорит {date}.',
        "credit_cannot_be_withdrawn": "⚡ — кредит на конвертации в ConvertBot, его НЕЛЬЗЯ вывести или обменять обратно на Stars. Проблема с оплатой? /paysupport",
        "topup_refunded": "⚠️ Платёж на {stars} ⭐ прошёл, но ⚡ не дошёл до баланса, поэтому {stars} ⭐ вернули. Попробуйте снова через несколько минут.",
        "topup_failed": "⚠️ Платёж на {stars} ⭐ прошёл, но ⚡ не дошёл до баланса. Ничего не потеряно — владелец добавит его вручную. Уточнить: /paysupport",
        "paysupport_text": "💳 Помощь с оплатой\n\nПлатежи окончательны: Stars НЕ возвращаются, ⚡ НЕЛЬЗЯ вывести или обменять обратно на Stars. Возвращается только платёж, который не удалось превратить в кредит, — автоматически и полностью.\n\nДеньги списаны, а кредита нет, или списали дважды? Напишите на {contact}: дата, сумма и ваш Telegram ID, {user_id}. Всё исправят кредитом.\n\n/balance показывает ваши платежи.",
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
            "Постоянная ссылка, по которой вам пишут анонимно."
        ),
        "bot_description": (
            "Получите ссылку, разместите её где угодно — и вам смогут писать анонимно. Вы отвечаете в отдельных беседах, сразу в нескольких.\n\nБот знает, кто с кем говорит, чтобы ответы попадали в нужную беседу, но никогда не хранит написанное. /privacy\n\nНа русском, английском и узбекском."
        ),
        # ---- shared policy keys (/privacy, /terms, /deletemydata) ----
        "privacy_heading": "🔒 Конфиденциальность",
        "privacy_kept_heading": "Что бот хранит:",
        "privacy_stored": (
            "• ваш Telegram ID и язык\n• ссылка на ваш ящик, если вы её создали\n• кто с кем общается: каждая беседа — пара ID и номер, чтобы ответы попадали в нужную беседу\n• какое сообщение к какой беседе относится (по ID сообщения) и когда оно передано\n• кого вы заблокировали\n• что вам показали разовое объявление — чтобы оно показывалось один раз\n• время каждого использования бота — чтобы считать пользователей\n• дневное число нажатий кнопок по всему боту, не по людям\n• пожертвования: сумма и ID платежа в Telegram\n\nСообщения и реакции никогда не хранятся: они проходят насквозь и остаются только в двух чатах Telegram, где каждый может удалить свою копию. Единственное исключение — /feedback, ниже.\n\nАнонимно — значит анонимно для того, кому вы пишете: он никогда не видит ваше имя, username или ID. Сам бот знает, кто с кем связан, — так работают ответы."
        ),
        "privacy_problems_heading": "Когда что-то ломается:",
        "privacy_problems": "Каждая ошибка записывается без вас: код, номер инцидента, время и версия бота.\n\n«Добавить мои данные» под ошибкой прикрепляет к этому инциденту ваш Telegram ID, @username, язык и тип чата (личный или группа) — только если нажать «Отправить». Сообщения и файлы — никогда. Это стирается через 30 дней или сразу через /deletemydata. Записи об ошибках удаляются через 180 дней.",
        "privacy_seen_by_heading": "Кто ещё это видит:",
        "privacy_seen_by": (
            "• Telegram, через который идут все сообщения\n• хостинг-провайдер и база данных бота"
        ),
        "privacy_others": (
            "• больше никто — сообщения идут только через Telegram, внешние сервисы не используются"
        ),
        "privacy_kept_for_heading": "Сколько это хранится:",
        "privacy_kept_for": "Настройки хранятся, пока вы их не удалите или не перестанете пользоваться ботом. Время использования удаляется примерно через три месяца. Платёжные записи и ваш ⚡ баланс хранятся дольше — на случай споров об оплате.\n\nНичего не продаётся, не используется для рекламы и не передаётся никому, кроме названных выше.",
        "privacy_your_choices": (
            "Что вы можете сделать:\n/deletemydata — удалить всё, что бот о вас хранит\n/terms — правила\n\nБлокировка бота ничего не удаляет, поэтому сначала отправьте /deletemydata."
        ),
        "terms_heading": "📜 Условия",
        "terms_use": (
            "Пользуйтесь ботом в рамках закона и правил Telegram. За травлю или обход ограничений бота аккаунт блокируется."
        ),
        "terms_specific": (
            "Сообщения: анонимность не делает угрозы, травлю или что-то незаконное допустимыми. Владелец ящика может навсегда заблокировать отправителя и сам отвечает за то, что предлагает себе присылать."
        ),
        "terms_money": "Деньги: /donate добровольный и помогает оплачивать хостинг. Stars также дают ⚡ кредит для ConvertBot: 2 ⚡ за ⭐, или 6 за первые 500 Stars и 4 за следующие 500; всё сверх 2 — бонус, который сгорает через 90 дней после оплаты. Платежи окончательны: Stars НЕ возвращаются, кредит НЕЛЬЗЯ вывести — кроме платежа, который не удалось превратить в кредит, он возвращается автоматически. Платежи проводит Telegram; бот не видит номер карты. Проблемы: /paysupport",
        "terms_no_warranty": (
            "Без гарантий: бота бесплатно ведёт один человек, он может тормозить, ошибаться или отключиться без предупреждения. Храните копии важного у себя."
        ),
        "policy_full_text": "Полный текст: {url}",
        "policy_contact": "Вопросы, жалобы или запрос по данным: {contact}",
        "delete_data_confirm": "⚠️ Это сотрёт всё, что бот хранит о вас. Отменить будет нельзя.",
        "delete_data_consequences": "Ссылка на ваш ящик перестанет работать навсегда, на открытые беседы больше нельзя будет ответить, список блокировок удалится.\n\nОтправленные сообщения останутся в чатах Telegram; здесь они никогда не хранились.\n\nПлатёжные записи останутся без вашего username — на случай споров об оплате. ⚡ баланс тоже останется.",
        "delete_data_button_yes": "🗑 Стереть",
        "delete_data_button_no": "↩️ Оставить мои данные",
        "delete_data_kept": "Ничего не стёрто.",
        "delete_data_done": (
            "🗑 Готово — удалено записей: {rows}. Отправьте /start, когда захотите начать заново."
        ),
        "delete_data_failed": (
            "Не удалось удалить — что-то пошло не так с моей стороны. Попробуйте через несколько минут."
        ),
        "language_set_confirmation": "✅ Язык изменён на русский.",
        "cancel_header": "\u274c \u041e\u0442\u043c\u0435\u043d\u0435\u043d\u043e:",
        "cancel_nothing": "Отменять нечего.",
        "cancel_ask": "Что остановить? Вот что я жду:",
        "cancel_kept": "Хорошо — ничего не отменено.",
        "cancel_reply_box_freed": "Поле ответа снова свободно.",
        "cancel_button_all": "❌ Всё",
        "cancel_button_none": "↩️ Ничего, продолжаем",
        "cancel_button_donation": "💸 Сумма пожертвования",
        "cancel_item_donation": "\u0441\u0443\u043c\u043c\u0430 \u043f\u043e\u0436\u0435\u0440\u0442\u0432\u043e\u0432\u0430\u043d\u0438\u044f, \u043a\u043e\u0442\u043e\u0440\u0443\u044e \u044f \u0437\u0430\u043f\u0440\u043e\u0441\u0438\u043b",
        "cancel_item_stale_prompt": "старый вопрос без ответа",
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
        "privacy_feedback_heading": "Если вы отправляете /feedback:",
        "privacy_feedback": (
            "Оно приходит тому, кто ведёт ботов, с названием бота и вашим языком — без имени, username и ID. Ответить он не может. Копия хранится 30 дней."
        ),
        "cancel_item_anon_session": "\u043e\u0442\u043a\u0440\u044b\u0442\u044b\u0439 \u0430\u043d\u043e\u043d\u0438\u043c\u043d\u044b\u0439 \u0447\u0430\u0442 — \u043d\u0430\u0436\u043c\u0438\u0442\u0435 \u0441\u0441\u044b\u043b\u043a\u0443 \u0441\u043d\u043e\u0432\u0430, \u0447\u0442\u043e\u0431\u044b \u043d\u0430\u0447\u0430\u0442\u044c \u043d\u043e\u0432\u044b\u0439",
        "cancel_button_anon_session": "💬 Анонимный чат, в котором вы",
        "start_greeting": "Здравствуйте! Этот бот управляет анонимными почтовыми ящиками для вопросов.\n\n",
        "help_brief": (
            "Как это устроено:\n\n• /link даёт постоянную ссылку. Любой, кто по ней перейдёт, сможет анонимно написать вам сюда.\n• Чтобы ответить, ответьте на сообщение (смахните его).\n• Прислали чужую ссылку? Перейдите по ней и напишите.\n• Реакции доходят до собеседника (только обычные эмодзи).\n• Идеи или проблемы: /feedback\n\nОсновные команды: /link · /conversations · /cancel\nВсё остальное: полное руководство, ниже или /fullhelp.\n\n"
        ),
        "help_full_button": "📖 Полное руководство",
        "help_brief_button": "↩️ Кратко",
        "help_page_everything": "Полное руководство: /fullhelp",
        "help_page_gone": "Этой страницы справки больше нет. Отправьте /help.",
        "helppage_link": (
            "🔗 /link — ссылка на ваш анонимный ящик\n\nРазместите её где угодно: любой, кто её откроет, сможет анонимно написать вам сюда. Каждый переход начинает новую беседу. Ссылка не меняется, пока вы не создадите новую.\nКнопки под ней:\n• ⏸️ Пауза: новых бесед нет, пока не возобновите (/pause, /resume).\n• 🔄 Новая ссылка: старая перестаёт начинать беседы (/newlink).\n• Число гостей и бесед (/stats).\nТекущие беседы в любом случае продолжают работать.\n\nПример: отправьте /link и поставьте ссылку в био Instagram."
        ),
        "helppage_which": (
            "❓ /which — из какой беседы это сообщение?\n\nОтветьте на любое сообщение здесь командой /which.\n• Владелец ящика: назову номер беседы.\n• Гость: покажу, где она начинается.\n\nПример: \"{example}\""
        ),
        "reactions_update_note": "🆕 Теперь здесь можно ставить реакции на сообщения — собеседник их увидит. Подробнее: /help",
        "gift_ad": "🎉 У Mumu ботов уже больше {users} уникальных пользователей, поэтому мы решили раздать {amount} ⚡ бесплатного кредита! Получить и потратить его можно в {download} и {convert}.\n\n<i>Чтобы получить подарок, отправьте любое сообщение любому из этих двух ботов: он начислится автоматически. Получите его до {claim_by}; после получения он действует {days} дней.</i>",
        "reaction_not_passed": "⚠️ Собеседник не увидит эту реакцию — передаются только обычные эмодзи.",
        "reaction_one_only": "⚠️ Собеседник видит только вашу последнюю реакцию — на сообщение передаётся одна.",
        "help_text": "Ботом можно пользоваться двумя способами:\n\nСвой ящик — /link даёт постоянную ссылку. Разместите её где угодно (био, канал, сторис). Любой, кто по ней перейдёт, сможет анонимно написать вам сюда.\nЧтобы ответить, ответьте на сообщение: смахните его или нажмите и удерживайте, затем «Ответить». Каждый ответ должен быть ответом на сообщение — так он попадёт в нужную беседу.\n\nЧужая ссылка — перейдите по ней и напишите. Первое сообщение уйдёт сразу; дальше отвечайте на сообщение, которому отвечаете. Повторный переход по ссылке начинает новую беседу.\n\nРеакции: поставьте реакцию — собеседник увидит такую же, от моего имени. Одна на сообщение, только эти:\n{reactions}\n⚠️ Реакции Telegram Premium (свои эмодзи) НЕ передаются.\n\nКоманды:\n/link - ссылка на ваш ящик\n/conversations - ваши беседы, открытые и в архиве\n/which - ответьте ею на сообщение: из какой оно беседы?\n/archive - ответьте ею на сообщение, чтобы убрать беседу в архив\n/newlink, /pause, /resume - также кнопки под /link\n/blocked - кого вы заблокировали\n/stats - ваша статистика\n/cancel - остановить то, чего я жду\n/feedback - написать тому, кто ведёт ботов (анонимно)\n/donate - помочь с хостингом (необязательно)\n/balance - ваш ⚡ кредит\n/paysupport - проблема с оплатой\n/en, /uz, /rus - сменить язык (или /language)\n/privacy - что я храню о вас\n/terms - для чего можно использовать бота\n/deletemydata - удалить всё, что я о вас храню\n/start - начать сначала\n/fullhelp - это руководство (/help - краткое)\n\n⚠️ Платежи окончательны — Stars НЕ возвращаются, ⚡ кредит НЕЛЬЗЯ вывести.\n\n",
        "follow_link_invalid": "Эта ссылка недействительна — возможно, тот, кто ею поделился, сбросил её. Попросите у него новую.",
        "follow_link_own": "Это ваша собственная ссылка — поделитесь ею с другими: /link",
        "follow_link_blocked": "Вы не можете написать этому человеку.",
        "follow_link_paused": "Этот человек сейчас не принимает новые анонимные сообщения — попробуйте позже.",
        "follow_link_started": (
            "Вы пишете кому-то анонимно — он не увидит, кто вы.\n\nНапишите первое сообщение ниже. Дальше отвечайте на сообщение, которому отвечаете (смахните его)."
        ),
        "follow_cancel_button": "✖️ Отмена",
        "follow_link_cancelled": "Отменено.",
        "link_status_paused": "на паузе — новые беседы не принимаются",
        "link_status_active": "принимает сообщения",
        "link_message": (
            "Ссылка на ваш анонимный ящик ({status}):\n{url}\n\nРазместите её где угодно — любой, кто её откроет, сможет анонимно написать вам сюда."
        ),
        "link_button_pause": "⏸️ Пауза",
        "link_button_resume": "▶️ Возобновить",
        "link_button_newlink": "🔄 Новая ссылка",
        "anonlink_no_link_yet": "Ссылки пока нет — сначала отправьте /link.",
        "anonlink_paused_answer": "На паузе.",
        "anonlink_resumed_answer": "Возобновлено.",
        "anonlink_newlink_answer": "Новая ссылка создана — старая больше не начинает беседы.",
        "newlink_message": "Новая ссылка (старая больше не начинает новые беседы):\n{url}",
        "pause_message": (
            "На паузе — новых бесед не будет до /resume. Текущие продолжают работать."
        ),
        "resume_message": "Возобновлено — ваша ссылка снова принимает новые беседы.",
        "stats_message": "Вам писали {followers} разных гостей, всего {conversations} беседы(бесед).",
        # ---- /conversations ----
        "convs_title": "\U0001f4e5 Ваши беседы",
        "convs_title_archived": "\U0001f5c4 Беседы в архиве",
        "convs_summary": "{open} открытых \u00b7 {archived} в архиве",
        "convs_summary_blocked": "{open} открытых \u00b7 {archived} в архиве \u00b7 {blocked} заблокировано (см. /blocked)",
        "convs_empty_open": (
            "Открытых бесед нет. Поделитесь своей /link, чтобы получать сообщения."
        ),
        "convs_empty_archived": (
            "В архиве пока пусто."
        ),
        "convs_row_button": "#{conv_number} \u00b7 {when}",
        "convs_show_archived": "\U0001f5c4 Архив",
        "convs_show_open": "\U0001f4e5 Открытые",
        "convs_page": "{page}/{pages}",
        "convs_prev": "\u2039",
        "convs_next": "\u203a",
        "conv_card": "Беседа #{conv_number}\n\nНачата: {started}\nПоследнее сообщение: {when}",
        "conv_card_archived": "В архиве с: {archived}",
        "conv_back_button": "\u2039 Назад",
        "conv_jump_button": "\u2934\ufe0f К началу",
        "conv_archive_button": "\U0001f5c4 В архив",
        "conv_unarchive_button": "\u21a9\ufe0f Из архива",
        "conv_archived_answer": "В архиве — вернётся, если вам снова напишут.",
        "conv_unarchived_answer": "Снова в списке открытых.",
        "conv_gone": "Такой беседы больше нет в записях.",
        "jump_bookmark": (
            "📍 Здесь начинается беседа #{conv_number}. Ответьте на это сообщение, чтобы написать в неё."
        ),
        "jump_no_anchor": (
            "Я больше не могу найти начало беседы #{conv_number} — в ней было тихо больше {days} дней."
        ),
        # ---- /which ----
        "which_needs_reply": (
            "Ответьте на сообщение командой /which — скажу, из какой оно беседы."
        ),
        "which_unknown": (
            "Этого сообщения нет в открытых беседах (возможно, оно ваше или в архиве)."
        ),
        "which_owner": "Это сообщение относится к беседе #{conv_number}.",
        "which_follower": "Это сообщение относится к беседе, которая начинается здесь.",
        "which_follower_no_anchor": "Это сообщение относится к беседе, которую вы открыли {started}.",
        # ---- как давно ----
        "rel_now": "только что",
        "rel_minutes": "{n} мин. назад",
        "rel_hours": "{n} ч. назад",
        "rel_days": "{n} дн. назад",
        "blocked_none": "Вы никого не заблокировали.",
        "blocked_header": "Заблокированные гости:",
        "blocked_guest_line": "  \U0001f6ab Гость из бесед(ы) {conversations}",
        "blocked_guest_line_unknown": "  \U0001f6ab Гость (бесед не найдено)",
        "blocked_unblock_button": "↩️ Разблокировать (беседа {conversations})",
        "blocked_unblock_button_unknown": "↩️ Разблокировать",
        "reply_button": "↩️ Как ответить",
        "block_button": "\U0001f6ab Заблокировать",
        "not_your_conversation": "Это не ваша беседа.",
        "blocked_answer": "Заблокировано.",
        "unblock_button_labelled": "↩️ Разблокировать беседу #{conv_number}",
        "too_fast": "Слишком быстро — подождите {seconds} с и попробуйте снова.",
        "edit_not_relayed": (
            "Правки не передаются — собеседник видит исходное сообщение. Отправьте исправление новым ответом."
        ),
        "unblocked_answer": "Разблокирован.",
        "delivery_blocked": "Ваше сообщение не удалось доставить.",
        "inbox_gone": "Этого ящика больше не существует.",
        "incoming_header": "\U0001f4e9 Беседа #{conv_number}",
        "incoming_header_follower": "\U0001f4ec Новое сообщение",
        "delivery_forbidden": (
            "Не отправлено — этот человек ещё не запускал бота."
        ),
        "delivery_failed": "Не удалось доставить это сообщение: {error}",
        "sent_confirmation": "Отправлено ✅",
        "reply_no_match": "Ваше сообщение не отправлено — не удалось сопоставить этот ответ с какой-либо беседой.",
        "must_reply": (
            "Не отправлено. Ответьте на сообщение выше (смахните его), чтобы отправить туда."
        ),
        "reply_button_hint": (
            "Смахните это сообщение вправо или нажмите и удерживайте его, затем «Ответить»."
        ),
        "must_reply_opening": (
            "Ещё не отправлено. Ответьте на сообщение выше (смахните его) — это станет вашим первым сообщением."
        ),
        "must_reply_ambiguous": (
            "Не отправлено — здесь открыто несколько бесед. Ответьте на сообщение, которому отвечаете (смахните его)."
        ),
        "restarting_send_again": (
            "🔄 Меня обновляют — отправьте это снова через несколько секунд."
        ),
        "update_soon_try_later": "🔧 Меня сейчас обновят — попробуйте снова примерно через {minutes} мин. Я напишу, когда вернусь.",
        "update_soon_try_later_soon": "🔧 Меня обновляют — попробуйте чуть позже. Я напишу, когда вернусь.",
        "update_will_reset": "🔧 Меня сейчас обновят, и то, что вы делаете, сбросится. Начать заново можно через несколько минут.",
        "update_done_try_now": "✅ Обновление готово — попробуйте снова.",
        "gift_granted": "🎁 Нас уже больше 100 — спасибо! Вам {amount} ⚡ бесплатного кредита до {date}: это около {conversions} платных конвертаций в {convert} или больших загрузок в {download}. /balance",
        "reply_forbidden": "Не удалось доставить ваш ответ — возможно, вас заблокировали или человек покинул бота.",
        "reply_failed": "Не удалось доставить ваш ответ: {error}",
        "delivered_confirmation": "Доставлено ↩️",
        "reply_stale": (
            "Не отправлено — этого сообщения нет в открытых беседах. Ответьте на одно из сообщений, которые я сюда доставил."
        ),
        "generic_nudge": "Вам прислали ссылку? Сначала перейдите по ней. Нужен свой ящик? /link",
        "export_removed": "📦 Команда /export удалена: теперь бот хранит меньше данных о ваших переписках. Ваши сообщения остаются в чате Telegram.",
        "archive_needs_reply": (
            "Ответьте на сообщение из беседы командой /archive или выберите её в /conversations."
        ),
        "archive_not_owner": (
            "Архивировать беседы может только владелец ящика."
        ),
        "archive_already": "Беседа #{conv_number} уже в архиве.",
        "archive_confirm": (
            "Убрать беседу #{conv_number} в архив?\n\nНичего не удаляется, и собеседнику не сообщается. Если он снова напишет, беседа вернётся."
        ),
        "archive_button_yes": "🗂 В архив #{conv_number}",
        "archive_button_no": "Оставить открытой",
        "archive_done": "🗂 Беседа #{conv_number} в архиве. Посмотреть — /conversations.",
        "archive_kept": "Оставил открытой.",
        "unknown_command": "Я не знаю такую команду. Отправьте /help, чтобы увидеть, что я умею.",
    },
}


# Every problem a person can run into ends with its code, and the code is what
# puts a "Report the issue" button under it -- see problems.py. Imported here,
# below the tables, because it is pure data and nothing above needs it.
import problems  # noqa: E402

_BOT = "anon_bot"


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
        "link": "Havolangiz va uning sozlamalari",
        "conversations": "Suhbatlaringiz",
        "which": "Bu qaysi suhbat? — xabarga javob qilib yuboring",
        "blocked": "Kimlarni bloklagansiz",
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
        "link": "Ваша ссылка и её настройки",
        "conversations": "Ваши разговоры",
        "which": "Какой это разговор? — ответьте на сообщение",
        "blocked": "Кого вы заблокировали",
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
