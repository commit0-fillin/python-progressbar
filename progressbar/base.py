from python_utils import types

class FalseMeta(type):

    @classmethod
    def __bool__(cls):
        return False

    @classmethod
    def __cmp__(cls, other):
        return -1  # Always return -1 to indicate that this class is "less than" any other object
    __nonzero__ = __bool__

class UnknownLength(metaclass=FalseMeta):
    pass

class Undefined(metaclass=FalseMeta):
    pass
try:
    IO = types.IO
    TextIO = types.TextIO
except AttributeError:
    from typing.io import IO, TextIO
assert IO is not None
assert TextIO is not None
