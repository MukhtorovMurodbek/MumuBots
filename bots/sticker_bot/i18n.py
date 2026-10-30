"""Translation strings for StickerBot's end-user-facing text (English,
Uzbek, Russian). Deliberately duplicated per bot -- same "no shared files
between bots" independence as shared_features.py -- but the STRINGS content
here is specific to this bot's own commands and flows.

Admin-only output (/whois, /messageas, /dbdump, /status, /crashtest) is
intentionally NOT translated -- only the bot owner ever sees it, same
reasoning as downloader_bot's i18n.py.

The keys below split into two groups:
  - "Shared" keys (donate flow, sibling-bot blurb) exist under the exact
    same names in every bot's i18n.py, since shared_features.py is
    duplicated byte-identical across the family and calls t() with these
    names regardless of which bot it's running in.
  - Bot-specific keys, everything below the shared block, for this bot's
    own bot.py (plus video_sticker.py/import_utils.py error text) strings
    only.
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
        "whomade_someone": "someone who keeps their name private",
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
            "Turns images, GIFs and videos into Telegram sticker packs."
        ),
        "bot_description": (
            "Send a picture, a GIF or a video and this bot builds it into a sticker pack that "
            "belongs to you.\n"
            "\n"
            "Share a pack for someone else to add to, or bulk-import one from another Telegram "
            "pack or a WhatsApp export.\n"
            "\n"
            "English, Uzbek and Russian. /privacy says what it keeps about you."
        ),
        # ---- shared policy keys (/privacy, /terms, /deletemydata) ----
        "privacy_heading": "🔒 Privacy",
        "privacy_kept_heading": "What this bot keeps:",
        "privacy_stored": "• your Telegram ID and language\n• the packs you made: name, title, and your display name and username at the time\n• who can add to your packs, your share links with their limits, and invited @usernames (sealed; until they use the bot, 30 days at most)\n• which of your packs are public, and that you used the bot in a group, so its /packs can list them: 90 days from your last use there, or until you leave the group\n• a timestamp each time you use the bot, to count users\n• daily button-tap counts for the whole bot, not per person\n• donations: the amount and Telegram's payment ID\n• whatever the bot is in the middle of doing with you, until it's done",
        "privacy_problems_heading": "When something goes wrong:",
        "privacy_problems": (
            "Every error is logged without you: its code, an incident number, the time and the bot's version.\n\n\"Add my details\" under an error attaches your Telegram ID, @username, language and chat type (private or group) to that one incident — only if you tap Send. Never your messages or files. They are cleared after 30 days, or at once with /deletemydata. Error records are deleted after 180 days."
        ),
        "privacy_seen_by_heading": "Who else sees it:",
        "privacy_seen_by": (
            "• Telegram, which carries every message\n• the hosting provider and the database the bot uses"
        ),
        "privacy_others": (
            "• nobody else — stickers are made on the bot's own server, and no outside service is used"
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
            "Stickers: upload only what you have the right to, and nothing Telegram's terms forbid. Once made, a pack lives on Telegram — the bot can forget it, but only its owner can delete it there."
        ),
        "terms_money": "Money: /donate is voluntary and helps pay for hosting. Stars also add ⚡ credit for ConvertBot: 2 ⚡ per ⭐, or 6 for your first 500 Stars and 4 for the next 500; the part above 2 is bonus that expires 90 days after payment. Payments are FINAL: Stars are NOT refunded and credit can NOT be withdrawn — except a payment that couldn't become credit, which is refunded automatically. Telegram handles payments; the bot never sees a card. Problems: /paysupport",
        "terms_no_warranty": (
            "No promises: one person runs this for free, and it can be slow, wrong or down without warning. Keep your own copies of what matters."
        ),
        "policy_full_text": "Full text: {url}",
        "policy_contact": "Questions, complaints or a data request: {contact}",
        "delete_data_confirm": "⚠️ This erases what this bot holds on you. There is no undo.",
        "delete_data_consequences": "The bot forgets your packs: it stops listing them and can't add to them. The packs keep working on Telegram — delete them there if you want. Your language, share links and others' access to your packs go too.\n\nPayment records stay, without your username, for payment disputes. Your ⚡ balance stays too.",
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
        "help_brief": "How it works:\n\n• /newpack makes a sticker pack: send pictures, GIFs, videos or stickers, an emoji after each if you like.\n• /addsticker adds to a pack you have; /mypacks lists them.\n• /import copies stickers from another Telegram pack or a WhatsApp export.\n• Ideas or problems: /feedback\n\nMain commands: /newpack · /addsticker · /mypacks · /cancel\nEvery command: the full guide, below or /fullhelp.\n\n",
        "help_full_button": "📖 Full guide",
        "help_brief_button": "↩️ Short version",
        "help_page_everything": "The full guide: /fullhelp",
        "help_page_gone": "That help page is gone. Send /help.",
        "helppage_newpack": '🆕 /newpack — a new sticker pack\n\n1. Send /newpack, then the pack\'s title as a message (up to {title_max} characters; longer is cut). If Telegram refuses the title, start it with a letter.\n2. Send what should become stickers, one per message: photos or image files, GIFs, videos, or stickers. The pack is made with the first one — no sticker, no pack.\n3. Each sticker gets {emoji}. To change it, send emoji right after it (up to {emoji_max}): they replace the last sticker\'s emoji.\n4. That\'s all: each sticker is saved as it is added, and the first one\'s answer has the pack\'s link. /cancel stops.\n\nHow each kind turns out:\n• A photo, an image file or a static sticker becomes a static sticker, {side} px on its longer side.\n• A GIF, a video or a video sticker becomes a video sticker: {side} px, the first {seconds} seconds, {fps} frames a second, no sound, under {kb} KB. If it can\'t be made that small, I say so and suggest our converter bot.\n• Animated (.tgs) stickers can\'t be added.\n• Many at once: a WhatsApp sticker pack (.zip or .wastickers), or /import from a Telegram pack.\n\nExample: /newpack, then "Cat faces", then three photos of your cat, then 😺.',
        "helppage_addsticker": '➕ /addsticker — more stickers in a pack you have\n\n1. /addsticker lists your packs. Tap one, then "➕ Add stickers".\n2. Send photos, GIFs, videos or stickers, just as for a new pack. Each gets {emoji}; emoji sent right after replace it.\n• Send a sticker that is already in the pack, and I ask whether to add it again or remove it. Removing the last one deletes the whole pack, so I ask once more. In a pack someone shares with you, removing is offered only if its owner lets co-editors delete.\n• Only packs made with this bot that are yours are listed. None yet? /newpack.\n• /mypacks shows the same list. Each pack\'s menu can also rename it, let someone else edit it with you, or delete it.\n\nExample: /addsticker, tap "Cat faces", tap "➕ Add stickers", send a new photo.',
        "helppage_import": '📥 /import — many stickers at once, from another pack\n\nIt works while you are filling a pack: after /newpack and its title, or after "➕ Add stickers".\n• /import followed by a public Telegram pack\'s link (t.me/addstickers/…) or its name copies its stickers into your pack, with their emoji. Up to {max} each time.\n• Animated (.tgs) stickers are skipped, and I say how many.\n• From WhatsApp: send the sticker pack\'s .zip or .wastickers file instead.\n• Then keep sending more whenever you like.\n\nExample: /newpack, then "Favourites", then /import t.me/addstickers/SomePack',
        "privacy_feedback_heading": "If you send /feedback:",
        "privacy_feedback": (
            "It reaches the person who runs these bots with the bot's name and your language — not your name, username or ID. They can't reply. A copy is kept for 30 days."
        ),
        "cancel_item_new_pack": "the new pack you were naming",
        "cancel_button_new_pack": "🆕 Naming a new pack",
        "cancel_item_rename": "renaming \"{title}\"",
        "cancel_button_rename": "✏️ Renaming a pack",
        "cancel_item_editing": "editing \"{title}\"",
        "cancel_button_editing": "📦 Editing a pack",
        # ---- stickerbot-specific keys ----
        "start_intro": (
            "Hey! I turn your images, GIFs, videos and stickers into Telegram sticker "
            "packs.\n\n"
        ),
        "help_text": "Commands:\n/newpack - start a new sticker pack\n/addsticker - add stickers to a pack you have\n/mypacks - your packs: rename, share, delete\n/import <pack link/name> - (while editing) copy stickers from another Telegram pack, or send a WhatsApp .zip/.wastickers file\n/done - close the pack you're filling (optional)\n/cancel - stop something I'm waiting for\n/feedback - write to whoever runs these bots (anonymous)\n/whomade <pack link/name> - who made a pack (if made with this bot)\n/donate - help pay for hosting (optional)\n/balance - your ⚡ credit\n/paysupport - trouble with a payment\n/en, /uz, /rus - switch language (or /language)\n/privacy - what I keep about you\n/terms - what I may be used for\n/deletemydata - delete everything I hold on you\n/start - start over\n/fullhelp - this guide (/help - the short one)\n/convert - file conversion is in ConvertBot\n\nWhile editing a pack: send pictures, GIFs, videos or stickers to add them, and an emoji right after one to change its emoji.\n\n⚠️ Payments are final — Stars are NOT refunded, and ⚡ credit can NOT be withdrawn.\n\n",
        "whomade_usage": "Usage: /whomade <pack name or t.me/addstickers link>",
        "whomade_not_found": (
            "I have no record of that pack — it wasn't made with this bot, or the name is wrong."
        ),
        "whomade_result": "📦 \"{title}\"\nCreated by {creator} on {date} (via this bot).",
        "coedit_link_invalid": "That co-editing link isn't valid — it may have been reset by the pack owner.",
        "coedit_pack_gone": "That pack doesn't seem to exist anymore.",
        "coedit_own_pack": "That's your own pack — use /mypacks to manage it.",
        "coedit_joined_intro": "You can now add to \"{title}\"! Send pictures, GIFs, videos or stickers. Each gets 😭; send an emoji right after to change it. The pack is in /mypacks.",
        "btn_new_pack": "➕ New pack",
        "btn_my_packs": "📁 My packs",
        "btn_help": "❓ Help",
        "btn_back": "⬅️ Back",
        "no_packs_yet": "No packs yet — tap New pack or use /newpack.",
        "your_packs": "Your packs:",
        "not_your_pack": "That's not your pack.",
        "pack_detail_title": "📦 {title}",
        "btn_open_pack": "🔗 Open pack",
        "btn_add_stickers": "➕ Add stickers",
        "btn_rename": "✏️ Rename",
        "btn_coedit": "👥 Co-edit",
        "btn_delete_pack": "🗑️ Delete pack",
        "coedit_message": "👥 Co-editing \"{title}\"\n\nLink: {link}\n{valid_line}\nCo-editors: {editors} of {max}{invited}\n\nWhoever opens the link can add stickers to this pack. A new link stops the old one.",
        "btn_reset_link": "🔄 Reset link",
        "only_owner_coedit": "Only the pack owner can manage co-editing.",
        "link_reset_confirm": "Link reset — the old one no longer works.",
        "only_owner_rename": "Only the pack owner can rename it.",
        "rename_prompt": "Send the new title for \"{title}\".",
        "rename_broken_state": "Something went wrong — try Rename again from /mypacks.",
        "btn_back_to_pack": "⬅️ Back to pack",
        "renamed_success": "Renamed to \"{title}\".",
        "renamed_failed": "Couldn't rename it: {error}",
        "only_owner_delete": "Only the pack owner can delete it.",
        "btn_delete": "🗑️ Delete",
        "btn_cancel_inline": "⬅️ Cancel",
        "delete_confirm1": (
            "⚠️ Delete \"{title}\"? This removes it from Telegram for everyone who "
            "has it, including any co-editors, and can't be undone."
        ),
        "btn_delete_confirm": "🗑️ Yes, permanently delete it",
        "delete_confirm2": "❗ Last check — permanently delete \"{title}\"? There's no undo after this.",
        "delete_failed": "⚠️ Couldn't delete it: {error}",
        "btn_my_packs_back": "⬅️ My packs",
        "delete_success": "🗑️ \"{title}\" has been permanently deleted.",
        "newpack_title_prompt": "What should the pack title be?",
        "title_empty": "That's empty — send an actual title for the pack.",
        "title_truncated": "Telegram caps pack titles at 64 characters — using \"{title}\".",
        "editing_intro_new": "Send pictures, GIFs, videos or stickers. Each gets 😭; send an emoji right after to change it.",
        "no_packs_for_add": "You don't have any packs yet. Use /newpack first.",
        "pick_pack_prompt": "Which pack? Tap it, then \"➕ Add stickers\".",
        "editing_intro_add": "Send pictures, GIFs, videos or stickers to add. Each gets 😭; send an emoji right after to change it.",
        "editing_intro_shared": "Send pictures, GIFs, videos or stickers to add. Each gets 😭; send an emoji right after to change it.",
        "status_verb_creating": "Creating",
        "status_verb_editing": "Editing",
        "status_default_title": "this pack",
        "btn_delete_pack_yes": "🗑️ Yes, delete the pack",
        "btn_cancel": "Cancel",
        "remove_last_confirm": (
            "That's the last sticker — removing it deletes the *whole pack*. Sure?"
        ),
        "remove_failed": "⚠️ Couldn't remove that sticker: {error}",
        "remove_success": "🗑️ Removed from the pack.",
        "remove_last_owner_only": "That's the last sticker. Only the pack's owner can remove it, as it deletes the pack.",
        "keep_pack": "OK, kept the pack.",
        "pack_deleted_empty": "🗑️ Pack deleted (it had no stickers left).",
        "pack_deleted_note": "❌ Pack deleted.",
        "image_process_failed": "Couldn't process that image: {error}",
        "last_attempt_failed": "⚠️ Last attempt failed — send another item to retry, or /cancel.",
        "converting_video": "Converting to a video sticker...",
        "video_convert_failed_redirect": (
            "{error}\n\nNeed it as a normal file? Send it to @ConvertBot 👇"
        ),
        "video_convert_generic_failed": "Couldn't convert that: {error}",
        "animated_not_supported": (
            "Animated (.tgs) stickers aren't supported — send a picture, GIF, video or video sticker."
        ),
        "import_usage": (
            "Send /import and a Telegram pack's link or name, or a WhatsApp .zip/.wastickers file."
        ),
        "import_invalid_source": "That doesn't look like a valid pack name or t.me/addstickers link.",
        "import_fetching": "Fetching stickers from \"{source}\"...",
        "import_summary_head": "Imported {added} sticker(s) from \"{source}\"",
        "import_summary_skipped": ", skipped {skipped} unsupported (animated/Lottie)",
        "import_summary_failed": ", {failed} failed",
        "import_summary_tail": '. Keep sending more whenever you like.',
        "done_standalone_hint": (
            "You aren't editing a pack. Start one with /newpack or open one in /mypacks."
        ),
        "import_standalone_hint": (
            "Open a pack first (/newpack or /mypacks), then send /import <link>."
        ),
        "whatsapp_reading": "Reading the WhatsApp sticker pack...",
        "whatsapp_summary_head": "Imported {added} sticker(s) from the WhatsApp pack",
        "no_sticker_to_tag": "Add a sticker first, then send emoji to tag it.",
        "retagged_success": "Retagged as {emojis}.",
        "retag_failed": "Couldn't update the emoji: {error}",
        "nothing_added_yet": "You haven't added anything yet. Send an image first.",
        "done_success": (
            "✅ Finished \"{title}\" — {count} sticker(s) added.\n\nhttps://t.me/addstickers/{pack_name}"
        ),
        "convert_redirect": (
            "File conversion is in @ConvertBot — tap below."
        ),
        "cancelled_status_note": "❌ Cancelled.",
        "unrecognized": "Not sure what that's for — try /newpack, /mypacks, or /help.",
        "unknown_command": "I don't recognize that command. Send /help to see what I can do.",
        "err_invalid_name": (
            "⚠️ Telegram refused this title. Send /cancel, then /newpack with a title that starts with a letter."
        ),
        "err_name_occupied": (
            "⚠️ Telegram had a name clash (rare). Send /cancel, then /newpack again."
        ),
        "err_too_many_stickers": "⚠️ This pack is already at Telegram's sticker limit (120) — start a new pack with /newpack instead.",
        "err_bad_format": "⚠️ Telegram didn't accept that file's format for this pack — try a different image.",
        "err_generic": "⚠️ Telegram rejected that: {msg}\n\nYou can try again, or /cancel to stop.",
        "err_timed_out": (
            "⚠️ Telegram didn't confirm in time, but it may have worked — check the pack before retrying. Or /cancel."
        ),
        "restarting_send_again": "🔄 I'm being updated — send that again in a few seconds.",
        "update_soon_try_later": "🔧 I'm about to be updated — please try again in about {minutes} min. I'll message you when I'm back.",
        "update_soon_try_later_soon": "🔧 I'm being updated — please try again shortly. I'll message you when I'm back.",
        "update_will_reset": "🔧 I'm about to be updated, and what you're doing now will be reset. You can start again in a few minutes.",
        "update_done_try_now": "✅ Update done — try again now.",
        "gift_granted": "🎁 We passed 100 users — thank you! Here's {amount} ⚡ of free credit, good until {date}: about {conversions} paid conversions in {convert} or big downloads in {download}. /balance",
        "video_convert_ffmpeg_missing": (
            "ffmpeg isn't installed on this host, so GIF/video stickers can't "
            "be converted. Install it with 'apt install ffmpeg' (Linux), "
            "'brew install ffmpeg' (Mac), or add a Windows build to PATH."
        ),
        "video_convert_empty_file": "That file came through empty — try sending it again.",
        "video_convert_too_big": (
            "Couldn't fit this clip into Telegram's 256 KB limit ({note}). Try a shorter or simpler clip."
        ),
        "import_pack_not_found": (
            "Couldn't find a public pack called \"{source}\"."
        ),
        "import_bad_zip": "That doesn't look like a valid .zip/.wastickers file.",
        "import_zip_no_images": "No usable images found inside that zip.",
        # 2.2.0: per-sticker replies, emoji by reply, public packs, group chats.
        "status_line_open": '📝 {verb} "{title}"',
        "sticker_added_to": '✅ Added to “{title}” as #{n} with {emoji}. Reply to this with an emoji to change it.',
        "emoji_target_unknown": "I can't tell which sticker that is any more. Reply to the sticker itself instead.",
        "emoji_not_ours": "That sticker isn't from a pack made with this bot, so I can't change it.",
        "emoji_usage": 'Reply to a sticker, or to my “Added” message, with /emoji and the emoji — for example /emoji 😎',
        "emoji_send_reply": 'Reply to this message with the new emoji for that sticker.',
        "already_in_pack": "This sticker is already in “{title}”. Add it again?",
        "already_in_pack_owner": 'This sticker is already in “{title}”. Add it again, or remove it from the pack?',
        "already_in_pack_group": "This sticker is already in “{title}”. Add it again?",
        "btn_add_again": '➕ Add again',
        "btn_dont_add": "✖ Don't add",
        "btn_remove_it": '🗑 Remove it',
        "remove_not_allowed": "Only the pack's owner can delete its stickers.",
        "dup_not_added": 'OK, not added.',
        "dup_expired": 'That question has expired. Send the sticker again.',
        "dup_not_yours": 'Only the person who sent it can answer.',
        "btn_pack_private": '🔒 Private · tap to make it public in groups',
        "btn_pack_public": '🌐 Public in groups · tap to make it private',
        "pack_now_public": "🌐 “{title}” is public now: anyone can add to it in groups where you use me.",
        "pack_now_private": '🔒 “{title}” is private now. Only you and its co-editors can add to it.',
        "group_hello": "👋 Hi! I make sticker packs.\n\n• /packs — packs you can add to here\n• reply to a picture, GIF, video or sticker with /add and a number from /packs\n• reply to a sticker with /emoji 😎 to change its emoji\n\nTo let people add to a private pack, post its co-edit link here.",
        "group_packs_head": '📦 Packs you can add to here:',
        "group_packs_tail": 'Reply to a picture with /add and the number.',
        "group_packs_none": "No public packs here yet. A pack's owner can make it public in my chat (/mypacks → the pack → 🔒), or post its co-edit link here.",
        "group_add_need_reply": 'Reply to a picture, GIF, video or sticker with /add.',
        "group_add_which": 'Which pack? /add and its number from /packs, or its link.',
        "group_pack_unknown": "I don't know that pack. Use a number from /packs or a pack link.",
        "group_no_access": "🔒 You don't have access to “{title}”. Its owner can make it public, or post its co-edit link here.",
        "group_adding": '⏳ Adding...',
        "helppage_groups": "👥 In group chats\n\nAdd me to a group, then:\n• /packs lists the packs you can add to there, numbered.\n• Reply to a picture, GIF, video or sticker with /add 2 (the number), or /add and a pack's link. /add on its own uses the pack you last added to there. /add on a sticker that is already in the pack asks whether to add it again: nothing is deleted in a group.\n• To change a sticker's emoji, reply to it with /emoji 😎; a reply to my “Added” message needs only the emoji. (In a group I only react to commands and to replies to my own messages.) In my chat an emoji alone, as a reply to any sticker of your packs, does it too.\n\nWho can add: a pack's owner and its co-editors, anywhere. Public packs (/mypacks → a pack → 🔒/🌐) also take stickers from anybody in a group where their owner or a co-editor uses me. Private packs, the default, are not listed; post a pack's co-edit link in the group to let people in.",
        "help_btn_groups": '👥 In groups',
        # 2.2.0: /done optional; co-edit link limits and invitations.
        "pack_created_link": '📦 Your pack is made: https://t.me/addstickers/{pack_name}',
        "coedit_link_expired": "That co-editing link has expired. Ask the pack's owner for a new one.",
        "coedit_link_full": 'That pack already has as many co-editors as its owner allows. Ask the owner to raise the limit.',
        "coedit_removed_access": '🔒 You can no longer add to "{title}": its owner removed you.',
        "invites_claimed": "👥 You were invited to add to {titles}. You'll find them in /mypacks.",
        "your_packs_shared_note": '👥 — packs other people share with you.',
        "co_period_1": '1 day',
        "co_period_7": '1 week',
        "co_period_30": '1 month',
        "co_period_none": 'no limit',
        "co_valid_forever": 'Valid: no time limit',
        "co_valid_until": 'Valid until {date} ({left} left)',
        "co_valid_expired": '⚠️ Expired on {date}: nobody new can join. Tap ⏳ to open it again, or make a new link.',
        "co_left_days": '{n} days',
        "co_left_hours": '{n} h',
        "co_invited_count": ' (+{n} invited by @username)',
        "btn_co_valid": '⏳ Link valid: {period}',
        "btn_co_max": '👥 Max co-editors: {n}',
        "btn_co_invite": '➕ Add by @username',
        "btn_co_list": '📋 Who can add ({n})',
        "co_valid_set": 'The link now works for {period}.',
        "co_max_set": 'At most {n} co-editors.',
        "btn_co_delete_off": '🗑 Co-editors can delete: no',
        "btn_co_delete_on": '🗑 Co-editors can delete: yes',
        "co_delete_set_on": "Co-editors can now delete stickers (never the last one).",
        "co_delete_set_off": 'Only you can delete stickers now.',
        "co_removed": 'Removed.',
        "co_list_heading": '📋 Who can add to "{title}":',
        "co_list_invited": "• @{username} — invited, hasn't used me yet",
        "co_list_more": '…and {n} more.',
        "co_list_empty": 'Nobody yet.',
        "co_list_remove_hint": 'Tap ✖ to take someone out. Stickers they added stay.',
        "invite_unavailable": "Adding by @username isn't available right now. Try again later, or use the link.",
        "invite_prompt": "Send the @usernames of people who may add to \"{title}\", separated by spaces. Unused invitations expire after 30 days. /cancel to stop.",
        "invite_done": '✅ Invited: {names}.',
        "invite_full": 'Not invited, the pack would have more than {max} co-editors: {names}. Raise the limit with 👥 first.',
        "invite_bad": 'Not usernames: {names}',
        "cancel_item_invite": 'the @usernames for "{title}"',
        "cancel_button_invite": '👥 Inviting people',
    },
    "uz": {
        "whomade_someone": "ismini oshkor qilmagan kishi",
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
            "Rasm, GIF va videolardan Telegram stiker to'plamlarini yasaydi."
        ),
        "bot_description": (
            "Rasm, GIF yoki video yuboring — bot undan o'zingizga tegishli stiker to'plamini "
            "yig'adi.\n"
            "\n"
            "To'plamni boshqa birov ham to'ldira olishi uchun havolasini ulashing yoki boshqa "
            "Telegram to'plamidan hamda WhatsApp arxividan ko'chirib oling.\n"
            "\n"
            "Ingliz, o'zbek va rus tillarida. /privacy nima saqlanishini aytadi."
        ),
        # ---- shared policy keys (/privacy, /terms, /deletemydata) ----
        "privacy_heading": "🔒 Maxfiylik",
        "privacy_kept_heading": "Bu bot nimalarni saqlaydi:",
        "privacy_stored": "• Telegram ID raqamingiz va tilingiz\n• yaratgan to'plamlaringiz: nomi, sarlavhasi va o'sha paytdagi ismingiz va username'ingiz\n• to'plamlaringizga kim qo'sha olishi, cheklovlari bilan ulashish havolalaringiz va taklif qilingan @username'lar (shifrlangan; ular botdan foydalanguncha, ko'pi bilan 30 kun)\n• qaysi to'plamlaringiz ochiq va botdan guruhda foydalanganingiz — guruhdagi /packs ularni ko'rsatishi uchun: u yerdagi oxirgi foydalanishdan 90 kun yoki guruhdan chiqquningizcha\n• har safar botdan foydalanganingiz vaqti — foydalanuvchilarni sanash uchun\n• butun bot bo'yicha kunlik tugma bosishlar soni, odamlar bo'yicha emas\n• xayriyalar: miqdor va Telegram to'lov ID si\n• bot siz bilan hozir bajarayotgan ish, u tugaguncha",
        "privacy_problems_heading": "Nimadir noto'g'ri ketganda:",
        "privacy_problems": "Har bir xato sizsiz yoziladi: kodi, hodisa raqami, vaqti va bot versiyasi.\n\nXato ostidagi «Ma'lumotlarimni qo'shish» o'sha hodisaga Telegram ID, @username, til va chat turini (shaxsiy yoki guruh) qo'shadi — faqat «Yuborish»ni bossangiz. Xabarlar va fayllar hech qachon qo'shilmaydi. Ular 30 kundan keyin yoki /deletemydata bilan darhol o'chiriladi. Xato yozuvlari 180 kundan keyin o'chiriladi.",
        "privacy_seen_by_heading": "Yana kim ko'ra oladi:",
        "privacy_seen_by": "• Telegram — barcha xabarlarni u yetkazadi\n• hosting provayderi va bot ishlatadigan ma'lumotlar bazasi",
        "privacy_others": "• boshqa hech kim — stikerlar botning o'z serverida yasaladi, tashqi xizmatlar ishlatilmaydi",
        "privacy_kept_for_heading": "Qancha vaqt saqlanadi:",
        "privacy_kept_for": "Sozlamalar siz o'chirmaguningizcha yoki botdan foydalanishni to'xtatmaguningizcha saqlanadi. Foydalanish vaqtlari taxminan uch oydan keyin o'chiriladi. To'lov yozuvlari va ⚡ balansingiz to'lov nizolari uchun uzoqroq saqlanadi.\n\nHech narsa sotilmaydi, reklamaga ishlatilmaydi va yuqorida aytilmaganlarga berilmaydi.",
        "privacy_your_choices": "Nima qila olasiz:\n/deletemydata — bot siz haqingizda saqlagan narsalarni o'chirish\n/terms — qoidalar\n\nBotni bloklash hech narsani o'chirmaydi, shuning uchun avval /deletemydata yuboring.",
        "terms_heading": "📜 Shartlar",
        "terms_use": "Botdan qonun va Telegram shartlari doirasida foydalaning. Kimnidir bezovta qilgan yoki bot chegaralarini buzgan akkaunt bloklanadi.",
        "terms_specific": "Stikerlar: faqat huquqingiz bor narsani va Telegram shartlari taqiqlamagan narsani yuklang. Yaratilgan to'plam Telegram'da yashaydi — bot uni unutishi mumkin, lekin uni u yerda faqat egasi o'chira oladi.",
        "terms_money": "Pul: /donate ixtiyoriy va server xarajatlariga yordam beradi. Stars ConvertBot uchun ⚡ kredit ham beradi: har ⭐ uchun 2 ⚡, ilk 500 Stars uchun 6 va keyingi 500 uchun 4; 2 dan ortig'i bonus bo'lib, to'lovdan 90 kun o'tgach yo'qoladi. To'lovlar QAYTARILMAYDI: Stars qaytarilMAYDI, kreditni yechib olib BO'LMAYDI — kreditga aylanmagan to'lov bundan mustasno, u avtomatik qaytariladi. To'lovlarni Telegram amalga oshiradi; bot karta raqamini ko'rmaydi. Muammo: /paysupport",
        "terms_no_warranty": "Kafolat yo'q: buni bir kishi bepul yuritadi, u sekin ishlashi, xato qilishi yoki ogohlantirishsiz to'xtashi mumkin. Muhim narsalarning nusxasini o'zingizda saqlang.",
        "policy_full_text": "To'liq matn: {url}",
        "policy_contact": "Savol, shikoyat yoki ma'lumot so'rovi uchun: {contact}",
        "delete_data_confirm": "⚠️ Bot siz haqingizda saqlagan ma'lumotlar o'chiriladi. Buni ortga qaytarib bo'lmaydi.",
        "delete_data_consequences": "Bot to'plamlaringizni unutadi: ularni ko'rsatmaydi va ularga qo'sha olmaydi. To'plamlar Telegram'da ishlayveradi — xohlasangiz, ularni o'sha yerda o'chiring. Tilingiz, ulashish havolalaringiz va boshqalarning to'plamlaringizga kirishi ham o'chadi.\n\nTo'lov yozuvlari to'lov nizolari uchun username'ingizsiz qoladi. ⚡ balansingiz ham qoladi.",
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
        "help_brief": "Bot qanday ishlaydi:\n\n• /newpack stiker to'plami yaratadi: rasm, GIF, video yoki stiker yuboring, xohlasangiz har biridan keyin emoji.\n• /addsticker mavjud to'plamga qo'shadi; /mypacks ularni ko'rsatadi.\n• /import boshqa Telegram to'plamidan yoki WhatsApp eksportidan stikerlarni ko'chiradi.\n• Taklif yoki muammo: /feedback\n\nAsosiy buyruqlar: /newpack · /addsticker · /mypacks · /cancel\nBarcha buyruqlar: to'liq qo'llanma, pastda yoki /fullhelp.\n\n",
        "help_full_button": "📖 To'liq qo'llanma",
        "help_brief_button": "↩️ Qisqa variant",
        "help_page_everything": "To'liq qo'llanma: /fullhelp",
        "help_page_gone": "Bu yordam sahifasi endi yo'q. /help yuboring.",
        "helppage_newpack": '🆕 /newpack — yangi stiker to\'plami\n\n1. /newpack yuboring, keyin to\'plamning nomini xabar qilib ({title_max} belgigacha; uzunrog\'i qisqartiriladi). Telegram nomni qabul qilmasa, uni harf bilan boshlang.\n2. Stikerga aylanadigan narsalarni har birini alohida xabarda yuboring: rasm yoki rasm-fayl, GIF, video yoki stiker. To\'plam birinchisi bilan yaratiladi — stiker bo\'lmasa, to\'plam ham bo\'lmaydi.\n3. Har bir stikerga {emoji} beriladi. O\'zgartirish uchun darhol keyin emoji yuboring ({emoji_max} tagacha): ular oxirgi stikerning emojisini almashtiradi.\n4. Bo\'ldi: har bir stiker qo\'shilishi bilan saqlanadi, birinchisiga javobda to\'plam havolasi bor. /cancel to\'xtatadi.\n\nHar biri nimaga aylanadi:\n• Rasm, rasm-fayl yoki oddiy stiker oddiy stikerga aylanadi, uzun tomoni {side} px.\n• GIF, video yoki video-stiker video-stikerga aylanadi: {side} px, dastlabki {seconds} soniya, soniyasiga {fps} kadr, ovozsiz, {kb} KB dan kichik. Shunchalik kichraytirib bo\'lmasa, aytaman va konvertor botimizni taklif qilaman.\n• Animatsiyali (.tgs) stikerlarni qo\'shib bo\'lmaydi.\n• Ko\'pini birdan: WhatsApp stiker to\'plami (.zip yoki .wastickers) yoki Telegram to\'plamidan /import.\n\nMisol: /newpack, keyin "Mushuk yuzlari", keyin mushugingizning uchta rasmi, keyin 😺.',
        "helppage_addsticker": '➕ /addsticker — mavjud to\'plamingizga yangi stikerlar\n\n1. /addsticker to\'plamlaringiz ro\'yxatini chiqaradi. Birini bosing, keyin "➕ Stiker qo\'shish".\n2. Rasm, GIF, video yoki stikerlarni yangi to\'plamdagidek yuboring. Har biriga {emoji} beriladi; darhol keyin yuborilgan emoji uni almashtiradi.\n• To\'plamda allaqachon bor stikerni yuborsangiz, uni yana qo\'shish yoki olib tashlashni so\'rayman. Oxirgisini olib tashlash butun to\'plamni o\'chiradi, shuning uchun yana bir bor so\'rayman. Siz bilan ulashilgan to\'plamda olib tashlash faqat egasi hammualliflarga o\'chirishga ruxsat bergan bo\'lsa taklif qilinadi.\n• Faqat shu bot orqali yaratilgan va sizniki bo\'lgan to\'plamlar ko\'rsatiladi. Hali yo\'qmi? /newpack.\n• /mypacks ham shu ro\'yxatni ko\'rsatadi. Har bir to\'plam menyusida uni qayta nomlash, boshqa birov bilan birga tahrirlash yoki o\'chirish ham mumkin.\n\nMisol: /addsticker, "Mushuk yuzlari" ni bosing, "➕ Stiker qo\'shish" ni bosing, yangi rasm yuboring.',
        "helppage_import": '📥 /import — boshqa to\'plamdan ko\'p stikerni birdan\n\nTo\'plamni to\'ldirayotganingizda ishlaydi: /newpack va nomidan keyin yoki "➕ Stiker qo\'shish" dan keyin.\n• /import va ochiq Telegram to\'plamining havolasi (t.me/addstickers/…) yoki nomi uning stikerlarini emojilari bilan birga to\'plamingizga ko\'chiradi. Har safar {max} tagacha.\n• Animatsiyali (.tgs) stikerlar o\'tkazib yuboriladi va nechtasi ekanini aytaman.\n• WhatsApp\'dan: buning o\'rniga stiker to\'plamining .zip yoki .wastickers faylini yuboring.\n• Keyin xohlagan paytingizda yana yuborishingiz mumkin.\n\nMisol: /newpack, keyin "Sevimlilar", keyin /import t.me/addstickers/SomePack',
        "privacy_feedback_heading": "/feedback yuborsangiz:",
        "privacy_feedback": (
            "U botlarni yuritayotgan odamga bot nomi va tilingiz bilan yetib boradi — ismingiz, username yoki ID raqamingiz bilan emas. U javob yoza olmaydi. Nusxasi 30 kun saqlanadi."
        ),
        "cancel_item_new_pack": "siz nom qo'yayotgan yangi to'plam",
        "cancel_button_new_pack": "🆕 Yangi to'plamga nom berish",
        "cancel_item_rename": "\"{title}\" nomini o'zgartirish",
        "cancel_button_rename": "✏️ To'plam nomini o'zgartirish",
        "cancel_item_editing": "\"{title}\" ni tahrirlash",
        "cancel_button_editing": "📦 To'plamni tahrirlash",
        "start_intro": "Salom! Rasm, GIF, video va stikerlaringizdan Telegram stiker to'plamlari yasayman.\n\n",
        "help_text": "Buyruqlar:\n/newpack - yangi stiker to'plami yaratish\n/addsticker - mavjud to'plamga stiker qo'shish\n/mypacks - to'plamlaringiz: nomini o'zgartirish, ulashish, o'chirish\n/import <to'plam havolasi yoki nomi> - (tahrirlash vaqtida) boshqa Telegram to'plamidan ko'chirish yoki WhatsApp .zip/.wastickers faylini yuborish\n/done - to'ldirilayotgan to'plamni yopish (ixtiyoriy)\n/cancel - men kutayotgan narsani to'xtatish\n/feedback - botlarni yuritayotgan odamga yozish (anonim)\n/whomade <to'plam havolasi yoki nomi> - to'plamni kim yaratgan (shu bot orqali bo'lsa)\n/donate - server xarajatlariga yordam (ixtiyoriy)\n/balance - ⚡ kreditingiz\n/paysupport - to'lov bilan muammo\n/en, /uz, /rus - tilni almashtirish (yoki /language)\n/privacy - siz haqingizda nimalar saqlanadi\n/terms - botdan nima uchun foydalanish mumkin\n/deletemydata - siz haqingizdagi hamma narsani o'chirish\n/start - boshidan boshlash\n/fullhelp - shu qo'llanma (/help - qisqasi)\n/convert - fayllarni o'girish ConvertBot'da\n\nTo'plamni tahrirlash vaqtida: qo'shish uchun rasm, GIF, video yoki stiker yuboring, emojisini o'zgartirish uchun darhol emoji yuboring.\n\n⚠️ To'lovlar qaytarilmaydi — Stars qaytarilMAYDI, ⚡ kreditni yechib olib BO'LMAYDI.\n\n",
        "whomade_usage": "Qanday ishlatiladi: /whomade <to'plam nomi yoki t.me/addstickers havolasi>",
        "whomade_not_found": (
            "Bu to'plam haqida ma'lumot yo'q — u shu bot orqali yaratilmagan yoki nomi noto'g'ri."
        ),
        "whomade_result": '📦 "{title}"\n{date} kuni {creator} tomonidan shu bot orqali yaratilgan.',
        "coedit_link_invalid": "Bu havola yaroqsiz — ehtimol, to'plam egasi uni yangilagan.",
        "coedit_pack_gone": "Bu to'plam o'chirilganga o'xshaydi.",
        "coedit_own_pack": "Bu o'zingizning to'plamingiz — uni /mypacks orqali boshqaring.",
        "coedit_joined_intro": "Endi \"{title}\" to'plamiga qo'sha olasiz! Rasm, GIF, video yoki stiker yuboring. Har biriga 😭 qo'yiladi; o'zgartirish uchun darhol emoji yuboring. To'plam /mypacks da.",
        "btn_new_pack": "➕ Yangi to'plam",
        "btn_my_packs": "📁 Mening to'plamlarim",
        "btn_help": "❓ Yordam",
        "btn_back": "⬅️ Orqaga",
        "no_packs_yet": "Hali to'plamingiz yo'q — «Yangi to'plam» tugmasini bosing yoki /newpack yuboring.",
        "your_packs": "Sizning to'plamlaringiz:",
        "not_your_pack": "Bu sizning to'plamingiz emas.",
        "pack_detail_title": "📦 {title}",
        "btn_open_pack": "🔗 To'plamni ochish",
        "btn_add_stickers": "➕ Stiker qo'shish",
        "btn_rename": "✏️ Nomini o'zgartirish",
        "btn_coedit": '👥 Birgalikda tahrirlash',
        "btn_delete_pack": "🗑️ To'plamni o'chirish",
        "coedit_message": "👥 \"{title}\" — birgalikda tahrirlash\n\nHavola: {link}\n{valid_line}\nHammualliflar: {editors} / {max}{invited}\n\nHavolani ochgan har kim bu to'plamga stiker qo'sha oladi. Yangi havola eskisini to'xtatadi.",
        "btn_reset_link": "🔄 Havolani yangilash",
        "only_owner_coedit": "Birgalikda tahrirlashni faqat to'plam egasi boshqara oladi.",
        "link_reset_confirm": "Havola yangilandi — eskisi endi ishlamaydi.",
        "only_owner_rename": "To'plamni faqat egasi qayta nomlay oladi.",
        "rename_prompt": "\"{title}\" uchun yangi nom yuboring.",
        "rename_broken_state": "Xatolik yuz berdi — /mypacks orqali «Nomini o'zgartirish»ni qaytadan bosing.",
        "btn_back_to_pack": "⬅️ To'plamga qaytish",
        "renamed_success": "\"{title}\" deb qayta nomlandi.",
        "renamed_failed": "Nomini o'zgartirib bo'lmadi: {error}",
        "only_owner_delete": "To'plamni faqat egasi o'chira oladi.",
        "btn_delete": "🗑️ O'chirish",
        "btn_cancel_inline": "⬅️ Bekor qilish",
        "delete_confirm1": '⚠️ "{title}" o\'chirilsinmi? U Telegram\'dan hamma uchun, jumladan hammualliflar uchun ham o\'chib ketadi. Buni ortga qaytarib bo\'lmaydi.',
        "btn_delete_confirm": "🗑️ Ha, butunlay o'chirilsin",
        "delete_confirm2": "❗ Oxirgi tekshiruv — \"{title}\" butunlay o'chirilsinmi? Bundan keyin ortga qaytarib bo'lmaydi.",
        "delete_failed": "⚠️ O'chirib bo'lmadi: {error}",
        "btn_my_packs_back": "⬅️ Mening to'plamlarim",
        "delete_success": "🗑️ \"{title}\" butunlay o'chirildi.",
        "newpack_title_prompt": "To'plamning nomi qanday bo'lsin?",
        "title_empty": "Nom bo'sh bo'lmasin — to'plam uchun nom yuboring.",
        "title_truncated": "Telegram to'plam nomini 64 belgigacha cheklaydi — \"{title}\" ishlatiladi.",
        "editing_intro_new": "Rasm, GIF, video yoki stiker yuboring. Har biriga 😭 qo'yiladi; o'zgartirish uchun darhol emoji yuboring.",
        "no_packs_for_add": "Hali to'plamingiz yo'q. Avval /newpack yuboring.",
        "pick_pack_prompt": "Qaysi to'plam? Uni bosing, keyin \"➕ Stiker qo'shish\"ni tanlang.",
        "editing_intro_add": "Qo'shish uchun rasm, GIF, video yoki stiker yuboring. Har biriga 😭 qo'yiladi; o'zgartirish uchun darhol emoji yuboring.",
        "editing_intro_shared": "Qo'shish uchun rasm, GIF, video yoki stiker yuboring. Har biriga 😭 qo'yiladi; o'zgartirish uchun darhol emoji yuboring.",
        "status_verb_creating": "Yaratilmoqda",
        "status_verb_editing": "Tahrirlanmoqda",
        "status_default_title": "bu to'plam",
        "btn_delete_pack_yes": "🗑️ Ha, to'plam o'chirilsin",
        "btn_cancel": "Bekor qilish",
        "remove_last_confirm": "Bu oxirgi stiker — uni olib tashlasangiz, *butun to'plam* o'chadi. Ishonchingiz komilmi?",
        "remove_failed": "⚠️ Bu stikerni olib tashlab bo'lmadi: {error}",
        "remove_success": "🗑️ To'plamdan olib tashlandi.",
        "remove_last_owner_only": "Bu oxirgi stiker. Uni faqat to'plam egasi olib tashlay oladi, chunki bu to'plamni o'chiradi.",
        "keep_pack": "Yaxshi, to'plam qoldirildi.",
        "pack_deleted_empty": "🗑️ To'plam o'chirildi (unda stiker qolmagan edi).",
        "pack_deleted_note": "❌ To'plam o'chirildi.",
        "image_process_failed": "Rasmni qayta ishlab bo'lmadi: {error}",
        "last_attempt_failed": "⚠️ Oxirgi urinish muvaffaqiyatsiz tugadi — qayta urinish uchun boshqa narsa yuboring yoki /cancel qiling.",
        "converting_video": "Video stikerga aylantirilmoqda...",
        "video_convert_failed_redirect": "{error}\n\nOddiy fayl sifatida kerakmi? Uni @ConvertBot ga yuboring 👇",
        "video_convert_generic_failed": "Buni aylantirib bo'lmadi: {error}",
        "animated_not_supported": (
            "Animatsiyali (.tgs) stikerlar qo'llab-quvvatlanmaydi — rasm, GIF, video yoki video stiker yuboring."
        ),
        "import_usage": "/import va Telegram to'plami havolasi yoki nomini yuboring, yoki WhatsApp .zip/.wastickers faylini.",
        "import_invalid_source": "Bu haqiqiy to'plam nomi yoki t.me/addstickers havolasiga o'xshamayapti.",
        "import_fetching": "\"{source}\" dan stikerlar olinmoqda...",
        "import_summary_head": "\"{source}\" dan {added} ta stiker import qilindi",
        "import_summary_skipped": ", {skipped} ta qo'llab-quvvatlanmaydigani (animatsion/Lottie) o'tkazib yuborildi",
        "import_summary_failed": ", {failed} tasi muvaffaqiyatsiz tugadi",
        "import_summary_tail": '. Xohlagan paytingizda yana yuborishingiz mumkin.',
        "done_standalone_hint": (
            "Hozir to'plam tahrirlanmayapti. /newpack bilan boshlang yoki /mypacks dan oching."
        ),
        "import_standalone_hint": (
            "Avval to'plamni oching (/newpack yoki /mypacks), keyin /import <havola> yuboring."
        ),
        "whatsapp_reading": "WhatsApp stiker to'plami o'qilmoqda...",
        "whatsapp_summary_head": "WhatsApp to'plamidan {added} ta stiker import qilindi",
        "no_sticker_to_tag": "Avval stiker qo'shing, keyin uni belgilash uchun emoji yuboring.",
        "retagged_success": "Emoji o'zgartirildi: {emojis}",
        "retag_failed": "Emojini yangilab bo'lmadi: {error}",
        "nothing_added_yet": "Siz hali hech narsa qo'shmadingiz. Avval rasm yuboring.",
        "done_success": (
            "✅ \"{title}\" tugatildi — {count} ta stiker qo'shildi.\n\nhttps://t.me/addstickers/{pack_name}"
        ),
        "convert_redirect": "Fayllarni o'girish @ConvertBot da — pastdagi tugmani bosing.",
        "cancelled_status_note": "❌ Bekor qilindi.",
        "unrecognized": "Bu nima uchunligini tushunmadim — /newpack, /mypacks yoki /help ni sinab ko'ring.",
        "unknown_command": "Bunday buyruq yo'q. Bot nimalar qila olishini bilish uchun /help yuboring.",
        "err_invalid_name": (
            "⚠️ Telegram bu nomni qabul qilmadi. /cancel yuboring, keyin harf bilan boshlanadigan nom bilan /newpack."
        ),
        "err_name_occupied": (
            "⚠️ Telegram'da nom to'qnashdi (kamdan-kam). /cancel yuboring, keyin yana /newpack."
        ),
        "err_too_many_stickers": "⚠️ Bu to'plam Telegram'ning stiker chegarasiga (120) allaqachon yetgan — buning o'rniga /newpack bilan yangi to'plam boshlang.",
        "err_bad_format": "⚠️ Telegram bu fayl formatini shu to'plam uchun qabul qilmadi — boshqa rasmni sinab ko'ring.",
        "err_generic": "⚠️ Telegram buni rad etdi: {msg}\n\nQayta urinib ko'rishingiz mumkin, yoki to'xtatish uchun /cancel qiling.",
        "err_timed_out": (
            "⚠️ Telegram o'z vaqtida tasdiqlamadi, lekin bajarilgan bo'lishi mumkin — qayta urinishdan oldin to'plamni tekshiring. Yoki /cancel."
        ),
        "restarting_send_again": "🔄 Bot yangilanmoqda — bir necha soniyadan keyin qayta yuboring.",
        "update_soon_try_later": "🔧 Bot hozir yangilanadi — taxminan {minutes} daqiqadan keyin qayta urinib ko'ring. Ishga tushgach, xabar beraman.",
        "update_soon_try_later_soon": "🔧 Bot yangilanmoqda — birozdan keyin qayta urinib ko'ring. Ishga tushgach, xabar beraman.",
        "update_will_reset": "🔧 Bot hozir yangilanadi va hozirgi ishingiz to'xtaydi. Bir necha daqiqadan keyin qaytadan boshlashingiz mumkin.",
        "update_done_try_now": "✅ Yangilanish tugadi — endi qayta urinib ko'ring.",
        "gift_granted": "🎁 100 foydalanuvchidan oshdik — rahmat! Sizga {amount} ⚡ bepul kredit, {date} gacha amal qiladi: {convert} da taxminan {conversions} ta pullik konvertatsiya yoki {download} da katta fayllar. /balance",
        "video_convert_ffmpeg_missing": (
            "Bu serverda ffmpeg o'rnatilmagan, shuning uchun GIF/video stikerlarni "
            "aylantirib bo'lmaydi. Uni 'apt install ffmpeg' (Linux), 'brew install "
            "ffmpeg' (Mac) orqali o'rnating, yoki Windows uchun build'ni PATH'ga qo'shing."
        ),
        "video_convert_empty_file": "Bu fayl bo'sh holda keldi — uni qayta yuborishga urinib ko'ring.",
        "video_convert_too_big": (
            "Bu klipni Telegram'ning 256 KB chegarasiga sig'dirib bo'lmadi ({note}). Qisqaroq yoki soddaroq klip yuboring."
        ),
        "import_pack_not_found": (
            "\"{source}\" nomli ochiq to'plam topilmadi."
        ),
        "import_bad_zip": "Bu haqiqiy .zip/.wastickers fayliga o'xshamayapti.",
        "import_zip_no_images": "Bu zip ichida ishlatsa bo'ladigan rasm topilmadi.",
        # 2.2.0: per-sticker replies, emoji by reply, public packs, group chats.
        "status_line_open": '📝 "{title}" {verb}',
        "sticker_added_to": "✅ “{title}” to'plamiga #{n} sifatida {emoji} bilan qo'shildi. Emojini o'zgartirish uchun shu xabarga emoji bilan javob bering.",
        "emoji_target_unknown": "Bu qaysi stiker ekanini endi aniqlay olmayman. Uning o'rniga stikerning o'ziga javob bering.",
        "emoji_not_ours": "Bu stiker shu bot yaratgan to'plamdan emas, shuning uchun uni o'zgartira olmayman.",
        "emoji_usage": "Stikerga yoki mening “Qo'shildi” xabarimga /emoji va emoji bilan javob bering — masalan, /emoji 😎",
        "emoji_send_reply": "O'sha stiker uchun yangi emoji bilan shu xabarga javob bering.",
        "already_in_pack": "Bu stiker “{title}” to'plamida allaqachon bor. Yana qo'shaymi?",
        "already_in_pack_owner": "Bu stiker “{title}” to'plamida allaqachon bor. Yana qo'shaymi yoki to'plamdan olib tashlaymi?",
        "already_in_pack_group": "Bu stiker “{title}” to'plamida allaqachon bor. Yana qo'shaymi?",
        "btn_add_again": "➕ Yana qo'shish",
        "btn_dont_add": "✖ Qo'shmaslik",
        "btn_remove_it": '🗑 Olib tashlash',
        "remove_not_allowed": "Stikerlarni faqat to'plam egasi o'chira oladi.",
        "dup_not_added": "Mayli, qo'shilmadi.",
        "dup_expired": "Bu savolning muddati o'tgan. Stikerni qaytadan yuboring.",
        "dup_not_yours": 'Faqat uni yuborgan kishi javob bera oladi.',
        "btn_pack_private": '🔒 Shaxsiy · guruhlarda ochiq qilish uchun bosing',
        "btn_pack_public": '🌐 Guruhlarda ochiq · shaxsiy qilish uchun bosing',
        "pack_now_public": "🌐 “{title}” endi ochiq: men ishlatiladigan guruhlarda har kim unga qo'sha oladi.",
        "pack_now_private": "🔒 “{title}” endi shaxsiy. Unga faqat siz va hammualliflar qo'sha oladi.",
        "group_hello": "👋 Salom! Men stiker to'plamlarini yasayman.\n\n• /packs — bu yerda qo'sha oladigan to'plamlar\n• rasm, GIF, video yoki stikerga /add va /packs dagi raqam bilan javob bering\n• emojisini o'zgartirish uchun stikerga /emoji 😎 bilan javob bering\n\nShaxsiy to'plamga qo'shishga ruxsat berish uchun uning hammualliflik havolasini shu yerga yuboring.",
        "group_packs_head": "📦 Bu yerda qo'sha oladigan to'plamlar:",
        "group_packs_tail": 'Rasmga /add va raqam bilan javob bering.',
        "group_packs_none": "Bu yerda hali ochiq to'plam yo'q. To'plam egasi uni mening chatimda ochiq qilishi (/mypacks → to'plam → 🔒) yoki hammualliflik havolasini shu yerga yuborishi mumkin.",
        "group_add_need_reply": 'Rasm, GIF, video yoki stikerga /add bilan javob bering.',
        "group_add_which": "Qaysi to'plam? /add va /packs dagi raqami yoki havolasi.",
        "group_pack_unknown": "Bu to'plamni bilmayman. /packs dagi raqamni yoki to'plam havolasini yuboring.",
        "group_no_access": "🔒 “{title}” to'plamiga ruxsatingiz yo'q. Egasi uni ochiq qilishi yoki hammualliflik havolasini shu yerga yuborishi mumkin.",
        "group_adding": "⏳ Qo'shilmoqda...",
        "helppage_groups": "👥 Guruhlarda\n\nMeni guruhga qo'shing, keyin:\n• /packs u yerda qo'sha oladigan to'plamlarni raqamlab chiqaradi.\n• Rasm, GIF, video yoki stikerga /add 2 (raqam) yoki /add va to'plam havolasi bilan javob bering. Faqat /add — o'sha yerda oxirgi qo'shgan to'plamingizga qo'shadi. To'plamda allaqachon bor stikerga /add yozsangiz, uni yana qo'shish-qo'shmaslikni so'rayman: guruhda hech narsa o'chirilmaydi.\n• Stiker emojisini o'zgartirish uchun unga /emoji 😎 bilan javob bering; mening “Qo'shildi” xabarimga faqat emojining o'zi yetadi. (Guruhda men faqat buyruqlarga va o'z xabarlarimga berilgan javoblarga munosabat bildiraman.) Mening chatimda to'plamlaringizdagi istalgan stikerga emojining o'zi bilan javob berish ham yetadi.\n\nKim qo'sha oladi: to'plam egasi va hammualliflari — hamma joyda. Ochiq to'plamlarga (/mypacks → to'plam → 🔒/🌐) egasi yoki hammuallifi meni ishlatadigan guruhdagi har kim qo'sha oladi. Shaxsiy to'plamlar (odatiy holat) ro'yxatda ko'rinmaydi; odamlarga ruxsat berish uchun hammualliflik havolasini guruhga yuboring.",
        "help_btn_groups": '👥 Guruhlarda',
        # 2.2.0: /done optional; co-edit link limits and invitations.
        "pack_created_link": "📦 To'plamingiz tayyor: https://t.me/addstickers/{pack_name}",
        "coedit_link_expired": "Bu hammualliflik havolasining muddati tugagan. To'plam egasidan yangisini so'rang.",
        "coedit_link_full": "Bu to'plamda egasi ruxsat bergan miqdordagi hammualliflar allaqachon bor. Egasidan chegarani oshirishni so'rang.",
        "coedit_removed_access": '🔒 Endi "{title}" to\'plamiga qo\'sha olmaysiz: egasi sizni olib tashladi.',
        "invites_claimed": "👥 Sizni {titles} to'plamiga qo'shishga taklif qilishdi. Ularni /mypacks da topasiz.",
        "your_packs_shared_note": "👥 — boshqalar siz bilan ulashgan to'plamlar.",
        "co_period_1": '1 kun',
        "co_period_7": '1 hafta',
        "co_period_30": '1 oy',
        "co_period_none": 'cheklovsiz',
        "co_valid_forever": 'Amal qiladi: muddatsiz',
        "co_valid_until": '{date} gacha amal qiladi ({left} qoldi)',
        "co_valid_expired": "⚠️ Muddati {date} da tugagan: yangi odam qo'shila olmaydi. Qayta ochish uchun ⏳ ni bosing yoki yangi havola yarating.",
        "co_left_days": '{n} kun',
        "co_left_hours": '{n} soat',
        "co_invited_count": ' (+{n} ta @username orqali taklif qilingan)',
        "btn_co_valid": '⏳ Havola muddati: {period}',
        "btn_co_max": "👥 Ko'pi bilan hammualliflar: {n}",
        "btn_co_invite": "➕ @username orqali qo'shish",
        "btn_co_list": "📋 Kim qo'sha oladi ({n})",
        "co_valid_set": 'Havola endi {period} amal qiladi.',
        "co_max_set": "Ko'pi bilan {n} ta hammuallif.",
        "btn_co_delete_off": "🗑 Hammualliflar o'chira oladi: yo'q",
        "btn_co_delete_on": "🗑 Hammualliflar o'chira oladi: ha",
        "co_delete_set_on": "Endi hammualliflar stikerlarni o'chira oladi (oxirgisidan tashqari).",
        "co_delete_set_off": "Endi stikerlarni faqat siz o'chira olasiz.",
        "co_removed": 'Olib tashlandi.',
        "co_list_heading": '📋 "{title}" ga kim qo\'sha oladi:',
        "co_list_invited": '• @{username} — taklif qilingan, hali botni ishlatmagan',
        "co_list_more": '…va yana {n} ta.',
        "co_list_empty": "Hali hech kim yo'q.",
        "co_list_remove_hint": "Kimnidir chiqarish uchun ✖ ni bosing. Ular qo'shgan stikerlar qoladi.",
        "invite_unavailable": "@username orqali qo'shish hozir ishlamayapti. Keyinroq urinib ko'ring yoki havoladan foydalaning.",
        "invite_prompt": "\"{title}\" to'plamiga qo'sha oladigan odamlarning @username'larini bo'sh joy bilan ajratib yuboring. Ishlatilmagan takliflar 30 kundan keyin o'chadi. To'xtatish: /cancel",
        "invite_done": '✅ Taklif qilindi: {names}.',
        "invite_full": "Taklif qilinmadi, to'plamda {max} tadan ortiq hammuallif bo'lib qoladi: {names}. Avval 👥 bilan chegarani oshiring.",
        "invite_bad": 'Username emas: {names}',
        "cancel_item_invite": '"{title}" uchun @username\'lar',
        "cancel_button_invite": '👥 Odamlarni taklif qilish',
    },
    "ru": {
        "whomade_someone": "человек, который не раскрывает своё имя",
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
            "Делает стикерпаки из картинок, GIF и видео."
        ),
        "bot_description": (
            "Пришлите картинку, GIF или видео — бот соберёт из этого стикерпак, который принадлежит "
            "вам.\n"
            "\n"
            "Дайте ссылку, чтобы паком мог пополнять кто-то ещё, или перенесите стикеры из другого "
            "пака Telegram либо из архива WhatsApp.\n"
            "\n"
            "Английский, узбекский и русский. /privacy — что бот о вас хранит."
        ),
        # ---- shared policy keys (/privacy, /terms, /deletemydata) ----
        "privacy_heading": "🔒 Конфиденциальность",
        "privacy_kept_heading": "Что бот хранит:",
        "privacy_stored": "• ваш Telegram ID и язык\n• созданные вами наборы: имя, название и ваше имя и username на тот момент\n• кто может добавлять в ваши наборы, ваши ссылки для соавторов с их ограничениями и приглашённые @username (в зашифрованном виде; пока человек не воспользуется ботом, максимум 30 дней)\n• какие наборы открыты и что вы пользовались ботом в группе — чтобы /packs там их показывал: 90 дней с последнего использования там или пока вы не выйдете из группы\n• время каждого использования бота — чтобы считать пользователей\n• дневное число нажатий кнопок по всему боту, не по людям\n• пожертвования: сумма и ID платежа в Telegram\n• то, что бот сейчас делает с вами, пока это не закончится",
        "privacy_problems_heading": "Когда что-то ломается:",
        "privacy_problems": "Каждая ошибка записывается без вас: код, номер инцидента, время и версия бота.\n\n«Добавить мои данные» под ошибкой прикрепляет к этому инциденту ваш Telegram ID, @username, язык и тип чата (личный или группа) — только если нажать «Отправить». Сообщения и файлы — никогда. Это стирается через 30 дней или сразу через /deletemydata. Записи об ошибках удаляются через 180 дней.",
        "privacy_seen_by_heading": "Кто ещё это видит:",
        "privacy_seen_by": (
            "• Telegram, через который идут все сообщения\n• хостинг-провайдер и база данных бота"
        ),
        "privacy_others": (
            "• больше никто — стикеры делаются на сервере бота, внешние сервисы не используются"
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
            "Стикеры: загружайте только то, на что у вас есть права, и ничего запрещённого правилами Telegram. Созданный набор живёт в Telegram — бот может его забыть, но удалить его там может только владелец."
        ),
        "terms_money": "Деньги: /donate добровольный и помогает оплачивать хостинг. Stars также дают ⚡ кредит для ConvertBot: 2 ⚡ за ⭐, или 6 за первые 500 Stars и 4 за следующие 500; всё сверх 2 — бонус, который сгорает через 90 дней после оплаты. Платежи окончательны: Stars НЕ возвращаются, кредит НЕЛЬЗЯ вывести — кроме платежа, который не удалось превратить в кредит, он возвращается автоматически. Платежи проводит Telegram; бот не видит номер карты. Проблемы: /paysupport",
        "terms_no_warranty": (
            "Без гарантий: бота бесплатно ведёт один человек, он может тормозить, ошибаться или отключиться без предупреждения. Храните копии важного у себя."
        ),
        "policy_full_text": "Полный текст: {url}",
        "policy_contact": "Вопросы, жалобы или запрос по данным: {contact}",
        "delete_data_confirm": "⚠️ Это сотрёт всё, что бот хранит о вас. Отменить будет нельзя.",
        "delete_data_consequences": "Бот забудет ваши наборы: перестанет их показывать и не сможет в них добавлять. Сами наборы продолжат работать в Telegram — удалите их там, если нужно. Язык, ссылки для соавторов и доступ других к вашим наборам тоже удалятся.\n\nПлатёжные записи останутся без вашего username — на случай споров об оплате. ⚡ баланс тоже останется.",
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
        "help_brief": "Как это устроено:\n\n• /newpack создаёт набор стикеров: отправляйте картинки, GIF, видео или стикеры, после каждого можно эмодзи.\n• /addsticker добавляет в ваш набор; /mypacks показывает их.\n• /import копирует стикеры из другого набора Telegram или экспорта WhatsApp.\n• Идеи или проблемы: /feedback\n\nОсновные команды: /newpack · /addsticker · /mypacks · /cancel\nВсе команды: полное руководство, ниже или /fullhelp.\n\n",
        "help_full_button": "📖 Полное руководство",
        "help_brief_button": "↩️ Кратко",
        "help_page_everything": "Полное руководство: /fullhelp",
        "help_page_gone": "Этой страницы справки больше нет. Отправьте /help.",
        "helppage_newpack": '🆕 /newpack — новый набор стикеров\n\n1. Отправьте /newpack, затем название набора сообщением (до {title_max} символов; длиннее — обрежу). Если Telegram не примет название, начните его с буквы.\n2. Присылайте то, что станет стикерами, по одному в сообщении: фото или картинки файлом, GIF, видео или стикеры. Набор создаётся с первым стикером — нет стикера, нет набора.\n3. Каждый стикер получает {emoji}. Чтобы сменить, сразу после него пришлите эмодзи (до {emoji_max}): они заменят эмодзи последнего стикера.\n4. Это всё: каждый стикер сохраняется сразу, а в ответе на первый — ссылка на набор. /cancel — прервать.\n\nВо что что превращается:\n• Фото, картинка файлом или обычный стикер — обычный стикер, {side} px по длинной стороне.\n• GIF, видео или видеостикер — видеостикер: {side} px, первые {seconds} с, {fps} кадров в секунду, без звука, меньше {kb} КБ. Если так ужать не выйдет, я скажу и предложу наш бот-конвертер.\n• Анимированные стикеры (.tgs) добавить нельзя.\n• Много сразу: набор стикеров WhatsApp (.zip или .wastickers) или /import из набора Telegram.\n\nПример: /newpack, потом «Кошачьи морды», потом три фото вашей кошки, потом 😺.',
        "helppage_addsticker": '➕ /addsticker — добавить стикеры в ваш набор\n\n1. /addsticker покажет ваши наборы. Нажмите нужный, затем «➕ Добавить стикеры».\n2. Присылайте фото, GIF, видео или стикеры — так же, как для нового набора. Каждый получает {emoji}; эмодзи, присланные сразу после, его заменят.\n• Если прислать стикер, который уже есть в наборе, я спрошу, добавить его ещё раз или убрать. Если он последний, спрошу ещё раз — его удаление удалит весь набор. В наборе, которым с вами поделились, убрать предложу, только если владелец разрешил соавторам удалять.\n• В списке только ваши наборы, созданные через этого бота. Ещё нет? /newpack.\n• /mypacks показывает тот же список. В меню набора его можно ещё переименовать, дать кому-то редактировать вместе с вами или удалить.\n\nПример: /addsticker, нажмите «Кошачьи морды», затем «➕ Добавить стикеры», пришлите новое фото.',
        "helppage_import": '📥 /import — много стикеров сразу из другого набора\n\nРаботает, пока вы наполняете набор: после /newpack и названия или после «➕ Добавить стикеры».\n• /import и ссылка на открытый набор Telegram (t.me/addstickers/…) или его имя копирует его стикеры в ваш набор вместе с эмодзи. До {max} за раз.\n• Анимированные стикеры (.tgs) пропускаются, и я скажу, сколько.\n• Из WhatsApp: вместо этого пришлите файл набора .zip или .wastickers.\n• Дальше присылайте ещё, когда захотите.\n\nПример: /newpack, потом «Избранное», потом /import t.me/addstickers/SomePack',
        "privacy_feedback_heading": "Если вы отправляете /feedback:",
        "privacy_feedback": (
            "Оно приходит тому, кто ведёт ботов, с названием бота и вашим языком — без имени, username и ID. Ответить он не может. Копия хранится 30 дней."
        ),
        "cancel_item_new_pack": "\u043d\u043e\u0432\u044b\u0439 \u043d\u0430\u0431\u043e\u0440, \u043a\u043e\u0442\u043e\u0440\u043e\u043c\u0443 \u0432\u044b \u0434\u0430\u0432\u0430\u043b\u0438 \u043d\u0430\u0437\u0432\u0430\u043d\u0438\u0435",
        "cancel_button_new_pack": "🆕 Название нового набора",
        "cancel_item_rename": "\u043f\u0435\u0440\u0435\u0438\u043c\u0435\u043d\u043e\u0432\u0430\u043d\u0438\u0435 \u00ab{title}\u00bb",
        "cancel_button_rename": "✏️ Переименование набора",
        "cancel_item_editing": "\u0440\u0435\u0434\u0430\u043a\u0442\u0438\u0440\u043e\u0432\u0430\u043d\u0438\u0435 \u00ab{title}\u00bb",
        "cancel_button_editing": "📦 Редактирование набора",
        "start_intro": (
            "Здравствуйте! Я превращаю ваши изображения, GIF, видео и стикеры в наборы "
            "стикеров Telegram.\n\n"
        ),
        "help_text": "Команды:\n/newpack - новый набор стикеров\n/addsticker - добавить стикеры в ваш набор\n/mypacks - ваши наборы: переименовать, поделиться, удалить\n/import <ссылка/имя набора> - (во время редактирования) скопировать стикеры из другого набора Telegram или отправить файл WhatsApp .zip/.wastickers\n/done - закрыть заполняемый набор (необязательно)\n/cancel - остановить то, чего я жду\n/feedback - написать тому, кто ведёт ботов (анонимно)\n/whomade <ссылка/имя набора> - кто создал набор (если через этого бота)\n/donate - помочь с хостингом (необязательно)\n/balance - ваш ⚡ кредит\n/paysupport - проблема с оплатой\n/en, /uz, /rus - сменить язык (или /language)\n/privacy - что я храню о вас\n/terms - для чего можно использовать бота\n/deletemydata - удалить всё, что я о вас храню\n/start - начать сначала\n/fullhelp - это руководство (/help - краткое)\n/convert - конвертация файлов в ConvertBot\n\nВо время редактирования набора: отправляйте картинки, GIF, видео или стикеры, чтобы добавить их, и эмодзи сразу после, чтобы его поменять.\n\n⚠️ Платежи окончательны — Stars НЕ возвращаются, ⚡ кредит НЕЛЬЗЯ вывести.\n\n",
        "whomade_usage": "Использование: /whomade <имя набора или ссылка t.me/addstickers>",
        "whomade_not_found": (
            "Об этом наборе нет записи — он создан не через этого бота, или имя неверное."
        ),
        "whomade_result": "📦 «{title}»\nСоздал(а) {creator} {date} (через этого бота).",
        "coedit_link_invalid": "Эта ссылка для совместного редактирования недействительна — возможно, владелец набора сбросил её.",
        "coedit_pack_gone": "Похоже, этого набора больше не существует.",
        "coedit_own_pack": "Это ваш собственный набор — используйте /mypacks, чтобы им управлять.",
        "coedit_joined_intro": "Теперь вы можете добавлять в «{title}»! Отправляйте картинки, GIF, видео или стикеры. Каждому ставится 😭; чтобы поменять, сразу отправьте эмодзи. Набор — в /mypacks.",
        "btn_new_pack": "➕ Новый набор",
        "btn_my_packs": "📁 Мои наборы",
        "btn_help": "❓ Помощь",
        "btn_back": "⬅️ Назад",
        "no_packs_yet": "Пока нет наборов — нажмите «Новый набор» или используйте /newpack.",
        "your_packs": "Ваши наборы:",
        "not_your_pack": "Это не ваш набор.",
        "pack_detail_title": "📦 {title}",
        "btn_open_pack": "🔗 Открыть набор",
        "btn_add_stickers": "➕ Добавить стикеры",
        "btn_rename": "✏️ Переименовать",
        "btn_coedit": "👥 Совместное редактирование",
        "btn_delete_pack": "🗑️ Удалить набор",
        "coedit_message": "👥 Совместное редактирование «{title}»\n\nСсылка: {link}\n{valid_line}\nСоавторы: {editors} из {max}{invited}\n\nКто откроет ссылку, сможет добавлять стикеры в этот набор. Новая ссылка отключает старую.",
        "btn_reset_link": "🔄 Сбросить ссылку",
        "only_owner_coedit": "Совместным редактированием может управлять только владелец набора.",
        "link_reset_confirm": "Ссылка сброшена — старая больше не работает.",
        "only_owner_rename": "Переименовать набор может только его владелец.",
        "rename_prompt": "Отправьте новое название для «{title}».",
        "rename_broken_state": "Что-то пошло не так — попробуйте снова переименовать через /mypacks.",
        "btn_back_to_pack": "⬅️ Назад к набору",
        "renamed_success": "Переименовано в «{title}».",
        "renamed_failed": "Не удалось переименовать: {error}",
        "only_owner_delete": "Удалить набор может только его владелец.",
        "btn_delete": "🗑️ Удалить",
        "btn_cancel_inline": "⬅️ Отмена",
        "delete_confirm1": (
            "⚠️ Удалить «{title}»? Это удалит набор из Telegram у всех, у кого он "
            "есть, включая соредакторов, и отменить это будет нельзя."
        ),
        "btn_delete_confirm": "🗑️ Да, удалить навсегда",
        "delete_confirm2": "❗ Последняя проверка — удалить «{title}» навсегда? После этого отменить будет нельзя.",
        "delete_failed": "⚠️ Не удалось удалить: {error}",
        "btn_my_packs_back": "⬅️ Мои наборы",
        "delete_success": "🗑️ «{title}» удалён(а) навсегда.",
        "newpack_title_prompt": "Каким будет название набора?",
        "title_empty": "Это пусто — отправьте настоящее название для набора.",
        "title_truncated": "Telegram ограничивает название набора 64 символами — используется «{title}».",
        "editing_intro_new": "Отправляйте картинки, GIF, видео или стикеры. Каждому ставится 😭; чтобы поменять, сразу отправьте эмодзи.",
        "no_packs_for_add": "У вас пока нет наборов. Сначала используйте /newpack.",
        "pick_pack_prompt": "Какой набор? Нажмите на него, затем «➕ Добавить стикеры».",
        "editing_intro_add": "Отправляйте картинки, GIF, видео или стикеры, чтобы добавить. Каждому ставится 😭; чтобы поменять, сразу отправьте эмодзи.",
        "editing_intro_shared": "Отправляйте картинки, GIF, видео или стикеры, чтобы добавить. Каждому ставится 😭; чтобы поменять, сразу отправьте эмодзи.",
        "status_verb_creating": "Создание",
        "status_verb_editing": "Редактирование",
        "status_default_title": "этот набор",
        "btn_delete_pack_yes": "🗑️ Да, удалить набор",
        "btn_cancel": "Отмена",
        "remove_last_confirm": (
            "Это последний стикер — его удаление удалит *весь набор*. Уверены?"
        ),
        "remove_failed": "⚠️ Не удалось удалить этот стикер: {error}",
        "remove_success": "🗑️ Убрано из набора.",
        "remove_last_owner_only": "Это последний стикер. Убрать его может только владелец набора, так как это удалит набор.",
        "keep_pack": "Хорошо, набор оставлен.",
        "pack_deleted_empty": "🗑️ Набор удалён (в нём не осталось стикеров).",
        "pack_deleted_note": "❌ Набор удалён.",
        "image_process_failed": "Не удалось обработать это изображение: {error}",
        "last_attempt_failed": "⚠️ Последняя попытка не удалась — отправьте другой файл, чтобы попробовать снова, или /cancel.",
        "converting_video": "Преобразование в видео-стикер...",
        "video_convert_failed_redirect": (
            "{error}\n\nНужен обычный файл? Отправьте его в @ConvertBot 👇"
        ),
        "video_convert_generic_failed": "Не удалось это преобразовать: {error}",
        "animated_not_supported": (
            "Анимированные (.tgs) стикеры не поддерживаются — отправьте картинку, GIF, видео или видеостикер."
        ),
        "import_usage": (
            "Отправьте /import и ссылку или имя набора Telegram, или файл WhatsApp .zip/.wastickers."
        ),
        "import_invalid_source": "Это не похоже на настоящее имя набора или ссылку t.me/addstickers.",
        "import_fetching": "Загрузка стикеров из «{source}»...",
        "import_summary_head": "Импортировано {added} стикер(ов) из «{source}»",
        "import_summary_skipped": ", пропущено {skipped} неподдерживаемых (анимированные/Lottie)",
        "import_summary_failed": ", {failed} не удалось",
        "import_summary_tail": '. Присылайте ещё, когда захотите.',
        "done_standalone_hint": (
            "Вы сейчас не редактируете набор. Начните с /newpack или откройте его в /mypacks."
        ),
        "import_standalone_hint": (
            "Сначала откройте набор (/newpack или /mypacks), потом отправьте /import <ссылка>."
        ),
        "whatsapp_reading": "Чтение набора стикеров WhatsApp...",
        "whatsapp_summary_head": "Импортировано {added} стикер(ов) из набора WhatsApp",
        "no_sticker_to_tag": "Сначала добавьте стикер, затем отправьте эмодзи, чтобы его пометить.",
        "retagged_success": "Переметено как {emojis}.",
        "retag_failed": "Не удалось обновить эмодзи: {error}",
        "nothing_added_yet": "Вы ещё ничего не добавили. Сначала отправьте изображение.",
        "done_success": (
            "✅ «{title}» готов — добавлено стикеров: {count}.\n\nhttps://t.me/addstickers/{pack_name}"
        ),
        "convert_redirect": (
            "Конвертация файлов — в @ConvertBot, нажмите ниже."
        ),
        "cancelled_status_note": "❌ Отменено.",
        "unrecognized": "Не понял, для чего это — попробуйте /newpack, /mypacks или /help.",
        "unknown_command": "Я не знаю такую команду. Отправьте /help, чтобы увидеть, что я умею.",
        "err_invalid_name": (
            "⚠️ Telegram не принял это название. Отправьте /cancel, затем /newpack с названием, которое начинается с буквы."
        ),
        "err_name_occupied": (
            "⚠️ В Telegram совпало имя (редко). Отправьте /cancel, затем снова /newpack."
        ),
        "err_too_many_stickers": "⚠️ Этот набор уже достиг лимита Telegram по стикерам (120) — вместо этого начните новый набор через /newpack.",
        "err_bad_format": "⚠️ Telegram не принял формат этого файла для этого набора — попробуйте другое изображение.",
        "err_generic": "⚠️ Telegram отклонил это: {msg}\n\nВы можете попробовать снова, или /cancel, чтобы остановиться.",
        "err_timed_out": (
            "⚠️ Telegram не подтвердил вовремя, но это могло сработать — проверьте набор перед повтором. Или /cancel."
        ),
        "restarting_send_again": "🔄 Меня обновляют — отправьте это снова через несколько секунд.",
        "update_soon_try_later": "🔧 Меня сейчас обновят — попробуйте снова примерно через {minutes} мин. Я напишу, когда вернусь.",
        "update_soon_try_later_soon": "🔧 Меня обновляют — попробуйте чуть позже. Я напишу, когда вернусь.",
        "update_will_reset": "🔧 Меня сейчас обновят, и то, что вы делаете, сбросится. Начать заново можно через несколько минут.",
        "update_done_try_now": "✅ Обновление готово — попробуйте снова.",
        "gift_granted": "🎁 Нас уже больше 100 — спасибо! Вам {amount} ⚡ бесплатного кредита до {date}: это около {conversions} платных конвертаций в {convert} или больших загрузок в {download}. /balance",
        "video_convert_ffmpeg_missing": (
            "На этом сервере не установлен ffmpeg, поэтому GIF/видео-стикеры нельзя "
            "преобразовать. Установите его через 'apt install ffmpeg' (Linux), "
            "'brew install ffmpeg' (Mac), либо добавьте сборку для Windows в PATH."
        ),
        "video_convert_empty_file": "Этот файл пришёл пустым — попробуйте отправить его ещё раз.",
        "video_convert_too_big": (
            "Не удалось уложить клип в лимит Telegram 256 КБ ({note}). Попробуйте клип короче или проще."
        ),
        "import_pack_not_found": (
            "Не нашёл открытый набор «{source}»."
        ),
        "import_bad_zip": "Это не похоже на настоящий файл .zip/.wastickers.",
        "import_zip_no_images": "Внутри этого zip-файла не найдено пригодных изображений.",
        # 2.2.0: per-sticker replies, emoji by reply, public packs, group chats.
        "status_line_open": '📝 {verb} «{title}»',
        "sticker_added_to": '✅ Добавлено в «{title}» как №{n} с {emoji}. Ответьте на это сообщение эмодзи, чтобы его поменять.',
        "emoji_target_unknown": 'Я уже не могу понять, какой это стикер. Ответьте лучше на сам стикер.',
        "emoji_not_ours": 'Этот стикер не из пака, созданного этим ботом, поэтому изменить его я не могу.',
        "emoji_usage": 'Ответьте на стикер или на моё сообщение «Добавлено» командой /emoji с эмодзи — например, /emoji 😎',
        "emoji_send_reply": 'Ответьте на это сообщение новым эмодзи для этого стикера.',
        "already_in_pack": "Этот стикер уже есть в «{title}». Добавить его ещё раз?",
        "already_in_pack_owner": 'Этот стикер уже есть в «{title}». Добавить его ещё раз или убрать из набора?',
        "already_in_pack_group": "Этот стикер уже есть в «{title}». Добавить его ещё раз?",
        "btn_add_again": '➕ Добавить ещё раз',
        "btn_dont_add": '✖ Не добавлять',
        "btn_remove_it": '🗑 Убрать',
        "remove_not_allowed": "Удалять стикеры может только владелец набора.",
        "dup_not_added": 'Хорошо, не добавляю.',
        "dup_expired": 'Этот вопрос устарел. Пришлите стикер ещё раз.',
        "dup_not_yours": 'Ответить может только тот, кто его прислал.',
        "btn_pack_private": '🔒 Личный · нажмите, чтобы открыть в группах',
        "btn_pack_public": '🌐 Открыт в группах · нажмите, чтобы сделать личным',
        "pack_now_public": "🌐 «{title}» теперь открыт: в группах, где вы мной пользуетесь, в него может добавлять любой.",
        "pack_now_private": '🔒 «{title}» теперь личный. Добавлять в него можете только вы и соавторы.',
        "group_hello": "👋 Привет! Я делаю стикерпаки.\n\n• /packs — паки, в которые можно добавлять здесь\n• ответьте на картинку, GIF, видео или стикер командой /add и номером из /packs\n• ответьте на стикер командой /emoji 😎, чтобы поменять его эмодзи\n\nЧтобы пустить людей в личный пак, отправьте сюда его ссылку для соавторов.",
        "group_packs_head": '📦 Паки, в которые можно добавлять здесь:',
        "group_packs_tail": 'Ответьте на картинку командой /add и номером.',
        "group_packs_none": "Здесь пока нет открытых паков. Владелец может открыть пак у меня в чате (/mypacks → пак → 🔒) или отправить сюда ссылку для соавторов.",
        "group_add_need_reply": 'Ответьте командой /add на картинку, GIF, видео или стикер.',
        "group_add_which": 'В какой пак? /add и его номер из /packs или ссылка.',
        "group_pack_unknown": "Я не знаю этот пак. Укажите номер из /packs или ссылку на пак.",
        "group_no_access": '🔒 У вас нет доступа к «{title}». Владелец может открыть пак или отправить сюда ссылку для соавторов.',
        "group_adding": '⏳ Добавляю...',
        "helppage_groups": '👥 В группах\n\nДобавьте меня в группу, затем:\n• /packs показывает пронумерованные паки, в которые там можно добавлять.\n• Ответьте на картинку, GIF, видео или стикер командой /add 2 (номер) или /add со ссылкой на пак. Просто /add — в пак, в который вы добавляли там в последний раз. /add на стикер, который уже есть в паке, спросит, добавить ли его ещё раз: в группе ничего не удаляется.\n• Чтобы поменять эмодзи стикера, ответьте на него командой /emoji 😎; на моё «Добавлено» достаточно ответить самим эмодзи. (В группе я реагирую только на команды и на ответы на мои сообщения.) У меня в чате хватит и одного эмодзи в ответ на любой стикер ваших паков.\n\nКто может добавлять: владелец пака и соавторы — везде. В открытые паки (/mypacks → пак → 🔒/🌐) может добавлять любой участник группы, где мной пользуется владелец или соавтор. Личные паки (по умолчанию) в списке не видны; чтобы пустить людей, отправьте в группу ссылку для соавторов.',
        "help_btn_groups": '👥 В группах',
        # 2.2.0: /done optional; co-edit link limits and invitations.
        "pack_created_link": '📦 Набор создан: https://t.me/addstickers/{pack_name}',
        "coedit_link_expired": 'Срок этой ссылки для соавторов истёк. Попросите у владельца набора новую.',
        "coedit_link_full": 'В этом наборе уже столько соавторов, сколько разрешил владелец. Попросите его поднять предел.',
        "coedit_removed_access": '🔒 Вы больше не можете добавлять в «{title}»: владелец убрал вас.',
        "invites_claimed": '👥 Вас пригласили добавлять в {titles}. Они в /mypacks.',
        "your_packs_shared_note": '👥 — наборы, которыми с вами поделились.',
        "co_period_1": '1 день',
        "co_period_7": '1 неделя',
        "co_period_30": '1 месяц',
        "co_period_none": 'без срока',
        "co_valid_forever": 'Действует: без срока',
        "co_valid_until": 'Действует до {date} (осталось {left})',
        "co_valid_expired": '⚠️ Истекла {date}: новые люди не присоединятся. Нажмите ⏳, чтобы открыть снова, или сделайте новую ссылку.',
        "co_left_days": '{n} дн.',
        "co_left_hours": '{n} ч',
        "co_invited_count": ' (+{n} приглашено по @username)',
        "btn_co_valid": '⏳ Ссылка действует: {period}',
        "btn_co_max": '👥 Максимум соавторов: {n}',
        "btn_co_invite": '➕ Добавить по @username',
        "btn_co_list": '📋 Кто может добавлять ({n})',
        "co_valid_set": 'Теперь ссылка действует: {period}.',
        "co_max_set": 'Не больше {n} соавторов.',
        "btn_co_delete_off": '🗑 Соавторы могут удалять: нет',
        "btn_co_delete_on": '🗑 Соавторы могут удалять: да',
        "co_delete_set_on": "Теперь соавторы могут удалять стикеры (кроме последнего).",
        "co_delete_set_off": 'Теперь удалять стикеры можете только вы.',
        "co_removed": 'Убрано.',
        "co_list_heading": '📋 Кто может добавлять в «{title}»:',
        "co_list_invited": '• @{username} — приглашён, ещё не заходил ко мне',
        "co_list_more": '…и ещё {n}.',
        "co_list_empty": 'Пока никого.',
        "co_list_remove_hint": 'Нажмите ✖, чтобы убрать человека. Добавленные им стикеры останутся.',
        "invite_unavailable": 'Добавление по @username сейчас недоступно. Попробуйте позже или используйте ссылку.',
        "invite_prompt": "Отправьте через пробел @username тех, кто может добавлять в «{title}». Неиспользованные приглашения пропадают через 30 дней. Остановить: /cancel",
        "invite_done": '✅ Приглашены: {names}.',
        "invite_full": 'Не приглашены — соавторов стало бы больше {max}: {names}. Сначала поднимите предел кнопкой 👥.',
        "invite_bad": 'Это не username: {names}',
        "cancel_item_invite": '@username для «{title}»',
        "cancel_button_invite": '👥 Приглашение людей',
    },
}


# Every problem a person can run into ends with its code, and the code is what
# puts a "Report the issue" button under it -- see problems.py. Imported here,
# below the tables, because it is pure data and nothing above needs it.
import problems  # noqa: E402

_BOT = "sticker_bot"


def t(lang: str | None, key: str, **kwargs) -> str:
    table = STRINGS.get(lang) or STRINGS["en"]
    template = table.get(key) or STRINGS["en"].get(key, key)
    text = template.format(**kwargs) if kwargs else template
    return text + problems.code_line(problems.code_for(_BOT, key))


async def get_lang(user_id: int, context) -> str:
    """Cached in context.user_data to avoid a DB round-trip on every handler
    call. Falls back to "en" for a user who hasn't chosen a language yet
    (only reachable outside /start's first-run gate, e.g. someone who sends
    a sticker before ever running /start)."""
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
        "newpack": "Yangi stiker to'plami yaratish",
        "addsticker": "To'plamingizga stiker qo'shish",
        "mypacks": "To'plamlaringiz",
        "import": "Boshqa to'plamdan stikerlarni ko'chirish",
        "done": "To'ldirilayotgan to'plamni yopish (ixtiyoriy)",
        "emoji": "Stikerga javob: emojisini o'zgartirish",
        "whomade": "To'plamni kim yaratgan — stikerini yuboring",
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
        "newpack": "Создать новый стикерпак",
        "addsticker": "Добавить стикеры в ваш пак",
        "mypacks": "Ваши паки",
        "import": "Скопировать стикеры из другого пака",
        "done": "Закрыть пак, который вы заполняете (необязательно)",
        "emoji": "Ответ на стикер: сменить эмодзи",
        "whomade": "Кто сделал пак — пришлите его стикер",
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

# The group-chat menu (group_mode.GROUP_COMMANDS), in the other two languages.
GROUP_COMMAND_MENU = {
    "uz": {"packs": "Bu yerda qo'sha oladigan to'plamlar", "add": "Rasmga javob: to'plamga qo'shish",
           "emoji": "Stikerga javob: emojisini o'zgartirish", "help": "Guruhda qanday ishlayman"},
    "ru": {"packs": "Паки, в которые можно добавлять здесь", "add": "Ответ на картинку: добавить в пак",
           "emoji": "Ответ на стикер: сменить эмодзи", "help": "Как я работаю в группе"},
}
