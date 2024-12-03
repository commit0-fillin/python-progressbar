from __future__ import annotations
import contextlib
import enum
import os
import re
import typing
from . import base

def env_flag(name, default=None):
    """
    Accepts environt variables formatted as y/n, yes/no, 1/0, true/false,
    on/off, and returns it as a boolean.

    If the environment variable is not defined, or has an unknown value,
    returns `default`
    """
    value = os.environ.get(name)
    if value is None:
        return default
    
    value = value.lower()
    if value in ('y', 'yes', '1', 'true', 'on'):
        return True
    elif value in ('n', 'no', '0', 'false', 'off'):
        return False
    else:
        return default

class ColorSupport(enum.IntEnum):
    """Color support for the terminal."""
    NONE = 0
    XTERM = 16
    XTERM_256 = 256
    XTERM_TRUECOLOR = 16777216
    WINDOWS = 8

    @classmethod
    def from_env(cls):
        """Get the color support from the environment.

        If any of the environment variables contain `24bit` or `truecolor`,
        we will enable true color/24 bit support. If they contain `256`, we
        will enable 256 color/8 bit support. If they contain `xterm`, we will
        enable 16 color support. Otherwise, we will assume no color support.

        If `JUPYTER_COLUMNS` or `JUPYTER_LINES` or `JPY_PARENT_PID` is set, we
        will assume true color support.

        Note that the highest available value will be used! Having
        `COLORTERM=truecolor` will override `TERM=xterm-256color`.
        """
        if (os.environ.get('JUPYTER_COLUMNS') or
            os.environ.get('JUPYTER_LINES') or
            os.environ.get('JPY_PARENT_PID')):
            return cls.XTERM_TRUECOLOR

        color_term = os.environ.get('COLORTERM', '').lower()
        term = os.environ.get('TERM', '').lower()

        if '24bit' in color_term or 'truecolor' in color_term:
            return cls.XTERM_TRUECOLOR
        elif '256' in term or '256' in color_term:
            return cls.XTERM_256
        elif 'xterm' in term or 'xterm' in color_term:
            return cls.XTERM
        elif os.name == 'nt':
            return cls.WINDOWS
        else:
            return cls.NONE
if os.name == 'nt':
    pass
JUPYTER = bool(os.environ.get('JUPYTER_COLUMNS') or os.environ.get('JUPYTER_LINES') or os.environ.get('JPY_PARENT_PID'))
COLOR_SUPPORT = ColorSupport.from_env()
ANSI_TERMS = ('([xe]|bv)term', '(sco)?ansi', 'cygwin', 'konsole', 'linux', 'rxvt', 'screen', 'tmux', 'vt(10[02]|220|320)')
ANSI_TERM_RE = re.compile(f'^({'|'.join(ANSI_TERMS)})', re.IGNORECASE)
