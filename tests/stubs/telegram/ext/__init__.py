class _Any:
    def __init__(self, *a, **k): pass
class ApplicationBuilder(_Any): pass
class CallbackQueryHandler(_Any): pass
class CommandHandler(_Any): pass
class ContextTypes:
    DEFAULT_TYPE = object
class MessageHandler(_Any): pass
class MessageReactionHandler(_Any):
    MESSAGE_REACTION_UPDATED, MESSAGE_REACTION_COUNT_UPDATED, MESSAGE_REACTION = 1, 2, 3
class TypeHandler(_Any): pass
class ConversationHandler(_Any):
    END = -1
class PreCheckoutQueryHandler(_Any): pass
class ApplicationHandlerStop(Exception): pass
class BasePersistence:
    def __init__(self, store_data=None, update_interval=60):
        self.store_data, self.update_interval = store_data, update_interval
class PersistenceInput:
    def __init__(self, bot_data=True, chat_data=True, user_data=True, callback_data=True):
        self.bot_data, self.chat_data = bot_data, chat_data
        self.user_data, self.callback_data = user_data, callback_data
class filters:
    class _F:
        def __getattr__(self, name): return filters._F()
        def __and__(self, o): return self
        def __or__(self, o): return self
        def __invert__(self): return self
        def __call__(self, *a, **k): return self
    TEXT = _F(); COMMAND = _F(); PHOTO = _F(); ANIMATION = _F(); VIDEO = _F()
    SUCCESSFUL_PAYMENT = _F(); ATTACHMENT = _F(); AUDIO = _F(); VOICE = _F()
    ALL = _F(); REPLY = _F(); FORWARDED = _F(); Sticker = _F(); Document = _F()
    StatusUpdate = _F(); ChatType = _F(); UpdateType = _F()
    @staticmethod
    def Regex(*a, **k): return filters._F()
