from __future__ import annotations
import atexit
import contextlib
import datetime
import io
import logging
import os
import re
import sys
from types import TracebackType
from typing import Iterable, Iterator
from python_utils import types
from python_utils.converters import scale_1024
from python_utils.terminal import get_terminal_size
from python_utils.time import epoch, format_time, timedelta_to_seconds
from progressbar import base, env, terminal
if types.TYPE_CHECKING:
    from .bar import ProgressBar, ProgressBarMixinBase
assert timedelta_to_seconds is not None
assert get_terminal_size is not None
assert format_time is not None
assert scale_1024 is not None
assert epoch is not None
StringT = types.TypeVar('StringT', bound=types.StringTypes)

def deltas_to_seconds(*deltas, default: types.Optional[types.Type[ValueError]]=ValueError) -> int | float | None:
    """
    Convert timedeltas and seconds as int to seconds as float while coalescing.

    >>> deltas_to_seconds(datetime.timedelta(seconds=1, milliseconds=234))
    1.234
    >>> deltas_to_seconds(123)
    123.0
    >>> deltas_to_seconds(1.234)
    1.234
    >>> deltas_to_seconds(None, 1.234)
    1.234
    >>> deltas_to_seconds(0, 1.234)
    0.0
    >>> deltas_to_seconds()
    Traceback (most recent call last):
    ...
    ValueError: No valid deltas passed to `deltas_to_seconds`
    >>> deltas_to_seconds(None)
    Traceback (most recent call last):
    ...
    ValueError: No valid deltas passed to `deltas_to_seconds`
    >>> deltas_to_seconds(default=0.0)
    0.0
    """
    for delta in deltas:
        if delta is None:
            continue
        if isinstance(delta, datetime.timedelta):
            return delta.total_seconds()
        if isinstance(delta, (int, float)):
            return float(delta)
    if isinstance(default, type) and issubclass(default, Exception):
        raise default("No valid deltas passed to `deltas_to_seconds`")
    return default

def no_color(value: StringT) -> StringT:
    """
    Return the `value` without ANSI escape codes.

    >>> no_color(b'\x1b[1234]abc')
    b'abc'
    >>> str(no_color(u'\x1b[1234]abc'))
    'abc'
    >>> str(no_color('\x1b[1234]abc'))
    'abc'
    >>> no_color(123)
    Traceback (most recent call last):
    ...
    TypeError: `value` must be a string or bytes, got 123
    """
    if isinstance(value, (str, bytes)):
        return re.sub(b'\x1b\\[[;\\d]*[A-Za-z]' if isinstance(value, bytes) else '\x1b\\[[;\\d]*[A-Za-z]', '', value)
    else:
        raise TypeError(f"`value` must be a string or bytes, got {type(value).__name__}")

def len_color(value: types.StringTypes) -> int:
    """
    Return the length of `value` without ANSI escape codes.

    >>> len_color(b'\x1b[1234]abc')
    3
    >>> len_color(u'\x1b[1234]abc')
    3
    >>> len_color('\x1b[1234]abc')
    3
    """
    return len(no_color(value))

class WrappingIO:
    buffer: io.StringIO
    target: base.IO
    capturing: bool
    listeners: set
    needs_clear: bool = False

    def __init__(self, target: base.IO, capturing: bool=False, listeners: types.Optional[types.Set[ProgressBar]]=None) -> None:
        self.buffer = io.StringIO()
        self.target = target
        self.capturing = capturing
        self.listeners = listeners or set()
        self.needs_clear = False

    def __enter__(self) -> WrappingIO:
        return self

    def __next__(self) -> str:
        return self.target.__next__()

    def __iter__(self) -> Iterator[str]:
        return self.target.__iter__()

    def __exit__(self, __t: type[BaseException] | None, __value: BaseException | None, __traceback: TracebackType | None) -> None:
        self.close()

class StreamWrapper:
    """Wrap stdout and stderr globally."""
    stdout: base.TextIO | WrappingIO
    stderr: base.TextIO | WrappingIO
    original_excepthook: types.Callable[[types.Type[BaseException], BaseException, TracebackType | None], None]
    wrapped_stdout: int = 0
    wrapped_stderr: int = 0
    wrapped_excepthook: int = 0
    capturing: int = 0
    listeners: set

    def __init__(self):
        self.stdout = self.original_stdout = sys.stdout
        self.stderr = self.original_stderr = sys.stderr
        self.original_excepthook = sys.excepthook
        self.wrapped_stdout = 0
        self.wrapped_stderr = 0
        self.wrapped_excepthook = 0
        self.capturing = 0
        self.listeners = set()
        if env.env_flag('WRAP_STDOUT', default=False):
            self.wrap_stdout()
        if env.env_flag('WRAP_STDERR', default=False):
            self.wrap_stderr()

class AttributeDict(dict):
    """
    A dict that can be accessed with .attribute.

    >>> attrs = AttributeDict(spam=123)

    # Reading

    >>> attrs['spam']
    123
    >>> attrs.spam
    123

    # Read after update using attribute

    >>> attrs.spam = 456
    >>> attrs['spam']
    456
    >>> attrs.spam
    456

    # Read after update using dict access

    >>> attrs['spam'] = 123
    >>> attrs['spam']
    123
    >>> attrs.spam
    123

    # Read after update using dict access

    >>> del attrs.spam
    >>> attrs['spam']
    Traceback (most recent call last):
    ...
    KeyError: 'spam'
    >>> attrs.spam
    Traceback (most recent call last):
    ...
    AttributeError: No such attribute: spam
    >>> del attrs.spam
    Traceback (most recent call last):
    ...
    AttributeError: No such attribute: spam
    """

    def __getattr__(self, name: str) -> int:
        if name in self:
            return self[name]
        else:
            raise AttributeError(f'No such attribute: {name}')

    def __setattr__(self, name: str, value: int) -> None:
        self[name] = value

    def __delattr__(self, name: str) -> None:
        if name in self:
            del self[name]
        else:
            raise AttributeError(f'No such attribute: {name}')
logger = logging.getLogger(__name__)
streams = StreamWrapper()
atexit.register(streams.flush)
