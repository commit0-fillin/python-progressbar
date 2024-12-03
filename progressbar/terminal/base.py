from __future__ import annotations
import abc
import collections
import colorsys
import enum
import threading
from collections import defaultdict
from typing import ClassVar
from python_utils import converters, types
from .. import base as pbase, env
from .os_specific import getch
ESC = '\x1b'

class CSI:
    _code: str
    _template = ESC + '[{args}{code}'

    def __init__(self, code, *default_args):
        self._code = code
        self._default_args = default_args

    def __call__(self, *args):
        return self._template.format(args=';'.join(map(str, args or self._default_args)), code=self._code)

    def __str__(self):
        return self()

class CSINoArg(CSI):

    def __call__(self):
        return super().__call__()
CUP = CSI('H', 1, 1)
UP = CSI('A', 1)
DOWN = CSI('B', 1)
RIGHT = CSI('C', 1)
LEFT = CSI('D', 1)
NEXT_LINE = CSI('E', 1)
PREVIOUS_LINE = CSI('F', 1)
COLUMN = CSI('G', 1)
CLEAR_SCREEN = CSI('J', 0)
CLEAR_SCREEN_TILL_END = CSINoArg('0J')
CLEAR_SCREEN_TILL_START = CSINoArg('1J')
CLEAR_SCREEN_ALL = CSINoArg('2J')
CLEAR_SCREEN_ALL_AND_HISTORY = CSINoArg('3J')
CLEAR_LINE_ALL = CSI('K')
CLEAR_LINE_RIGHT = CSINoArg('0K')
CLEAR_LINE_LEFT = CSINoArg('1K')
CLEAR_LINE = CSINoArg('2K')
SCROLL_UP = CSI('S')
SCROLL_DOWN = CSI('T')
SAVE_CURSOR = CSINoArg('s')
RESTORE_CURSOR = CSINoArg('u')
HIDE_CURSOR = CSINoArg('?25l')
SHOW_CURSOR = CSINoArg('?25h')

class _CPR(str):
    _response_lock = threading.Lock()

    def __call__(self, stream) -> tuple[int, int]:
        res: str = ''
        with self._response_lock:
            stream.write(str(self))
            stream.flush()
            while not res.endswith('R'):
                char = getch()
                if char is not None:
                    res += char
            res_list = res[2:-1].split(';')
            res_list = tuple((int(item) if item.isdigit() else item for item in res_list))
            if len(res_list) == 1:
                return types.cast(types.Tuple[int, int], res_list[0])
            return types.cast(types.Tuple[int, int], tuple(res_list))

class WindowsColors(enum.Enum):
    BLACK = (0, 0, 0)
    BLUE = (0, 0, 128)
    GREEN = (0, 128, 0)
    CYAN = (0, 128, 128)
    RED = (128, 0, 0)
    MAGENTA = (128, 0, 128)
    YELLOW = (128, 128, 0)
    GREY = (192, 192, 192)
    INTENSE_BLACK = (128, 128, 128)
    INTENSE_BLUE = (0, 0, 255)
    INTENSE_GREEN = (0, 255, 0)
    INTENSE_CYAN = (0, 255, 255)
    INTENSE_RED = (255, 0, 0)
    INTENSE_MAGENTA = (255, 0, 255)
    INTENSE_YELLOW = (255, 255, 0)
    INTENSE_WHITE = (255, 255, 255)

    @staticmethod
    def from_rgb(rgb: types.Tuple[int, int, int]):
        """
        Find the closest WindowsColors to the given RGB color.

        >>> WindowsColors.from_rgb((0, 0, 0))
        <WindowsColors.BLACK: (0, 0, 0)>

        >>> WindowsColors.from_rgb((255, 255, 255))
        <WindowsColors.INTENSE_WHITE: (255, 255, 255)>

        >>> WindowsColors.from_rgb((0, 255, 0))
        <WindowsColors.INTENSE_GREEN: (0, 255, 0)>

        >>> WindowsColors.from_rgb((45, 45, 45))
        <WindowsColors.BLACK: (0, 0, 0)>

        >>> WindowsColors.from_rgb((128, 0, 128))
        <WindowsColors.MAGENTA: (128, 0, 128)>
        """
        min_distance = float('inf')
        closest_color = None
        for color in WindowsColors:
            distance = sum((a - b) ** 2 for a, b in zip(rgb, color.value))
            if distance < min_distance:
                min_distance = distance
                closest_color = color
        return closest_color

class WindowsColor:
    """
    Windows compatible color class for when ANSI is not supported.
    Currently a no-op because it is not possible to buffer these colors.

    >>> WindowsColor(WindowsColors.RED)('test')
    'test'
    """
    __slots__ = ('color',)

    def __init__(self, color: Color):
        self.color = color

    def __call__(self, text):
        return text

class RGB(collections.namedtuple('RGB', ['red', 'green', 'blue'])):
    __slots__ = ()

    def __str__(self):
        return self.rgb

    @property
    def to_windows(self):
        """
        Convert an RGB color (0-255 per channel) to the closest color in the
        Windows 16 color scheme.
        """
        return WindowsColors.from_rgb((self.red, self.green, self.blue))

class HSL(collections.namedtuple('HSL', ['hue', 'saturation', 'lightness'])):
    """
    Hue, Saturation, Lightness color.

    Hue is a value between 0 and 360, saturation and lightness are between 0(%)
    and 100(%).

    """
    __slots__ = ()

    @classmethod
    def from_rgb(cls, rgb: RGB) -> HSL:
        """
        Convert a 0-255 RGB color to a 0-360 (hue) and 0-100 (saturation, lightness) HSL color.
        """
        r, g, b = rgb.red / 255.0, rgb.green / 255.0, rgb.blue / 255.0
        max_val = max(r, g, b)
        min_val = min(r, g, b)
        l = (max_val + min_val) / 2

        if max_val == min_val:
            h = s = 0  # achromatic
        else:
            d = max_val - min_val
            s = d / (2 - max_val - min_val) if l > 0.5 else d / (max_val + min_val)
            if max_val == r:
                h = (g - b) / d + (6 if g < b else 0)
            elif max_val == g:
                h = (b - r) / d + 2
            else:
                h = (r - g) / d + 4
            h /= 6

        return cls(int(h * 360), int(s * 100), int(l * 100))

class ColorBase(abc.ABC):
    pass

class Color(collections.namedtuple('Color', ['rgb', 'hls', 'name', 'xterm']), ColorBase):
    """
    Color base class.

    This class contains the colors in RGB (Red, Green, Blue), HSL (Hue,
    Lightness, Saturation) and Xterm (8-bit) formats. It also contains the
    color name.

    To make a custom color the only required arguments are the RGB values.
    The other values will be automatically interpolated from that if needed,
    but you can be more explicitly if you wish.
    """
    __slots__ = ()

    def __call__(self, value: str) -> str:
        return self.fg(value)

    def __str__(self):
        return self.name

    def __repr__(self):
        return f'{self.__class__.__name__}({self.name!r})'

    def __hash__(self):
        return hash(self.rgb)

class Colors:
    by_name: ClassVar[defaultdict[str, types.List[Color]]] = collections.defaultdict(list)
    by_lowername: ClassVar[defaultdict[str, types.List[Color]]] = collections.defaultdict(list)
    by_hex: ClassVar[defaultdict[str, types.List[Color]]] = collections.defaultdict(list)
    by_rgb: ClassVar[defaultdict[RGB, types.List[Color]]] = collections.defaultdict(list)
    by_hls: ClassVar[defaultdict[HSL, types.List[Color]]] = collections.defaultdict(list)
    by_xterm: ClassVar[dict[int, Color]] = dict()

class ColorGradient(ColorBase):

    def __init__(self, *colors: Color, interpolate=Colors.interpolate):
        assert colors
        self.colors = colors
        self.interpolate = interpolate

    def __call__(self, value: float) -> Color:
        return self.get_color(value)

    def get_color(self, value: float) -> Color:
        """Map a value from 0 to 1 to a color."""
        if value <= 0:
            return self.colors[0]
        if value >= 1:
            return self.colors[-1]

        segment_size = 1.0 / (len(self.colors) - 1)
        segment = int(value / segment_size)
        t = (value - segment * segment_size) / segment_size

        color1 = self.colors[segment]
        color2 = self.colors[segment + 1]

        return self.interpolate(color1, color2, t)
OptionalColor = types.Union[Color, ColorGradient, None]

def apply_colors(text: str, percentage: float | None=None, *, fg: OptionalColor=None, bg: OptionalColor=None, fg_none: Color | None=None, bg_none: Color | None=None, **kwargs: types.Any) -> str:
    """Apply colors/gradients to a string depending on the given percentage.

    When percentage is `None`, the `fg_none` and `bg_none` colors will be used.
    Otherwise, the `fg` and `bg` colors will be used. If the colors are
    gradients, the color will be interpolated depending on the percentage.
    """
    if percentage is None:
        fg_color = fg_none
        bg_color = bg_none
    else:
        fg_color = fg(percentage) if isinstance(fg, ColorGradient) else fg
        bg_color = bg(percentage) if isinstance(bg, ColorGradient) else bg

    result = text

    if fg_color:
        result = fg_color.fg(result)
    if bg_color:
        result = bg_color.bg(result)

    return result

class DummyColor:

    def __call__(self, text):
        return text

    def __repr__(self):
        return 'DummyColor()'

class SGR(CSI):
    _start_code: int
    _end_code: int
    _code = 'm'
    __slots__ = ('_start_code', '_end_code')

    def __init__(self, start_code: int, end_code: int):
        self._start_code = start_code
        self._end_code = end_code

    def __call__(self, text, *args):
        return self._start_template + text + self._end_template

class SGRColor(SGR):
    __slots__ = ('_color', '_start_code', '_end_code')

    def __init__(self, color: Color, start_code: int, end_code: int):
        self._color = color
        super().__init__(start_code, end_code)
encircled = SGR(52, 54)
framed = SGR(51, 54)
overline = SGR(53, 55)
bold = SGR(1, 22)
gothic = SGR(20, 10)
italic = SGR(3, 23)
strike_through = SGR(9, 29)
fast_blink = SGR(6, 25)
slow_blink = SGR(5, 25)
underline = SGR(4, 24)
double_underline = SGR(21, 24)
faint = SGR(2, 22)
inverse = SGR(7, 27)
