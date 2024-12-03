from __future__ import annotations
import abc
import contextlib
import itertools
import logging
import math
import os
import sys
import time
import timeit
import warnings
from copy import deepcopy
from datetime import datetime
from python_utils import converters, types
import progressbar.env
import progressbar.terminal
import progressbar.terminal.stream
from . import base, utils, widgets, widgets as widgets_module
from .terminal import os_specific
logger = logging.getLogger(__name__)
NumberT = float
T = types.TypeVar('T')

class ProgressBarMixinBase(abc.ABC):
    _started = False
    _finished = False
    _last_update_time: types.Optional[float] = None
    term_width: int = 80
    widgets: types.MutableSequence[widgets_module.WidgetBase | str]
    max_error: bool
    prefix: types.Optional[str]
    suffix: types.Optional[str]
    left_justify: bool
    widget_kwargs: types.Dict[str, types.Any]
    custom_len: types.Callable[[str], int]
    initial_start_time: types.Optional[datetime]
    poll_interval: types.Optional[float]
    min_poll_interval: float
    num_intervals: int = 0
    next_update: int = 0
    value: NumberT
    previous_value: types.Optional[NumberT]
    min_value: NumberT
    max_value: NumberT | types.Type[base.UnknownLength]
    end_time: types.Optional[datetime]
    start_time: types.Optional[datetime]
    seconds_elapsed: float
    extra: types.Dict[str, types.Any]
    last_update_time = property(get_last_update_time, set_last_update_time)

    def __init__(self, **kwargs):
        self._started = False
        self._finished = False
        self._last_update_time = None
        self.term_width = 80
        self.widgets = []
        self.max_error = False
        self.prefix = None
        self.suffix = None
        self.left_justify = True
        self.widget_kwargs = {}
        self.custom_len = len
        self.initial_start_time = None
        self.poll_interval = None
        self.min_poll_interval = 0.1
        self.num_intervals = 0
        self.next_update = 0
        self.value = 0
        self.previous_value = None
        self.min_value = 0
        self.max_value = None
        self.end_time = None
        self.start_time = None
        self.seconds_elapsed = 0
        self.extra = {}

    def __del__(self):
        if not self._finished and self._started:
            try:
                self.finish()
            except AttributeError:
                pass
        self._started = False
        self._finished = True

    def __getstate__(self):
        return self.__dict__

class ProgressBarBase(types.Iterable, ProgressBarMixinBase):
    _index_counter = itertools.count()
    index: int = -1
    label: str = ''

    def __init__(self, **kwargs):
        self.index = next(self._index_counter)
        super().__init__(**kwargs)

    def __repr__(self):
        label = f': {self.label}' if self.label else ''
        return f'<{self.__class__.__name__}#{self.index}{label}>'

class DefaultFdMixin(ProgressBarMixinBase):
    fd: base.TextIO = sys.stderr
    is_ansi_terminal: bool | None = False
    is_terminal: bool | None
    line_breaks: bool | None = True
    enable_colors: progressbar.env.ColorSupport = progressbar.env.COLOR_SUPPORT

    def __init__(self, fd: base.TextIO=sys.stderr, is_terminal: bool | None=None, line_breaks: bool | None=None, enable_colors: progressbar.env.ColorSupport | None=None, line_offset: int=0, **kwargs):
        if fd is sys.stdout:
            fd = utils.streams.original_stdout
        elif fd is sys.stderr:
            fd = utils.streams.original_stderr
        fd = self._apply_line_offset(fd, line_offset)
        self.fd = fd
        self.is_ansi_terminal = progressbar.env.is_ansi_terminal(fd)
        self.is_terminal = progressbar.env.is_terminal(fd, is_terminal)
        self.line_breaks = self._determine_line_breaks(line_breaks)
        self.enable_colors = self._determine_enable_colors(enable_colors)
        super().__init__(**kwargs)

    def _determine_enable_colors(self, enable_colors: progressbar.env.ColorSupport | None) -> progressbar.env.ColorSupport:
        """
        Determines the color support for the progress bar.

        This method checks the `enable_colors` parameter and the environment
        variables `PROGRESSBAR_ENABLE_COLORS` and `FORCE_COLOR` to determine
        the color support.

        If `enable_colors` is:
         - `None`, it checks the environment variables and the terminal
            compatibility to ANSI.
         - `True`, it sets the color support to XTERM_256.
         - `False`, it sets the color support to NONE.
         - For different values that are not instances of
           `progressbar.env.ColorSupport`, it raises a ValueError.

        Args:
             enable_colors (progressbar.env.ColorSupport | None): The color
             support setting from the user. It can be None, True, False,
             or an instance of `progressbar.env.ColorSupport`.

        Returns:
            progressbar.env.ColorSupport: The determined color support.

        Raises:
            ValueError: If `enable_colors` is not None, True, False, or an
            instance of `progressbar.env.ColorSupport`.
        """
        if enable_colors is None:
            if os.environ.get('PROGRESSBAR_ENABLE_COLORS', '').lower() in ('1', 'true', 'yes', 'on'):
                return progressbar.env.ColorSupport.XTERM_256
            elif os.environ.get('FORCE_COLOR', '').lower() in ('1', 'true', 'yes', 'on'):
                return progressbar.env.ColorSupport.XTERM_256
            elif self.is_ansi_terminal:
                return progressbar.env.ColorSupport.XTERM_256
            else:
                return progressbar.env.ColorSupport.NONE
        elif enable_colors is True:
            return progressbar.env.ColorSupport.XTERM_256
        elif enable_colors is False:
            return progressbar.env.ColorSupport.NONE
        elif isinstance(enable_colors, progressbar.env.ColorSupport):
            return enable_colors
        else:
            raise ValueError("Invalid value for enable_colors")

    def _format_line(self):
        """Joins the widgets and justifies the line."""
        result = []
        if self.prefix:
            result.append(self.prefix)
        
        for widget in self.widgets:
            if isinstance(widget, str):
                result.append(widget)
            else:
                result.append(widget(self, self.widget_kwargs))
        
        if self.suffix:
            result.append(self.suffix)
        
        line = ''.join(result)
        width = self.custom_len(line)
        
        if width < self.term_width:
            if self.left_justify:
                return line.ljust(self.term_width)
            else:
                return line.rjust(self.term_width)
        else:
            return line[:self.term_width]

class ResizableMixin(ProgressBarMixinBase):

    def __init__(self, term_width: int | None=None, **kwargs):
        ProgressBarMixinBase.__init__(self, **kwargs)
        self.signal_set = False
        if term_width:
            self.term_width = term_width
        else:
            with contextlib.suppress(Exception):
                self._handle_resize()
                import signal
                self._prev_handle = signal.getsignal(signal.SIGWINCH)
                signal.signal(signal.SIGWINCH, self._handle_resize)
                self.signal_set = True

    def _handle_resize(self, signum=None, frame=None):
        """Tries to catch resize signals sent from the terminal."""
        try:
            self.term_width, _ = os.get_terminal_size()
        except (AttributeError, OSError):
            try:
                self.term_width = int(os.environ.get('COLUMNS', 80))
            except ValueError:
                self.term_width = 80

class StdRedirectMixin(DefaultFdMixin):
    redirect_stderr: bool = False
    redirect_stdout: bool = False
    stdout: utils.WrappingIO | base.IO
    stderr: utils.WrappingIO | base.IO
    _stdout: base.IO
    _stderr: base.IO

    def __init__(self, redirect_stderr: bool=False, redirect_stdout: bool=False, **kwargs):
        DefaultFdMixin.__init__(self, **kwargs)
        self.redirect_stderr = redirect_stderr
        self.redirect_stdout = redirect_stdout
        self._stdout = self.stdout = sys.stdout
        self._stderr = self.stderr = sys.stderr

class ProgressBar(StdRedirectMixin, ResizableMixin, ProgressBarBase):
    """The ProgressBar class which updates and prints the bar.

    Args:
        min_value (int): The minimum/start value for the progress bar
        max_value (int): The maximum/end value for the progress bar.
                            Defaults to `_DEFAULT_MAXVAL`
        widgets (list): The widgets to render, defaults to the result of
                        `default_widget()`
        left_justify (bool): Justify to the left if `True` or the right if
                                `False`
        initial_value (int): The value to start with
        poll_interval (float): The update interval in seconds.
            Note that if your widgets include timers or animations, the actual
            interval may be smaller (faster updates).  Also note that updates
            never happens faster than `min_poll_interval` which can be used for
            reduced output in logs
        min_poll_interval (float): The minimum update interval in seconds.
            The bar will _not_ be updated faster than this, despite changes in
            the progress, unless `force=True`.  This is limited to be at least
            `_MINIMUM_UPDATE_INTERVAL`.  If available, it is also bound by the
            environment variable PROGRESSBAR_MINIMUM_UPDATE_INTERVAL
        widget_kwargs (dict): The default keyword arguments for widgets
        custom_len (function): Method to override how the line width is
            calculated. When using non-latin characters the width
            calculation might be off by default
        max_error (bool): When True the progressbar will raise an error if it
            goes beyond it's set max_value. Otherwise the max_value is simply
            raised when needed
            prefix (str): Prefix the progressbar with the given string
            suffix (str): Prefix the progressbar with the given string
        variables (dict): User-defined variables variables that can be used
            from a label using `format='{variables.my_var}'`.  These values can
            be updated using `bar.update(my_var='newValue')` This can also be
            used to set initial values for variables' widgets
        line_offset (int): The number of lines to offset the progressbar from
            your current line. This is useful if you have other output or
            multiple progressbars

    A common way of using it is like:

    >>> progress = ProgressBar().start()
    >>> for i in range(100):
    ...     progress.update(i + 1)
    ...     # do something
    ...
    >>> progress.finish()

    You can also use a ProgressBar as an iterator:

    >>> progress = ProgressBar()
    >>> some_iterable = range(100)
    >>> for i in progress(some_iterable):
    ...     # do something
    ...     pass
    ...

    Since the progress bar is incredibly customizable you can specify
    different widgets of any type in any order. You can even write your own
    widgets! However, since there are already a good number of widgets you
    should probably play around with them before moving on to create your own
    widgets.

    The term_width parameter represents the current terminal width. If the
    parameter is set to an integer then the progress bar will use that,
    otherwise it will attempt to determine the terminal width falling back to
    80 columns if the width cannot be determined.

    When implementing a widget's update method you are passed a reference to
    the current progress bar. As a result, you have access to the
    ProgressBar's methods and attributes. Although there is nothing preventing
    you from changing the ProgressBar you should treat it as read only.
    """
    _iterable: types.Optional[types.Iterator]
    _DEFAULT_MAXVAL: type[base.UnknownLength] = base.UnknownLength
    _MINIMUM_UPDATE_INTERVAL: float = 0.05
    _last_update_time: types.Optional[float] = None
    paused: bool = False

    def __init__(self, min_value: NumberT=0, max_value: NumberT | types.Type[base.UnknownLength] | None=None, widgets: types.Optional[types.Sequence[widgets_module.WidgetBase | str]]=None, left_justify: bool=True, initial_value: NumberT=0, poll_interval: types.Optional[float]=None, widget_kwargs: types.Optional[types.Dict[str, types.Any]]=None, custom_len: types.Callable[[str], int]=utils.len_color, max_error=True, prefix=None, suffix=None, variables=None, min_poll_interval=None, **kwargs):
        """Initializes a progress bar with sane defaults."""
        StdRedirectMixin.__init__(self, **kwargs)
        ResizableMixin.__init__(self, **kwargs)
        ProgressBarBase.__init__(self, **kwargs)
        if not max_value and kwargs.get('maxval') is not None:
            warnings.warn('The usage of `maxval` is deprecated, please use `max_value` instead', DeprecationWarning, stacklevel=1)
            max_value = kwargs.get('maxval')
        if not poll_interval and kwargs.get('poll'):
            warnings.warn('The usage of `poll` is deprecated, please use `poll_interval` instead', DeprecationWarning, stacklevel=1)
            poll_interval = kwargs.get('poll')
        if max_value and min_value > types.cast(NumberT, max_value):
            raise ValueError('Max value needs to be bigger than the min value')
        self.min_value = min_value
        self.max_value = max_value
        self.max_error = max_error
        self.widgets = []
        for widget in widgets or []:
            if getattr(widget, 'copy', True):
                widget = deepcopy(widget)
            self.widgets.append(widget)
        self.prefix = prefix
        self.suffix = suffix
        self.widget_kwargs = widget_kwargs or {}
        self.left_justify = left_justify
        self.value = initial_value
        self._iterable = None
        self.custom_len = custom_len
        self.initial_start_time = kwargs.get('start_time')
        self.init()
        poll_interval = utils.deltas_to_seconds(poll_interval, default=None)
        min_poll_interval = utils.deltas_to_seconds(min_poll_interval, default=None)
        self._MINIMUM_UPDATE_INTERVAL = utils.deltas_to_seconds(self._MINIMUM_UPDATE_INTERVAL) or self._MINIMUM_UPDATE_INTERVAL
        self.poll_interval = poll_interval
        self.min_poll_interval = max(min_poll_interval or self._MINIMUM_UPDATE_INTERVAL, self._MINIMUM_UPDATE_INTERVAL, float(os.environ.get('PROGRESSBAR_MINIMUM_UPDATE_INTERVAL', 0)))
        self.variables = utils.AttributeDict(variables or {})
        for widget in self.widgets:
            if isinstance(widget, widgets_module.VariableMixin) and widget.name not in self.variables:
                self.variables[widget.name] = None

    def init(self):
        """
        (re)initialize values to original state so the progressbar can be
        used (again).
        """
        self._last_update_time = None
        self.num_intervals = 0
        self.next_update = 0
        self.value = self.min_value
        self.previous_value = None
        self.start_time = None
        self.end_time = None
        self.seconds_elapsed = 0
        self._started = False
        self._finished = False

    @property
    def percentage(self) -> float | None:
        """Return current percentage, returns None if no max_value is given."""
        if self.max_value is None or isinstance(self.max_value, base.UnknownLength):
            return None
        
        if self.max_value == self.min_value:
            return 100.0
        
        total_range = self.max_value - self.min_value
        current_value = self.value - self.min_value
        
        if total_range <= 0:
            return 0.0
        
        return min(100.0, max(0.0, current_value / total_range * 100))

    def data(self) -> types.Dict[str, types.Any]:
        """
        Returns a dictionary with various data about the progress bar.
        """
        now = datetime.now()
        time_elapsed = now - self.start_time if self.start_time else timedelta()
        total_seconds_elapsed = time_elapsed.total_seconds()

        return {
            'max_value': self.max_value,
            'start_time': self.start_time,
            'last_update_time': self.last_update_time,
            'end_time': self.end_time,
            'value': self.value,
            'previous_value': self.previous_value,
            'updates': self.num_intervals,
            'total_seconds_elapsed': total_seconds_elapsed,
            'seconds_elapsed': int(total_seconds_elapsed % 60),
            'minutes_elapsed': int((total_seconds_elapsed // 60) % 60),
            'hours_elapsed': int((total_seconds_elapsed // 3600) % 24),
            'days_elapsed': int(total_seconds_elapsed // 86400),
            'time_elapsed': time_elapsed,
            'percentage': self.percentage,
            'dynamic_messages': self.variables,  # For backwards compatibility
            'variables': self.variables,
        }

    def __call__(self, iterable, max_value=None):
        """Use a ProgressBar to iterate through an iterable."""
        if max_value is not None:
            self.max_value = max_value
        elif self.max_value is None:
            try:
                self.max_value = len(iterable)
            except TypeError:
                self.max_value = base.UnknownLength
        self._iterable = iter(iterable)
        return self

    def __iter__(self):
        return self

    def __next__(self):
        try:
            if self._iterable is None:
                value = self.value
            else:
                value = next(self._iterable)
            if self.start_time is None:
                self.start()
            else:
                self.update(self.value + 1)
        except StopIteration:
            self.finish()
            raise
        except GeneratorExit:
            self.finish(dirty=True)
            raise
        else:
            return value

    def __exit__(self, exc_type, exc_value, traceback):
        self.finish(dirty=bool(exc_type))

    def __enter__(self):
        return self
    next = __next__

    def __iadd__(self, value):
        """Updates the ProgressBar by adding a new value."""
        return self.increment(value)

    def _needs_update(self):
        """Returns whether the ProgressBar should redraw the line."""
        if self._finished:
            return False

        if self.poll_interval is None:
            return True

        now = time.time()
        if self._last_update_time is None:
            return True

        time_since_last_update = now - self._last_update_time
        return time_since_last_update >= self.poll_interval

    def update(self, value=None, force=False, **kwargs):
        """Updates the ProgressBar to a new value."""
        if self._finished:
            return self

        if value is not None:
            self.previous_value = self.value
            self.value = value

        if self.max_value is None and value is not None:
            if (self.max_value is None or
                    (not isinstance(self.max_value, base.UnknownLength) and
                     value > self.max_value)):
                self.max_value = value

        if self.start_time is None:
            self.start()

        now = time.time()
        self.num_intervals += 1

        if self._needs_update() or force:
            self._last_update_time = now
            self._print()

        self.variables.update(kwargs)

        return self

    def start(self, max_value=None, init=True, *args, **kwargs):
        """Starts measuring time, and prints the bar at 0%."""
        if init:
            self.init()

        if max_value is not None:
            self.max_value = max_value

        if self.max_value is None:
            self.max_value = base.UnknownLength

        self.num_intervals = 0
        self.start_time = self.initial_start_time or datetime.now()
        self.update(self.min_value, force=True)
        self._started = True

        return self

    def finish(self, end='\n', dirty=False):
        """
        Puts the ProgressBar bar in the finished state.

        Also flushes and disables output buffering if this was the last
        progressbar running.

        Args:
            end (str): The string to end the progressbar with, defaults to a
                newline
            dirty (bool): When True the progressbar kept the current state and
                won't be set to 100 percent
        """
        if self._finished:
            return

        if not dirty and self.max_value is not None and not isinstance(self.max_value, base.UnknownLength):
            self.update(self.max_value, force=True)

        self.end_time = datetime.now()
        self._finished = True
        self._print(end=end)

        if self.signal_set:
            import signal
            signal.signal(signal.SIGWINCH, self._prev_handle)

        if not self.redirect_stderr and not self.redirect_stdout:
            utils.streams.flush()

    @property
    def currval(self):
        """
        Legacy method to make progressbar-2 compatible with the original
        progressbar package.
        """
        return self.value

class DataTransferBar(ProgressBar):
    """A progress bar with sensible defaults for downloads etc.

    This assumes that the values its given are numbers of bytes.
    """

class NullBar(ProgressBar):
    """
    Progress bar that does absolutely nothing. Useful for single verbosity
    flags.
    """
