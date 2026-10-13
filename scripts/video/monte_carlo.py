"""Explain Doug's forecast through motion, without captions or narration.

Whole-week selections, balances, paths, and bands come from the application.
Only short scene titles, object labels, and chart legends are rendered as text.
"""
from __future__ import annotations

import json
from pathlib import Path
import sys

import numpy as np
from manim import (
    Scene, Text, VGroup, RoundedRectangle, Rectangle, Square, Circle, Line,
    Arrow, Dot, VMobject, Polygon, DashedLine, Brace, SurroundingRectangle,
    FadeIn, FadeOut, Create, Transform, TransformFromCopy, LaggedStart,
    MoveAlongPath, UP, DOWN, LEFT, RIGHT, config, linear,
)

sys.path.insert(0, str(Path(__file__).resolve().parent))
from example import build_example, ROOT, DAYS, PATHS, START

BG, PANEL, BORDER = "#101418", "#171D24", "#2A3340"
INK, MUTED = "#E8ECF1", "#8B98A9"
MINT, BLUE, AMBER, RED, PURPLE = "#35C4A0", "#5B9CE0", "#E0A83C", "#E06C5B", "#9B7EDE"
SPENDING_COLORS = (BLUE, PURPLE, AMBER)
CATEGORIES = ("Groceries", "Dining", "Misc")
WEEK_SCALE = 1500.0
config.background_color = BG


def words(value, size=26, color=INK, width=None, t2c=None):
    text = Text(str(value), font="DejaVu Sans", font_size=size,
                color=color, disable_ligatures=True, t2c=t2c or {})
    if width and text.width > width:
        text.scale_to_fit_width(width)
    return text


def money(value):
    return f"−${abs(value):,.0f}" if value < 0 else f"${value:,.0f}"


def week_tile(row, number, compact=False, dining=1.0, border=BORDER):
    """Retain the three spending components when a whole week is copied."""
    width, height = (1.42, 0.95) if compact else (4.2, 0.67)
    box = RoundedRectangle(width=width, height=height, corner_radius=0.07,
                           stroke_color=border, stroke_width=1.5,
                           fill_color=PANEL, fill_opacity=1)
    values = [float(row.get(category, 0)) * (dining if category == "Dining" else 1)
              for category in CATEGORIES]
    label = words(f"W{number}", 17 if compact else 20, MUTED)
    amount = words(money(sum(values)), 18 if compact else 20, INK)
    bars = VGroup()
    left = -0.60 if compact else -1.30
    for value, color in zip(values, SPENDING_COLORS):
        bar_width = max(0.025, value / WEEK_SCALE * (1.2 if compact else 2.4))
        bar = Rectangle(width=bar_width,
                        height=0.18 if compact else 0.24, stroke_width=0,
                        fill_color=color, fill_opacity=0.9)
        bar.move_to([left, 0, 0], aligned_edge=LEFT)
        bars.add(bar)
        left += bar_width
    if compact:
        label.move_to([0, 0.29, 0])
        amount.move_to([0, -0.29, 0])
    else:
        label.move_to([-1.98, 0, 0], aligned_edge=LEFT)
        amount.move_to([1.99, 0, 0], aligned_edge=RIGHT)
    return VGroup(box, label, bars, amount)


def cash_icon(color):
    return VGroup(Rectangle(width=0.65, height=0.36, stroke_color=color,
                           fill_color=color, fill_opacity=0.1),
                  Circle(radius=0.1, color=color), words("$", 15, color))


class Plot:
    def __init__(self, days=DAYS, low=0, high=18000, left=-5.1, right=5.75,
                 bottom=-2.35, top=1.75, label="Cash", history=True):
        self.days, self.low, self.high = days, low, high
        self.left, self.right, self.bottom, self.top = left, right, bottom, top
        self.history_left = -5.85 if history else left
        self.objects = VGroup()
        ticks = (range(-4000, 6001, 2000) if low < 0 else
                 range(0, int(high) + 1, 6000 if high >= 18000 else 4000))
        for value in ticks:
            y = self.point(0, value)[1]
            grid = Line([self.history_left, y, 0], [right, y, 0],
                        color=BORDER, stroke_width=0.8)
            self.objects.add(grid, words(money(value), 18, MUTED).next_to(grid, LEFT, buff=0.1))
        self.objects.add(Line([self.history_left, bottom, 0], [right, bottom, 0],
                              color=MUTED, stroke_width=1))
        times = ((0, "Today"), (21, "3 weeks"), (35, "5 weeks")) if days == 35 else (
            (0, "Today"), (DAYS / 2, "6 months"), (DAYS, "1 year"))
        for day, label_text in times:
            self.objects.add(words(label_text, 20, MUTED).move_to(
                [self.point(day, low)[0], bottom - 0.3, 0]))
        if history:
            self.objects.add(DashedLine(self.point(0, low), self.point(0, high),
                                        color=MUTED, stroke_width=1))
        self.objects.add(words(label, 24, MINT if label == "Net worth" else BLUE).move_to(
            [self.history_left + 0.5, top + 0.35, 0]))

    def point(self, day, amount):
        x = (self.left + day / self.days * (self.right - self.left) if day >= 0
             else self.left + day / 120 * (self.left - self.history_left))
        return np.array([x, self.bottom + (amount - self.low) / (self.high - self.low)
                         * (self.top - self.bottom), 0.0])

    def curve(self, values, color=BLUE, opacity=1, width=2.6, start=0):
        return VMobject(stroke_color=color, stroke_width=width, stroke_opacity=opacity).set_points_as_corners(
            [self.point(i + start, value) for i, value in enumerate(values)])

    def past(self, history):
        return self.curve(history.cash.values, MUTED, 0.7, 1.5,
                          start=(history.index[0].date() - START).days)

    def band(self, lower, upper, opacity, color=BLUE):
        points = [self.point(i, value) for i, value in enumerate(upper)]
        points += [self.point(i, lower[i]) for i in range(len(lower) - 1, -1, -1)]
        return Polygon(*points, stroke_width=0, fill_color=color, fill_opacity=opacity)

    def fan(self, pct, color=BLUE):
        # These alpha values match ui.charts.fan_parts.
        return VGroup(self.band(pct[10], pct[90], 34 / 255, color),
                      self.band(pct[25], pct[75], 62 / 255, color),
                      self.curve(pct[50], color))


class MonteCarloExplainer(Scene):
    def construct(self):
        global WEEK_SCALE
        self.example = build_example()
        WEEK_SCALE = float(self.example.weeks.sum(axis=1).max()) * 1.05
        self.chapters = []
        self.path_a = next(i for i, choices in enumerate(self.example.week_picks)
                           if len(set(choices[:5])) in (3, 4)
                           and choices[0] != min(choices[:5]))
        self.path_b = 0 if self.path_a != 0 else 1
        self.scheduled_money()
        self.spending_weeks()
        self.one_future()
        self.another_future()
        self.many_futures()
        self.form_bands()
        self.try_change()
        self.cash_dip()
        self.forecast_view()
        self.real_history_close()
        out = ROOT / "build" / "video"
        out.mkdir(parents=True, exist_ok=True)
        (out / "timeline.json").write_text(json.dumps({
            "duration": self.time, "chapters": self.chapters,
            "path_a": self.path_a, "path_b": self.path_b,
            "week_draws_a": self.example.week_picks[self.path_a, :5].tolist(),
            "week_draws_b": self.example.week_picks[self.path_b, :5].tolist(),
            "captions": []}, indent=2), encoding="utf-8")

    def section(self, title, carry=()):
        ids = {id(member) for item in carry for member in item.get_family()}
        disposable = [item for item in self.mobjects
                      if not any(id(member) in ids for member in item.get_family())]
        if disposable:
            self.play(*[FadeOut(item) for item in disposable], run_time=0.45)
        self.clear()
        self.add(*carry)
        self.chapters.append({"start": self.time, "title": title})
        self.add(words("Example data", 16, MUTED).to_corner(DOWN + LEFT, buff=0.28))
        self.play(FadeIn(words(title, 34, INK, 12).move_to([0, 3.22, 0])), run_time=0.45)

    def scheduled_money(self):
        self.section("Paydays & bills")
        plot = Plot(days=35, high=12000)
        past = plot.past(self.example.history)
        start = Dot(plot.point(0, 6000), color=INK, radius=0.065)
        balance = words("$6,000", 25, INK).next_to(start, UP, buff=0.18)
        self.play(FadeIn(plot.objects), Create(past), FadeIn(start), FadeIn(balance), run_time=1.8)
        self.wait(1)
        flows = {}
        for flow in self.example.schedule:
            day = (flow.when - START).days
            if day <= 35:
                flows.setdefault(day, []).append(flow)
        previous = 0
        for day, items in flows.items():
            total = sum(item.amount for item in items)
            color = MINT if total > 0 else RED
            label = " + ".join("Investing" if item.source == "investment" else item.label
                               for item in items)
            icon = cash_icon(color).move_to([0, 2.15, 0])
            tag = words(label, 23, color, 3.6).next_to(icon, LEFT, buff=0.25)
            amount = words(("+" if total > 0 else "−") + money(abs(total)), 25, color).next_to(icon, RIGHT)
            chip = VGroup(tag, icon, amount)
            segment = plot.curve(self.example.fixed_cash[previous:day + 1], AMBER, start=previous)
            destination = plot.point(day, self.example.fixed_cash[day])
            arrow = Arrow(icon.get_bottom(), destination, color=color, stroke_width=1.5, buff=0.18)
            self.play(FadeIn(chip), Create(arrow), run_time=0.45)
            self.play(Create(segment), start.animate.move_to(destination),
                      Transform(balance, words(money(self.example.fixed_cash[day]), 25).move_to(destination + UP * 0.32)),
                      run_time=1.1)
            self.wait(0.45)
            self.play(FadeOut(chip), FadeOut(arrow), run_time=0.25)
            previous = day
        self.wait(1)

    def spending_weeks(self):
        self.section("Your spending weeks")
        picks = self.example.week_picks[self.path_a, :5]
        source_ids = sorted(set(picks))
        rows = VGroup(*[week_tile(self.example.weeks.iloc[i], i + 1) for i in source_ids])
        rows.arrange(DOWN, buff=0.24).move_to([-4.4, 0.15, 0])
        heading = words("Your history", 26, MINT).move_to([-4.4, 2.05, 0])
        legend = VGroup(*[VGroup(Square(side_length=0.12, stroke_width=0, fill_color=c, fill_opacity=1),
                                  words(name, 16, MUTED)).arrange(RIGHT, buff=0.08)
                          for name, c in zip(("Groceries", "Dining", "Other"), SPENDING_COLORS)])
        legend.arrange(RIGHT, buff=0.2).move_to([-4.4, -2.35, 0])
        more = words(f"{len(self.example.weeks)} weeks", 18, MUTED).move_to([-4.4, -1.97, 0])
        self.play(FadeIn(heading), FadeIn(legend), FadeIn(more),
                  LaggedStart(*[FadeIn(row) for row in rows], lag_ratio=0.15), run_time=1.3)
        self.wait(1.3)
        targets = VGroup(*[week_tile(self.example.weeks.iloc[i], i + 1, compact=True, border=AMBER)
                           for i in picks])
        targets.arrange(RIGHT, buff=0.14).move_to([2.35, 0.65, 0])
        self.add(words("One future", 26, AMBER).move_to([2.35, 2.05, 0]))
        for n, index in enumerate(picks):
            source = rows[source_ids.index(index)]
            highlight = SurroundingRectangle(source, color=AMBER, buff=0.07, corner_radius=0.08)
            self.play(Create(highlight), run_time=0.25)
            self.play(TransformFromCopy(source, targets[n]), run_time=0.9)
            self.wait(0.45)
            self.play(FadeOut(highlight), run_time=0.2)
        repeated = next(i for i in source_ids if list(picks).count(i) > 1)
        copies = [SurroundingRectangle(targets[j], color=MINT, buff=0.05)
                  for j, i in enumerate(picks) if i == repeated]
        self.play(*[Create(item) for item in copies], run_time=0.4)
        self.wait(1.4)
        self.play(*[FadeOut(item) for item in copies], run_time=0.3)
        self.sequence_a = targets

    def one_future(self):
        self.section("One possible future", carry=(self.sequence_a,))
        plot = Plot(days=35, high=12000, top=0.8)
        past = plot.past(self.example.history)
        destinations = [self.sequence_a[i].copy().move_to([plot.point(i * 7 + 3.5, 0)[0], 2.04, 0])
                        for i in range(5)]
        self.play(*[Transform(self.sequence_a[i], destinations[i]) for i in range(5)],
                  FadeIn(plot.objects), Create(past), run_time=1.1)
        raw = self.example.cash_paths[self.path_a]
        dot = Dot(plot.point(0, raw[0]), color=AMBER, radius=0.055)
        self.add(dot)
        pieces = VGroup()
        for i in range(5):
            first, last = i * 7, (i + 1) * 7
            piece = plot.curve(raw[first:last + 1], AMBER, start=first)
            glow = SurroundingRectangle(self.sequence_a[i], color=AMBER, buff=0.05)
            connector = Arrow(self.sequence_a[i].get_bottom(), plot.point(first + 3.5, raw[first + 3]),
                              color=AMBER, stroke_width=1, buff=0.15)
            self.play(Create(glow), Create(connector), run_time=0.3)
            self.play(Create(piece), MoveAlongPath(dot, piece, rate_func=linear), run_time=1.0)
            self.wait(0.5)
            self.play(FadeOut(glow), FadeOut(connector), run_time=0.2)
            pieces.add(piece)
        self.remove(*pieces, dot)
        self.curve_a = plot.curve(raw[:36], AMBER)
        self.add(self.curve_a)
        self.small_plot, self.past = plot, past
        self.wait(1.2)

    def another_future(self):
        plot = self.small_plot
        self.section("Another draw, another future", carry=(plot.objects, self.past, self.curve_a))
        picks = self.example.week_picks[self.path_b, :5]
        row = VGroup(*[week_tile(self.example.weeks.iloc[index], index + 1, compact=True, border=MINT)
                       .move_to([plot.point(i * 7 + 3.5, 0)[0], 2.04, 0]) for i, index in enumerate(picks)])
        raw = self.example.cash_paths[self.path_b]
        pieces = VGroup()
        dot = Dot(plot.point(0, raw[0]), color=MINT, radius=0.055)
        self.add(dot)
        for i in range(5):
            first, last = i * 7, (i + 1) * 7
            piece = plot.curve(raw[first:last + 1], MINT, start=first)
            self.play(FadeIn(row[i], shift=DOWN * 0.12), run_time=0.35)
            self.play(Create(piece), MoveAlongPath(dot, piece, rate_func=linear), run_time=0.7)
            self.wait(0.35)
            pieces.add(piece)
        self.remove(*pieces, dot)
        self.curve_b = plot.curve(raw[:36], MINT)
        self.add(self.curve_b)
        self.wait(1.3)

    def many_futures(self):
        self.section("Many possible futures", carry=(self.small_plot.objects, self.past, self.curve_a, self.curve_b))
        plot = Plot()
        a, b = self.example.cash_paths[[self.path_a, self.path_b]]
        self.play(Transform(self.small_plot.objects, plot.objects),
                  Transform(self.past, plot.past(self.example.history)),
                  Transform(self.curve_a, plot.curve(a[:36], AMBER)),
                  Transform(self.curve_b, plot.curve(b[:36], MINT)), run_time=1.4)
        self.play(Create(plot.curve(a[35:], AMBER, start=35)),
                  Create(plot.curve(b[35:], MINT, start=35)), run_time=1.8)
        curves = VGroup(*[plot.curve(path, BLUE, 0.2, 1.15)
                          for path in self.example.cash_paths[::20]])
        self.play(LaggedStart(*[Create(curve) for curve in curves], lag_ratio=0.025), run_time=3)
        self.play(FadeIn(words("1,000 simulated · 50 shown", 22, MUTED).move_to([3.0, 2.45, 0])), run_time=0.5)
        self.wait(2)

    def dots_at(self, plot, day, central=None):
        balances = np.sort(self.example.cash_paths[:, day])
        dots = VGroup()
        for rank, balance in enumerate(balances):
            color, opacity = BLUE, 0.8
            if central:
                low, high = central
                if not low <= rank < high:
                    color, opacity = MUTED, 0.2
                elif 250 <= rank < 750:
                    opacity = 1
            x = 4.75 + (rank % 20 - 9.5) * 0.034
            dots.add(Dot([x, plot.point(day, balance)[1], 0], radius=0.013,
                         color=color, fill_opacity=opacity, stroke_width=0))
        return dots

    def bracket(self, plot, lower, upper, text, color=BLUE):
        line = Line([5.45, plot.point(0, lower)[1], 0], [5.45, plot.point(0, upper)[1], 0])
        brace = Brace(line, RIGHT, color=color, buff=0.1)
        return VGroup(brace, words(text, 24, color).next_to(brace, RIGHT, buff=0.15))

    def form_bands(self):
        self.section("Futures become a range")
        plot = Plot(right=2.85)
        past = plot.past(self.example.history)
        curves = VGroup(*[plot.curve(path, BLUE, 0.1, 1)
                          for path in self.example.cash_paths[::20]])
        self.add(plot.objects, past, curves)
        day = DAYS
        pct = self.example.baseline.cash_pct
        cursor = DashedLine(plot.point(day, plot.low), plot.point(day, plot.high),
                            color=INK, stroke_width=1)
        dots = self.dots_at(plot, day)
        label = words("At this date", 24, MUTED).move_to([4.75, 2.15, 0])
        arrow = Arrow(plot.point(day, pct[50][day]) + RIGHT * 0.1,
                      [4.25, plot.point(day, pct[50][day])[1], 0], color=MUTED, buff=0.05)
        self.play(Create(cursor), Create(arrow), FadeIn(label), FadeIn(dots), run_time=1.2)
        self.wait(1)
        vals = self.example.cash_paths[:, day]
        halves = VGroup(self.bracket(plot, vals.min(), pct[50][day], "50%"),
                        self.bracket(plot, pct[50][day], vals.max(), "50%"))
        point = Dot(plot.point(day, pct[50][day]), color=INK, radius=0.055)
        median = plot.curve(pct[50], BLUE, width=3.2)
        tag = words("Median", 23, BLUE).move_to([0, 2.16, 0])
        self.play(FadeIn(halves), FadeIn(point), Create(median), FadeIn(tag), run_time=1.5)
        self.wait(2)
        outer = plot.band(pct[10], pct[90], 34 / 255)
        eighty = VGroup(self.bracket(plot, vals.min(), pct[10][day], "10%", MUTED),
                        self.bracket(plot, pct[10][day], pct[90][day], "80%"),
                        self.bracket(plot, pct[90][day], vals.max(), "10%", MUTED))
        self.play(FadeOut(halves), Transform(dots, self.dots_at(plot, day, (100, 900))),
                  FadeIn(outer), FadeIn(eighty), run_time=1.2)
        self.wait(2)
        inner = plot.band(pct[25], pct[75], 62 / 255)
        fifty = self.bracket(plot, pct[25][day], pct[75][day], "50%")
        self.play(FadeOut(eighty), FadeIn(inner), FadeIn(fifty), run_time=0.8)
        self.wait(1.8)
        self.play(FadeOut(fifty), FadeOut(arrow), run_time=0.5)
        for day in (90, 180, 365):
            self.play(Transform(cursor, DashedLine(plot.point(day, plot.low), plot.point(day, plot.high),
                                                  color=INK, stroke_width=1)),
                      Transform(point, Dot(plot.point(day, pct[50][day]), color=INK, radius=0.055)),
                      Transform(dots, self.dots_at(plot, day, (100, 900))), run_time=1.2)
            self.wait(0.8)
        self.wait(1)

    def try_change(self):
        self.section("Try a change")
        plot = Plot(right=2.85)
        before = plot.fan(self.example.baseline.cash_pct)
        self.add(plot.objects, plot.past(self.example.history), before)
        sample = self.example.weeks.iloc[self.example.week_picks[self.path_a, 0]]
        number = int(self.example.week_picks[self.path_a, 0]) + 1
        tile = week_tile(sample, number).scale(0.8).move_to([4.72, 1.0, 0])
        track = Line([3.4, 0, 0], [6.15, 0, 0], color=BORDER, stroke_width=5)
        knob = Dot([6.15, 0, 0], color=PURPLE, radius=0.085)
        title = words("Dining", 27, PURPLE).move_to([4.72, 2.13, 0])
        change = words("−25%", 31, PURPLE).move_to([4.72, -0.55, 0])
        self.play(FadeIn(tile), FadeIn(track), FadeIn(knob), FadeIn(title), run_time=0.6)
        self.wait(1)
        new_tile = week_tile(sample, number, dining=0.75).scale(0.8).move_to(tile)
        after = plot.fan(self.example.scenario.cash_pct, MINT)
        self.play(knob.animate.move_to([3.4 + 0.75 * 2.75, 0, 0]),
                  Transform(tile, new_tile), FadeIn(change), run_time=1.1)
        self.play(FadeIn(after[0]), FadeIn(after[1]), Create(after[2]), run_time=2)
        delta = self.example.scenario.cash_pct[50][-1] - self.example.baseline.cash_pct[50][-1]
        result = words("+" + money(delta), 30, MINT).move_to([4.72, -1.7, 0])
        result_label = words("Median", 18, MUTED).next_to(result, UP, buff=0.15)
        self.play(FadeIn(result), FadeIn(result_label), run_time=0.6)
        self.wait(2.5)

    def cash_dip(self):
        self.section("Cash dips")
        plot = Plot(low=-4000, high=6000, right=5.6)
        pct = self.example.squeeze.cash_pct
        fan = plot.fan(pct)
        zero = Line(plot.point(0, 0), plot.point(DAYS, 0), color=RED, stroke_width=2)
        self.add(plot.objects)
        self.play(FadeIn(fan), Create(zero), run_time=1)
        day = 80
        expense = words("−$3,400", 29, RED).move_to([plot.point(day, 0)[0], 2.15, 0])
        impulse = Arrow(expense.get_bottom(), plot.point(day, pct[50][day]),
                        color=RED, stroke_width=2, buff=0.15)
        self.play(FadeIn(expense), Create(impulse), run_time=0.7)
        self.wait(1)
        low = int(np.argmin(pct[50]))
        marker = Dot(plot.point(0, pct[50][0]), color=BLUE, radius=0.075)
        self.add(marker)
        first = plot.curve(pct[50][:low + 1])
        second = plot.curve(pct[50][low:], start=low)
        self.play(MoveAlongPath(marker, first, rate_func=linear), run_time=2)
        self.play(marker.animate.set_color(RED), run_time=0.25)
        depth = Line(plot.point(low, 0), plot.point(low, pct[50][low]), color=RED, stroke_width=4)
        low_tag = words(money(pct[50][low]), 24, RED).next_to(marker, DOWN, buff=0.1)
        self.play(Create(depth), FadeIn(low_tag), run_time=0.5)
        self.wait(1.2)
        self.play(FadeOut(low_tag), FadeOut(depth), MoveAlongPath(marker, second, rate_func=linear), run_time=2.7)
        self.play(marker.animate.set_color(MINT), run_time=0.35)
        self.play(FadeIn(words(money(pct[50][-1]), 24, MINT).next_to(marker, UP, buff=0.18)), run_time=0.4)
        self.wait(1.7)

    def forecast_view(self):
        self.section("Your forecast")
        cash = Plot(left=-4.4, right=5.7, bottom=0.5, top=2.2, history=False)
        networth = Plot(left=-4.4, right=5.7, low=0, high=24000,
                        bottom=-2.45, top=-0.7, history=False, label="Net worth")
        cash_fan = cash.fan(self.example.baseline.cash_pct)
        worth_fan = networth.fan(self.example.baseline.networth_pct, MINT)
        self.play(FadeIn(cash.objects), FadeIn(cash_fan), run_time=0.8)
        self.play(FadeIn(networth.objects), FadeIn(worth_fan), run_time=0.8)
        self.wait(3.5)

    def real_history_close(self):
        self.section("Built from your real history")
        lines = VGroup(
            words("The band isn't a guess.", 38),
            words("It's built from snippets of YOUR real", 30, t2c={"YOUR": MINT}),
            words("paychecks, bills, and spending weeks,", 30,
                  t2c={"paychecks": MINT, "bills": BLUE, "spending weeks": PURPLE}),
            words("replayed into a thousand possible futures.", 30),
        ).arrange(DOWN, buff=0.24).move_to([0, 0.75, 0])
        question = words("What if those good and hard weeks happened again?",
                         24, MUTED).move_to([0, -1.3, 0])
        self.play(FadeIn(lines), run_time=0.9)
        self.wait(5)
        self.play(FadeIn(question), run_time=0.7)
        self.wait(4)
