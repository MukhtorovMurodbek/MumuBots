def connect(*a, **k): raise RuntimeError("no database in tests")
class AsyncConnection:
    @staticmethod
    async def connect(*a, **k): raise RuntimeError("no database in tests")
class OperationalError(Exception): pass
class sql:
    @staticmethod
    def SQL(*a, **k): raise RuntimeError("stub")
    @staticmethod
    def Identifier(*a, **k): raise RuntimeError("stub")
