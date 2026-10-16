"""Chart helpers built on pyqtgraph.

The forecast fan is the app's signature element: a translucent P10–P90 band,
a stronger P25–P75 band, and a solid median — history joins it in a different
hue at a dashed "today" line.
"""
from __future__ import annotations

from datetime import date
from html import escape

import numpy as np
import pandas as pd
import pyqtgraph as pg
from PyQt6.QtCore import QEvent, QObject, Qt
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
        return [_short(v) for v in values]


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
    w.showGrid(x=True, y=True, alpha=0.16)
    w.getPlotItem().hideButtons()
    w.getPlotItem().setMenuEnabled(False)
    w.getViewBox().setMouseMode(pg.ViewBox.PanMode)
    tick_font = QFont()
    tick_font.setPointSize(9)
    for side in ("left", "bottom"):
        ax = w.getAxis(side)
        ax.setTickFont(tick_font)
        ax.setPen(pg.mkPen(theme.BORDER))
        ax.setTextPen(pg.mkPen(theme.MUTED))
    if legend:
        lg = w.addLegend(offset=(8, 4), labelTextSize="9pt")
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
    vb._window_ctx = {"x_lo": x_lo, "x_hi": frame_x_hi, "y_series": y_series,
                      "x_data": x_data, "include_y": include_y, "pad_y": pad_y,
                      "zoom_y": zoom_y}
    vb.setRange(xRange=(view_x_lo, frame_x_hi), yRange=(view_y_lo, view_y_hi),
                padding=0)


def reframe(plot: pg.PlotWidget, y_series, include_y, days: float | None) -> None:
    """Refit a locked date chart's vertical range to the series being shown.

    The opening frame is computed from every layer, so a deep debt line or a
    far-off reference floor stretches the axis and flattens the series people
    actually read for trends. When a layer is hidden the frame should follow
    the layers that are left, which is what this does.
    """
    ctx = getattr(plot.getViewBox(), "_window_ctx", None)
    if not ctx:
        return
    ctx["y_series"] = y_series
    ctx["include_y"] = include_y
    show_window(plot, days)


def show_window(plot: pg.PlotWidget, days: float | None) -> None:
    """Reframe a locked date chart on its trailing ``days`` (None = all of it).

    This is what the 3 months / 1 year / All buttons call. The y-range is
    refitted to the points that are actually in the window, so a short window
    on a long ledger shows its own detail instead of a flat line squeezed into
    the scale of three years.
    """
    vb = plot.getViewBox()
    ctx = getattr(vb, "_window_ctx", None)
    if not ctx:
        return
    x_lo, x_hi = ctx["x_lo"], ctx["x_hi"]
    view_lo = x_lo if not days else max(x_lo, x_hi - float(days) * DAY)
    frac = min(1.0, (x_hi - view_lo) / max(x_hi - x_lo, DAY))
    y_lo = y_hi = None
    if ctx["zoom_y"]:
        tail = _tail(ctx["y_series"], ctx["x_data"], frac)
        if tail.size:
            y_lo, y_hi = _y_bounds(tail, ctx["include_y"], ctx["pad_y"])
    if y_lo is None or y_hi - y_lo < 1.0:
        data = _finite(ctx["y_series"])
        if data.size == 0:
            return
        y_lo, y_hi = _y_bounds(data, ctx["include_y"], ctx["pad_y"])
    vb._home_range = ((view_lo, x_hi), (y_lo, y_hi))
    vb.setRange(xRange=(view_lo, x_hi), yRange=(y_lo, y_hi), padding=0)


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
         name: str | None = None, dashed: bool | str = False):
    """A line series. ``dashed`` may be True (dashes) or "dot" (dots), so two
    series of similar hue can still be told apart without relying on colour."""
    style = (Qt.PenStyle.DotLine if dashed == "dot" else
             Qt.PenStyle.DashLine if dashed else Qt.PenStyle.SolidLine)
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


def data_end_line(plot: pg.PlotWidget, when: date, text: str = "last transaction"):
    """Where the imported data stops, so a flat run after it reads as 'no data
    yet' rather than 'nothing happened'."""
    ln = pg.InfiniteLine(
        pos=to_ts([when])[0], angle=90,
        pen=pg.mkPen(theme.AMBER, width=1, style=Qt.PenStyle.DotLine),
        label=text, labelOpts={"color": theme.AMBER, "position": 0.14,
                               "fill": theme.qcolor(theme.BG, 200),
                               "anchors": [(1.0, 1.0), (1.0, 1.0)]},
    )
    ln.setZValue(3)
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


def on_bar_click(plot: pg.PlotWidget, names: list[str], callback) -> None:
    """Call ``callback(name)`` when a horizontal category bar is clicked."""
    old = getattr(plot, "_bar_click", None)
    if old is not None:
        try:
            plot.scene().sigMouseClicked.disconnect(old)
        except (TypeError, RuntimeError):
            pass
    n = len(names)
    vb = plot.getViewBox()

    def handler(ev) -> None:
        if ev.button() != Qt.MouseButton.LeftButton:
            return
        if not vb.sceneBoundingRect().contains(ev.scenePos()):
            return
        y = vb.mapSceneToView(ev.scenePos()).y()
        i = n - 1 - int(round(y))
        if 0 <= i < n and abs(y - round(y)) <= 0.45:
            callback(names[i])

    plot._bar_click = handler
    plot.scene().sigMouseClicked.connect(handler)
    plot.setCursor(Qt.CursorShape.PointingHandCursor)
    plot.setToolTip("Click a bar to see its transactions")


# --- hover readout ----------------------------------------------------------
class _Readout(QObject):
    """A vertical guide and a small box with the exact values under the mouse.

    Every chart was drawn but none could be read: the numbers behind a line
    were visible nowhere. The box lists each series at the point nearest the
    mouse, in the series' own colour, and disappears when the mouse leaves.
    """

    def __init__(self, plot: pg.PlotWidget, x, series, label, tolerance):
        super().__init__(plot)
        self.plot = plot
        self.x = nums(x)
        self.series = [(n, c, nums(y)) for n, c, y in series]
        self.label = label
        self.tolerance = tolerance
        self.vline = pg.InfiniteLine(
            angle=90, movable=False,
            pen=pg.mkPen(theme.MUTED, width=1))
        self.vline.setZValue(6)
        self.text = pg.TextItem(
            anchor=(0, 0), fill=theme.qcolor(theme.SURFACE, 240),
            border=pg.mkPen(theme.BORDER_STRONG))
        self.text.setZValue(7)
        for it in (self.vline, self.text):
            plot.addItem(it, ignoreBounds=True)
            it.setVisible(False)
        self.proxy = pg.SignalProxy(plot.scene().sigMouseMoved,
                                    rateLimit=60, slot=self._moved)
        plot.viewport().installEventFilter(self)

    def detach(self) -> None:
        try:
            self.proxy.disconnect()
        except (TypeError, RuntimeError):
            pass
        try:
            self.plot.viewport().removeEventFilter(self)
        except RuntimeError:
            pass

    def eventFilter(self, obj, ev) -> bool:
        if ev.type() == QEvent.Type.Leave:
            self._hide()
        return False

    def _hide(self) -> None:
        for it in (self.vline, self.text):
            it.setVisible(False)

    def _moved(self, evt) -> None:
        pos = evt[0]
        vb = self.plot.getViewBox()
        if self.x.size == 0 or not vb.sceneBoundingRect().contains(pos):
            self._hide()
            return
        mouse = vb.mapSceneToView(pos)
        i = int(np.clip(np.searchsorted(self.x, mouse.x()), 0, self.x.size - 1))
        if i > 0 and abs(self.x[i - 1] - mouse.x()) < abs(self.x[i] - mouse.x()):
            i -= 1
        if self.tolerance is not None and abs(self.x[i] - mouse.x()) > self.tolerance:
            self._hide()
            return
        when = self.label(self.x[i])
        rows = [f"<div style='color:{theme.MUTED};font-size:12px'>{when}</div>"]
        for name, color, y in self.series:
            if i < y.size and np.isfinite(y[i]):
                rows.append(
                    f"<div style='font-size:12px'><span style='color:{color}'>"
                    f"&#9632;</span> {escape(str(name))} "
                    f"<b style='color:{theme.TEXT}'>{_precise(y[i])}</b></div>")
        self.text.setHtml("".join(rows))
        self.vline.setPos(float(self.x[i]))
        (x0, x1), (y0, y1) = vb.viewRange()
        right_half = self.x[i] > (x0 + x1) / 2
        self.text.setAnchor((1, 0) if right_half else (0, 0))
        self.text.setPos(float(self.x[i]), y1)
        for it in (self.vline, self.text):
            it.setVisible(True)


def _precise(v: float) -> str:
    return f"-${abs(v):,.0f}" if v < 0 and round(abs(v)) else f"${abs(v):,.0f}"


def detach_readout(plot):
    """Detach hover data when a chart is cleared, including empty selections."""
    old = getattr(plot, "_readout", None)
    if old is not None:
        old.detach()
        plot._readout = None


def attach_readout(plot: pg.PlotWidget, x, series, label=None,
                   tolerance: float | None = None) -> None:
    """Show exact values on hover. ``series`` is a list of (name, colour, y).

    Call after ``plot.clear()`` and the drawing; a previous readout on the same
    plot is detached first so redraws do not stack listeners.
    """
    old = getattr(plot, "_readout", None)
    if old is not None:
        old.detach()
    fmt = label or (lambda ts: pd.Timestamp(ts, unit="s").strftime("%b ") +
                    str(pd.Timestamp(ts, unit="s").day) +
                    pd.Timestamp(ts, unit="s").strftime(", %Y"))
    plot._readout = _Readout(plot, x, series, fmt, tolerance)


# --- reference floors -------------------------------------------------------
# Three horizontal reference lines that give a money chart a sense of "how bad
# is bad". Zero is drawn hardest because crossing it is categorically different
# from any other move on the chart: below it you are borrowing, not saving.
FLOOR_ZERO_WIDTH = 1.6
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
    """The hard floor: solid, and neutral grey-white rather than red, because
    red is the debt line itself and the two sit together near zero."""
    ln = pg.InfiniteLine(
        pos=0.0, angle=0,
        pen=pg.mkPen(theme.REFERENCE, width=FLOOR_ZERO_WIDTH),
        label=label, labelOpts=_floor_label_opts(theme.REFERENCE, slot),
    )
    # Above the data, not behind it: a label is a child of its line, so it
    # stacks wherever the line does, and behind the data it was struck through
    # by every series that crossed it. The lines are thin enough to sit on top.
    ln.setZValue(2)
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
    ln.setZValue(1)
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
    """$1.2k, -$10k: the sign leads, as it does everywhere else in the app."""
    av = abs(v)
    sign = "-" if v < 0 and round(av) != 0 else ""
    if av >= 1_000_000:
        return f"{sign}${av / 1_000_000:.1f}M"
    if av >= 10_000:
        return f"{sign}${av / 1000:.0f}k"
    if av >= 1000:
        return f"{sign}${av / 1000:.1f}k"
    return f"{sign}${av:.0f}"


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
