"""Chart helpers built on pyqtgraph.

The forecast fan is the app's signature element: a translucent P10–P90 band,
a stronger P25–P75 band, and a solid median — history joins it in a different
hue at a dashed "today" line.
"""
from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd
import pyqtgraph as pg
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont

from . import theme


def to_ts(dates) -> np.ndarray:
    """Dates → POSIX seconds (UTC midnight), for DateAxisItem.

    pandas ≥2 carries mixed datetime64 resolutions (s/us/ns), so normalize to
    seconds explicitly rather than trusting asi8's unit."""
    idx = pd.DatetimeIndex(pd.to_datetime(list(dates)))
    return idx.values.astype("datetime64[s]").astype(np.int64).astype(float)


class MoneyAxis(pg.AxisItem):
    def tickStrings(self, values, scale, spacing):
        out = []
        for v in values:
            av = abs(v)
            if av >= 1_000_000:
                s = f"${v / 1_000_000:.1f}M"
            elif av >= 10_000:
                s = f"${v / 1000:.0f}k"
            elif av >= 1000:
                s = f"${v / 1000:.1f}k"
            else:
                s = f"${v:.0f}"
            out.append(s)
        return out


def new_plot(date_axis: bool = True, money_left: bool = True,
             money_bottom: bool = False, legend: bool = False) -> pg.PlotWidget:
    axes = {}
    if date_axis:
        axes["bottom"] = pg.DateAxisItem(orientation="bottom")
    elif money_bottom:
        axes["bottom"] = MoneyAxis(orientation="bottom")
    if money_left:
        axes["left"] = MoneyAxis(orientation="left")
    w = pg.PlotWidget(axisItems=axes)
    w.showGrid(x=True, y=True, alpha=0.12)
    w.getPlotItem().hideButtons()
    w.getPlotItem().setMenuEnabled(False)
    w.getViewBox().setMouseMode(pg.ViewBox.PanMode)
    tick_font = QFont()
    tick_font.setPointSize(8)
    for side in ("left", "bottom"):
        ax = w.getAxis(side)
        ax.setTickFont(tick_font)
        ax.setPen(pg.mkPen(theme.BORDER))
        ax.setTextPen(pg.mkPen(theme.MUTED))
    if legend:
        lg = w.addLegend(offset=(8, 4), labelTextSize="8pt")
        lg.setBrush(pg.mkBrush(theme.qcolor(theme.SURFACE, 210)))
        lg.setPen(pg.mkPen(theme.BORDER))
    return w


# --- view locking -----------------------------------------------------------
# A pyqtgraph view with no limits pans and zooms forever. Scroll far enough and
# the data leaves the screen entirely, with nothing on the axes to say which
# direction it went — the chart reads as broken rather than empty, and the only
# way back is to know that double-click-free "auto range" exists. Bounding the
# view to the data it is drawing makes every gesture land somewhere meaningful.
MIN_SPAN_DAYS = 21.0        # furthest you can zoom *in* on a date axis
_PAD_Y = 0.08               # headroom above and below the data, as *drawn*
_PAD_X = 0.01
DAY = 86400.0

# Breathing room past the newest point. As a plain 1% of the span this was a
# day and a half on a four-month chart and eleven days on a three-year one —
# exactly backwards, since the short chart is the one being read closely. A
# floor in days keeps the last marker off the axis whatever the span.
_PAD_RIGHT_DAYS = 12.0

# --- slack: the room the *gestures* get, as opposed to the framing ----------
# The padding above decides how the chart looks when it opens. This decides how
# far it can be pushed afterwards, and the two were once the same number — which
# made the bounds feel like walls. Zooming toward the newest point ran out of
# travel almost immediately: with 1% headroom there is nowhere for the point of
# interest to move *to*, so the wheel stops responding while the thing being
# inspected is still hard against the edge.
#
# A full chart-width past the newest point. Zoomed all the way out, the ledger
# occupies the left half and the right half is room to work in — which is the
# side you want space on, because the newest data is the data being read and it
# is the end that a projection grows into.
SLACK_RIGHT_FRAC = 1.0      # of the drawn width, past the newest point
SLACK_RIGHT_DAYS = 45.0     # …but never less than this, on a very short chart

# How much of a long history a chart opens on. The bounds still cover every
# point — this only decides where the view starts. Recent months are what a
# balance chart is actually consulted for; three years compressed into one
# screen turns the part you care about into a few pixels at the right edge.
HISTORY_INITIAL_DAYS = 365.0


def _finite(arrays) -> np.ndarray:
    vals = []
    for a in arrays:
        if a is None:
            continue
        arr = np.asarray(a, dtype=float).ravel()
        arr = arr[np.isfinite(arr)]
        if arr.size:
            vals.append(arr)
    return np.concatenate(vals) if vals else np.array([], dtype=float)


def _y_bounds(data: np.ndarray, include_y, pad_y: float) -> tuple[float, float]:
    """Padded low/high for a set of values, pulling in nearby reference lines."""
    lo, hi = float(data.min()), float(data.max())
    span = max(hi - lo, 1.0)
    for v in include_y:
        if v is None or not np.isfinite(v):
            continue
        if lo - span <= v <= hi + span:
            lo, hi = min(lo, float(v)), max(hi, float(v))
    span = max(hi - lo, 1.0)
    return lo - span * pad_y, hi + span * pad_y


def _tail(y_series, x_data, frac: float) -> np.ndarray:
    """The plotted values falling inside the trailing ``frac`` of the x-range.

    With ``x_data`` the slice is exact. Without it the series are assumed to be
    evenly sampled across the span — true of every daily history in the app —
    and the last ``frac`` of each array is taken instead.
    """
    if x_data is not None:
        x = np.asarray(x_data, dtype=float).ravel()
        if x.size:
            cut = float(x.max()) - frac * (float(x.max()) - float(x.min()))
            keep = x >= cut
            if keep.any():
                sel = [np.asarray(a, dtype=float).ravel()[keep]
                       for a in y_series
                       if a is not None
                       and np.asarray(a).ravel().size == x.size]
                if sel:
                    return _finite(sel)
    sliced = []
    for a in y_series:
        if a is None:
            continue
        arr = np.asarray(a, dtype=float).ravel()
        if arr.size:
            sliced.append(arr[-max(2, int(round(arr.size * frac))):])
    return _finite(sliced)


def lock_view(plot: pg.PlotWidget, x_min: float, x_max: float, y_series,
              include_y=(), pad_y: float = _PAD_Y,
              min_span_days: float = MIN_SPAN_DAYS,
              initial_days: float | None = None, x_data=None,
              zoom_y: bool = True, slack_right: bool = True) -> None:
    """Bound a date chart in time, frame it in money, and show it.

    The two axes are deliberately not treated the same way.

    **Time is bounded.** The left edge sits tight against the first point,
    which is what stops a chart being scrolled off into blank space with no
    hint of which way it went. The right edge carries a full chart-width of
    slack, so the newest data — the part actually being read — has somewhere to
    move to when you zoom toward it.

    **Money is not bounded at all.** There are no y limits. Clamping the
    vertical range meant a view deliberately stretched with a right-drag, to
    pull a flat-looking line apart, snapped straight back to the data the
    moment the wheel was touched: the y limits reasserted themselves on every
    scroll and undid the gesture. Stretching the y axis is a legitimate way to
    read a chart and it should survive being scrolled. The x bounds are what
    keep the chart findable; the y axis can be left alone.

    Nothing is lost by that, because double-clicking any locked chart puts it
    back where it opened — see ``_install_home``.

    ``y_series`` are the plotted arrays; ``include_y`` are reference values
    (the floors) that should be visible *if they are anywhere near the data*.
    A $0 line a full chart-height below a $500k projection tells you nothing
    and costs every pixel of detail to display, so a reference further than one
    data-span away is left out of the opening frame rather than dragged into it.

    ``initial_days`` opens the chart on that many trailing days instead of the
    whole span — the *limits* are unchanged, so zooming out to the beginning
    still works, it simply is not where you land. ``x_data`` lets the opening
    y-range be computed from exactly the points in that window; ``zoom_y=False``
    keeps the full vertical range instead, which is what a bar chart wants —
    scaling to the visible bars would clip the taller ones the moment you
    panned back to them.
    """
    data = _finite(y_series)
    if data.size == 0:
        return
    lo, hi = _y_bounds(data, include_y, pad_y)

    x_lo, x_max = float(x_min), float(x_max)
    x_span = max(x_max - x_lo, DAY)
    x_lo -= x_span * _PAD_X
    frame_x_hi = x_max + max(x_span * _PAD_X, _PAD_RIGHT_DAYS * DAY)
    frame_x = frame_x_hi - x_lo

    # Zoom-out is capped at the whole envelope: the ledger in the left half,
    # working room in the right.
    x_hi = frame_x_hi
    if slack_right:
        x_hi = max(frame_x_hi, x_max + max(x_span * SLACK_RIGHT_FRAC,
                                           SLACK_RIGHT_DAYS * DAY))
    full_x = x_hi - x_lo

    # Opening window: the trailing slice, scaled to its own detail. Measured
    # against the frame, not the envelope — the slack is somewhere you can go,
    # not something the chart should open showing.
    view_x_lo, view_y_lo, view_y_hi = x_lo, lo, hi
    if initial_days and initial_days * DAY < frame_x:
        view_x_lo = frame_x_hi - initial_days * DAY
        frac = min(1.0, (frame_x_hi - view_x_lo) / frame_x)
        tail = _tail(y_series, x_data, frac) if zoom_y else np.array([])
        if tail.size:
            t_lo, t_hi = _y_bounds(tail, include_y, pad_y)
            view_y_lo, view_y_hi = t_lo, t_hi
            if view_y_hi - view_y_lo < 1.0:      # a perfectly flat window
                view_y_lo, view_y_hi = lo, hi

    vb = plot.getViewBox()
    vb.setLimits(
        xMin=x_lo, xMax=x_hi,
        # maxXRange == the whole envelope: you can pull back to the margin, but
        # never into open space beyond it.
        maxXRange=full_x,
        minXRange=min(min_span_days * DAY, full_x, frame_x_hi - view_x_lo),
        # Explicitly cleared, not merely omitted — this plot may have been
        # locked before, and setLimits only touches the keys it is given.
        yMin=None, yMax=None, minYRange=None, maxYRange=None,
    )
    _install_home(plot, (view_x_lo, frame_x_hi), (view_y_lo, view_y_hi))
    vb.setRange(xRange=(view_x_lo, frame_x_hi), yRange=(view_y_lo, view_y_hi),
                padding=0)


def _install_home(plot: pg.PlotWidget, x_range, y_range) -> None:
    """Remember where a chart opened, and put it back on a double-click.

    With the y axis unbounded there is no longer anything stopping a stray drag
    from pushing the data off the top of the screen, and this app hides
    pyqtgraph's auto-range button and context menu — so without a way home,
    "unbounded" would mean "one gesture from an empty chart and no way back
    except changing pages". Double-click is the recovery.

    Rebinding the handler on the instance is the same trick the version label
    in the header uses, and it is how PyQt looks a virtual up: attribute
    lookup on the object, so a plain assignment wins over the class method.
    """
    vb = plot.getViewBox()
    vb._home_range = (x_range, y_range)
    plot.setToolTip("Scroll to zoom · drag to pan · right-drag to stretch an "
                    "axis · double-click to reset")
    if getattr(vb, "_home_installed", False):
        return
    vb._home_installed = True
    fallback = vb.mouseDoubleClickEvent

    def go_home(ev) -> None:
        rng = getattr(vb, "_home_range", None)
        if rng is None:
            fallback(ev)
            return
        vb.setRange(xRange=rng[0], yRange=rng[1], padding=0)
        ev.accept()

    vb.mouseDoubleClickEvent = go_home


def unlock_view(plot: pg.PlotWidget) -> None:
    """Drop any previously-set bounds so a redraw starts from a clean slate."""
    vb = plot.getViewBox()
    vb.setLimits(xMin=None, xMax=None, yMin=None, yMax=None,
                 minXRange=None, maxXRange=None,
                 minYRange=None, maxYRange=None)
    # And the double-click target with them. The analysis panel draws a tornado
    # and a backtest into the same widget, so a stale home range would send a
    # double-click on the tornado back to wherever the backtest happened to sit.
    vb._home_range = None


def nums(a) -> np.ndarray:
    """Whatever was handed in, as a plain float array.

    pyqtgraph passes plotted arrays straight to `np.isfinite`, which raises on
    an `object` array — and it does so from inside a Qt callback, where PyQt
    aborts the process rather than raising into anything that could catch it.
    A pandas frame that came back from an empty SQL query is exactly that shape
    (`store._numeric` now stops it at source; this is the seatbelt), and so is
    a column of `Decimal`. One coercion at the boundary means a dtype surprise
    anywhere upstream degrades to a wrong-looking chart instead of a crash.
    """
    return np.asarray(a, dtype=float).ravel()


def line(plot: pg.PlotWidget, x, y, color: str, width: float = 2.0,
         name: str | None = None, dashed: bool = False):
    style = Qt.PenStyle.DashLine if dashed else Qt.PenStyle.SolidLine
    pen = pg.mkPen(color, width=width, style=style)
    return plot.plot(nums(x), nums(y), pen=pen, name=name)


def fan_parts(plot: pg.PlotWidget, x, pct: dict[int, np.ndarray], color: str,
              name: str | None = None):
    """P10–P90 + P25–P75 bands and a median line, returned separately.

    Handing back the three pieces is what lets the legend switch each one off
    independently — the outer band is often the first thing worth hiding when
    comparing two scenarios on one axis.
    """
    x = nums(x)
    c_outer = pg.PlotDataItem(x, nums(pct[10]))
    c_outer2 = pg.PlotDataItem(x, nums(pct[90]))
    c_inner = pg.PlotDataItem(x, nums(pct[25]))
    c_inner2 = pg.PlotDataItem(x, nums(pct[75]))
    outer = pg.FillBetweenItem(c_outer, c_outer2,
                               brush=pg.mkBrush(theme.qcolor(color, 34)))
    inner = pg.FillBetweenItem(c_inner, c_inner2,
                               brush=pg.mkBrush(theme.qcolor(color, 62)))
    plot.addItem(outer)
    plot.addItem(inner)
    med = line(plot, x, pct[50], color, width=2.2, name=name)
    return outer, inner, med


def fan(plot: pg.PlotWidget, x, pct: dict[int, np.ndarray], color: str,
        name: str | None = None):
    """P10–P90 + P25–P75 translucent bands with a solid median line."""
    return fan_parts(plot, x, pct, color, name)[2]


def today_line(plot: pg.PlotWidget, when: date | None = None):
    when = when or date.today()
    ln = pg.InfiniteLine(
        pos=to_ts([when])[0], angle=90,
        pen=pg.mkPen(theme.MUTED, width=1, style=Qt.PenStyle.DashLine),
        label="today", labelOpts={"color": theme.MUTED, "position": 0.94,
                                  "fill": theme.qcolor(theme.BG, 160)},
    )
    plot.addItem(ln)
    return ln


def target_line(plot: pg.PlotWidget, value: float, text: str):
    ln = pg.InfiniteLine(
        pos=value, angle=0,
        pen=pg.mkPen(theme.AMBER, width=1, style=Qt.PenStyle.DotLine),
        label=text, labelOpts={"color": theme.AMBER, "position": 0.72,
                               "fill": theme.qcolor(theme.BG, 160)},
    )
    plot.addItem(ln)
    return ln


def month_bars(plot: pg.PlotWidget, months: list, income: np.ndarray,
               spending: np.ndarray) -> None:
    x = to_ts(months)
    income, spending = nums(income), nums(spending)
    w = 86400 * 8.5
    plot.addItem(pg.BarGraphItem(x=x - w / 1.9, height=income, width=w,
                                 brush=pg.mkBrush(theme.qcolor(theme.ACCENT, 190)),
                                 pen=pg.mkPen(None), name="Income"))
    plot.addItem(pg.BarGraphItem(x=x + w / 1.9, height=spending, width=w,
                                 brush=pg.mkBrush(theme.qcolor(theme.RED, 170)),
                                 pen=pg.mkPen(None), name="Spending"))


def category_bars(plot: pg.PlotWidget, names: list[str], values: np.ndarray) -> None:
    """Horizontal bars, largest on top."""
    n = len(names)
    ys = np.arange(n)[::-1]
    values = nums(values)
    plot.addItem(pg.BarGraphItem(x0=0, y=ys, height=0.62, width=values,
                                 brush=pg.mkBrush(theme.qcolor(theme.BLUE, 175)),
                                 pen=pg.mkPen(None)))
    ax = plot.getAxis("left")
    ax.setTicks([[(float(y), nm) for y, nm in zip(ys, names)]])
    plot.getViewBox().setLimits(xMin=0)
    plot.setMouseEnabled(x=False, y=False)


# --- reference floors -------------------------------------------------------
# Three horizontal reference lines that give a money chart a sense of "how bad
# is bad". Zero is drawn hardest because crossing it is categorically different
# from any other move on the chart: below it you are borrowing, not saving.
FLOOR_ZERO_WIDTH = 2.6
# Label placement along the line, 0 = left edge, 1 = right edge. These used to
# sit at 0.03, which put them directly on top of the y-axis tick labels and let
# the plot's left margin clip them in half. Sitting them well to the right
# keeps them clear of the axis, and staggering the three floors stops them
# overlapping when the values happen to be close together.
_FLOOR_POS = {"zero": 0.90, "starter": 0.66, "emergency": 0.42}


def _floor_label_opts(color: str, slot: str) -> dict:
    return {
        "color": color,
        "position": _FLOOR_POS.get(slot, 0.85),
        "fill": theme.qcolor(theme.BG, 205),
        # anchor the text box by its bottom-centre so it rides above the line
        # instead of straddling it
        "anchors": [(0.5, 1.0), (0.5, 1.0)],
    }


def zero_floor(plot: pg.PlotWidget, label: str = "$0 · debt below this",
               slot: str = "zero"):
    """The hard floor. Thick, solid, red — the line not to cross."""
    ln = pg.InfiniteLine(
        pos=0.0, angle=0,
        pen=pg.mkPen(theme.RED, width=FLOOR_ZERO_WIDTH),
        label=label, labelOpts=_floor_label_opts(theme.RED, slot),
    )
    ln.setZValue(-5)          # behind the data, in front of the grid
    plot.addItem(ln)
    return ln


def soft_floor(plot: pg.PlotWidget, value: float, label: str,
               color: str | None = None, slot: str = "starter"):
    """A softer milestone — thin, dashed, muted."""
    if value is None or not np.isfinite(value) or value <= 0:
        return None
    col = color or theme.MUTED
    ln = pg.InfiniteLine(
        pos=float(value), angle=0,
        pen=pg.mkPen(col, width=1.2, style=Qt.PenStyle.DashLine),
        label=label, labelOpts=_floor_label_opts(col, slot),
    )
    ln.setZValue(-6)
    plot.addItem(ln)
    return ln


def money_floors(plot: pg.PlotWidget, starter: float = 1000.0,
                 emergency: float | None = None,
                 emergency_months: int = 3) -> None:
    """Standard floor set for any chart measuring cash or net worth."""
    zero_floor(plot)
    soft_floor(plot, starter, f"{_short(starter)} · starter buffer", theme.AMBER,
               slot="starter")
    if emergency and emergency > starter * 1.05:
        soft_floor(plot, emergency,
                   f"{_short(emergency)} · {emergency_months}-month emergency fund",
                   theme.ACCENT, slot="emergency")


def _short(v: float) -> str:
    av = abs(v)
    if av >= 1_000_000:
        return f"${v / 1_000_000:.1f}M"
    if av >= 10_000:
        return f"${v / 1000:.0f}k"
    if av >= 1000:
        return f"${v / 1000:.1f}k"
    return f"${v:.0f}"


def tornado(plot: pg.PlotWidget, levers: list[str], lows: np.ndarray,
            highs: np.ndarray) -> None:
    """Horizontal diverging bars around zero — biggest swing on top."""
    n = len(levers)
    ys = np.arange(n)[::-1]
    plot.addItem(pg.BarGraphItem(
        x0=np.minimum(lows, 0), x1=np.zeros(n), y=ys, height=0.55,
        brush=pg.mkBrush(theme.qcolor(theme.RED, 180)), pen=pg.mkPen(None)))
    plot.addItem(pg.BarGraphItem(
        x0=np.zeros(n), x1=np.maximum(highs, 0), y=ys, height=0.55,
        brush=pg.mkBrush(theme.qcolor(theme.ACCENT, 180)), pen=pg.mkPen(None)))
    plot.addItem(pg.InfiniteLine(pos=0.0, angle=90,
                                 pen=pg.mkPen(theme.BORDER, width=1.4)))
    plot.getAxis("left").setTicks([[(float(y), nm) for y, nm in zip(ys, levers)]])
    plot.setMouseEnabled(x=False, y=False)


def backtest_plot(plot: pg.PlotWidget, x, actual: np.ndarray,
                  pct: dict[int, np.ndarray]) -> None:
    """What the model said (band) against what happened (solid white)."""
    x = nums(x)
    lo = pg.PlotDataItem(x, nums(pct[10]))
    hi = pg.PlotDataItem(x, nums(pct[90]))
    lo2 = pg.PlotDataItem(x, nums(pct[25]))
    hi2 = pg.PlotDataItem(x, nums(pct[75]))
    plot.addItem(pg.FillBetweenItem(lo, hi,
                                    brush=pg.mkBrush(theme.qcolor(theme.BLUE, 34))))
    plot.addItem(pg.FillBetweenItem(lo2, hi2,
                                    brush=pg.mkBrush(theme.qcolor(theme.BLUE, 62))))
    line(plot, x, pct[50], theme.BLUE, 1.6, "Predicted · P50", dashed=True)
    line(plot, x, actual, theme.TEXT, 2.2, "What actually happened")
    zero_floor(plot)
