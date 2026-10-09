class _Any:
    def __init__(self, *a, **k): self.args, self.kwargs = a, k
class BotCommand(_Any):
    # The two fields the real class exposes, because publish_commands() reads
    # them back off a menu it was handed in order to translate it -- and a
    # stub that swallows them turns that into an AttributeError nobody would
    # see in production.
    @property
    def command(self): return self.kwargs.get("command", self.args[0] if self.args else None)

    @property
    def description(self):
        if "description" in self.kwargs:
            return self.kwargs["description"]
        return self.args[1] if len(self.args) > 1 else None


class BotCommandScopeChat(_Any): pass
class BotCommandScopeDefault(_Any): pass
class BotCommandScopeAllPrivateChats(_Any): pass
class BotCommandScopeAllGroupChats(_Any): pass
class BotCommandScopeAllChatAdministrators(_Any): pass
class InlineKeyboardButton(_Any): pass
class InlineKeyboardMarkup(_Any):
    # The one attribute of a keyboard a test has any reason to look at: the
    # rows it was built from. _Any keeps them in .args, which reads as
    # nothing; the real class calls them .inline_keyboard, and a test that
    # counts buttons should not have to know which of the two it is holding.
    @property
    def inline_keyboard(self): return self.args[0] if self.args else []
class LabeledPrice(_Any): pass
class ForceReply(_Any): pass
class LinkPreviewOptions(_Any): pass
class ReplyKeyboardRemove(_Any): pass
class TelegramObject: pass
class Update:
    MESSAGE = "message"; EDITED_MESSAGE = "edited_message"
    CALLBACK_QUERY = "callback_query"; PRE_CHECKOUT_QUERY = "pre_checkout_query"
    MESSAGE_REACTION = "message_reaction"

class InputSticker(_Any): pass
class InputMediaDocument(_Any): pass
class InputMediaPhoto(_Any): pass
class InputMediaVideo(_Any): pass
class InlineQueryResultArticle(_Any): pass
class InputFile(_Any): pass
class Message(_Any): pass
class Bot(_Any): pass
class Sticker(_Any): pass
class ChatAction: TYPING = "typing"
