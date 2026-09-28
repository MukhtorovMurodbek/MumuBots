class TelegramError(Exception): pass
class BadRequest(TelegramError): pass
class NetworkError(TelegramError): pass
class RetryAfter(TelegramError): pass
class TimedOut(NetworkError): pass

class Forbidden(TelegramError): pass
class Conflict(TelegramError): pass
class ChatMigrated(TelegramError): pass
class InvalidToken(TelegramError): pass
