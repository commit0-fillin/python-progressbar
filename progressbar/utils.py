from __future__ import absolute_import
import distutils.util
import io
import os
import re
import sys
import logging
import datetime
from python_utils.time import timedelta_to_seconds, epoch, format_time
from python_utils.converters import scale_1024
from python_utils.terminal import get_terminal_size

import six


assert timedelta_to_seconds
assert get_terminal_size
assert format_time
assert scale_1024
assert epoch


def is_terminal(fd, is_terminal=None):
    if is_terminal is None:
        if 'JPY_PARENT_PID' in os.environ:
            is_terminal = True
        else:
            is_terminal = env_flag('PROGRESSBAR_IS_TERMINAL', None)

    if is_terminal is None:  # pragma: no cover
        try:
            is_terminal = fd.isatty()
        except Exception:
            is_terminal = False

    return is_terminal


def deltas_to_seconds(*deltas, **kwargs):  # default=ValueError):
    '''
    Convert timedeltas and seconds as int to seconds as float while coalescing

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
    '''
    default = kwargs.pop('default', ValueError)
    assert not kwargs, 'Only the `default` keyword argument is supported'

    for delta in deltas:
        if delta is None:
            continue
        if isinstance(delta, datetime.timedelta):
            return timedelta_to_seconds(delta)
        elif not isinstance(delta, float):
            return float(delta)
        else:
            return delta

    if default is ValueError:
        raise ValueError('No valid deltas passed to `deltas_to_seconds`')
    else:
        return default


def no_color(value):
    '''
    Return the `value` without ANSI escape codes

    >>> no_color(b'\u001b[1234]abc') == b'abc'
    True
    >>> str(no_color(u'\u001b[1234]abc'))
    'abc'
    >>> str(no_color('\u001b[1234]abc'))
    'abc'
    '''
    if isinstance(value, bytes):
        pattern = '\\\u001b\\[.*?[@-~]'
        pattern = pattern.encode()
        replace = b''
        assert isinstance(pattern, bytes)
    else:
        pattern = u'\x1b\\[.*?[@-~]'
        replace = ''

    return re.sub(pattern, replace, value)


def len_color(value):
    '''
    Return the length of `value` without ANSI escape codes

    >>> len_color(b'\u001b[1234]abc')
    3
    >>> len_color(u'\u001b[1234]abc')
    3
    >>> len_color('\u001b[1234]abc')
    3
    '''
    return len(no_color(value))


def env_flag(name, default=None):
    '''
    Accepts environt variables formatted as y/n, yes/no, 1/0, true/false,
    on/off, and returns it as a boolean

    If the environt variable is not defined, or has an unknown value, returns
    `default`
    '''
    try:
        return bool(distutils.util.strtobool(os.environ.get(name, '')))
    except ValueError:
        return default


class WrappingIO:

    def __init__(self, target, capturing=False, listeners=set()):
        self.buffer = six.StringIO()
        self.target = target
        self.capturing = capturing
        self.listeners = listeners

    def write(self, value):
        if self.capturing:
            self.buffer.write(value)
            if '\n' in value:
                for listener in self.listeners:  # pragma: no branch
                    listener.update()
        else:
            self.target.write(value)

    def flush(self):
        self.buffer.flush()

    def _flush(self):
        value = self.buffer.getvalue()
        if value:
            self.flush()
            self.target.write(value)
            self.buffer.seek(0)
            self.buffer.truncate(0)


class StreamWrapper(object):
    '''Wrap stdout and stderr globally'''

    def __init__(self):
        self.stdout = self.original_stdout = sys.stdout
        self.stderr = self.original_stderr = sys.stderr
        self.original_excepthook = sys.excepthook
        self.wrapped_stdout = 0
        self.wrapped_stderr = 0
        self.wrapped_excepthook = 0
        self.capturing = 0
        self.listeners = set()

        if env_flag('WRAP_STDOUT', default=False):  # pragma: no cover
            self.wrap_stdout()

        if env_flag('WRAP_STDERR', default=False):  # pragma: no cover
            self.wrap_stderr()

    def start_capturing(self, bar=None):
        if bar:  # pragma: no branch
            self.listeners.add(bar)

        self.capturing += 1
        self.update_capturing()

    def stop_capturing(self, bar=None):
        if bar:  # pragma: no branch
            try:
                self.listeners.remove(bar)
            except KeyError:
                pass

        self.capturing -= 1
        self.update_capturing()

    def update_capturing(self):  # pragma: no cover
        if isinstance(self.stdout, WrappingIO):
            self.stdout.capturing = self.capturing > 0

        if isinstance(self.stderr, WrappingIO):
            self.stderr.capturing = self.capturing > 0

        if self.capturing <= 0:
            self.flush()

    def wrap(self, stdout=False, stderr=False):
        if stdout:
            self.wrap_stdout()

        if stderr:
            self.wrap_stderr()

    def wrap_stdout(self):
        self.wrap_excepthook()

        if not self.wrapped_stdout:
            self.stdout = sys.stdout = WrappingIO(self.original_stdout,
                                                  listeners=self.listeners)
        self.wrapped_stdout += 1

        return sys.stdout

    def wrap_stderr(self):
        self.wrap_excepthook()

        if not self.wrapped_stderr:
            self.stderr = sys.stderr = WrappingIO(self.original_stderr,
                                                  listeners=self.listeners)
        self.wrapped_stderr += 1

        return sys.stderr

    def unwrap_excepthook(self):
        if self.wrapped_excepthook:
            self.wrapped_excepthook -= 1
            sys.excepthook = self.original_excepthook

    def wrap_excepthook(self):
        if not self.wrapped_excepthook:
            logger.debug('wrapping excepthook')
            self.wrapped_excepthook += 1
            sys.excepthook = self.excepthook

    def unwrap(self, stdout=False, stderr=False):
        if stdout:
            self.unwrap_stdout()

        if stderr:
            self.unwrap_stderr()

    def unwrap_stdout(self):
        if self.wrapped_stdout > 1:
            self.wrapped_stdout -= 1
        else:
            sys.stdout = self.original_stdout
            self.wrapped_stdout = 0

    def unwrap_stderr(self):
        if self.wrapped_stderr > 1:
            self.wrapped_stderr -= 1
        else:
            sys.stderr = self.original_stderr
            self.wrapped_stderr = 0

    def flush(self):
        if self.wrapped_stdout:  # pragma: no branch
            try:
                self.stdout._flush()
            except (io.UnsupportedOperation,
                    AttributeError):  # pragma: no cover
                self.wrapped_stdout = False
                logger.warn('Disabling stdout redirection, %r is not seekable',
                            sys.stdout)

        if self.wrapped_stderr:  # pragma: no branch
            try:
                self.stderr._flush()
            except (io.UnsupportedOperation,
                    AttributeError):  # pragma: no cover
                self.wrapped_stderr = False
                logger.warn('Disabling stderr redirection, %r is not seekable',
                            sys.stderr)

    def excepthook(self, exc_type, exc_value, exc_traceback):
        self.original_excepthook(exc_type, exc_value, exc_traceback)
        self.flush()


class AttributeDict(dict):
    '''
    A dict that can be accessed with .attribute

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
    '''
    def __getattr__(self, name):
        if name in self:
            return self[name]
        else:
            raise AttributeError("No such attribute: " + name)

    def __setattr__(self, name, value):
        self[name] = value

    def __delattr__(self, name):
        if name in self:
            del self[name]
        else:
            raise AttributeError("No such attribute: " + name)


logger = logging.getLogger(__name__)
streams = StreamWrapper()
