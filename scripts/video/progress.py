"""Explain Track record to a nontechnical adult, using fictional app results.

The short explanation keeps time for reading by focusing on the results.
Detailed statistical procedures and historical reconstruction are omitted.
"""
from __future__ import annotations

import json
from pathlib import Path
import sys

import numpy as np
from manim import (
    Scene, Text, VGroup, RoundedRectangle, Rectangle, Line, Arrow, Dot,
    VMobject, Polygon, DashedLine, DashedVMobject,
    FadeIn, FadeOut, Create, LaggedStart,
    DOWN, LEFT, RIGHT, linear,
)

sys.path.insert(0, str(Path(__file__).resolve().parent))
from monte_carlo import words, money, PANEL, BORDER, INK, MUTED, MINT, BLUE, AMBER
from progress_example import build_progress_example, ROOT, OLD, NOW
from finfore.progress import MIN_WEEKS

MAX_DURATION_SECONDS = 120


def panel(width, height, color=BORDER):
    return RoundedRectangle(
        width=width, height=height, corner_radius=.12,
        fill_color=PANEL, fill_opacity=1, stroke_color=color, stroke_width=1.5,
    )


def card(title, value, explanation, color=BLUE, width=5.8, height=2.6):
    """Place every label relative to the card, without stacking over a value."""
    return VGroup(
        panel(width, height, color),
        words(title, 25, color, width=width-.5).move_to([0, height/2-.42, 0]),
        words(value, 37, INK, width=width-.5).move_to([0, .05, 0]),
        words(explanation, 23, MUTED, width=width-.5).move_to([0, -height/2+.46, 0]),
    )


def week_chip(amount, color=BLUE):
    return VGroup(panel(1.08, .55, color), words(money(amount), 21, INK, width=.94))


def week_grid(values, color, x, y=.55):
    return VGroup(*[week_chip(v, color) for v in values]).arrange_in_grid(
        rows=3, cols=4, buff=.16,
    ).move_to([x, y, 0])


class CashPlot:
    def __init__(self, example):
        self.frame = example.old.forecast_frame()
        self.actual = example.actual.to_numpy()
        lo = min(self.frame.p10.min(), self.actual.min())
        hi = max(self.frame.p90.max(), self.actual.max())
        self.low = np.floor((lo-600)/2000)*2000
        self.high = np.ceil((hi+600)/2000)*2000
        self.n = len(self.frame)
        self.axes = VGroup()
        for value in np.linspace(self.low, self.high, 5):
            y = self.point(0, value)[1]
            self.axes.add(
                Line([-5.25, y, 0], [5.35, y, 0], color=BORDER, stroke_width=1),
                words(money(value), 21, MUTED).move_to([-6.02, y, 0]),
            )
        self.axes.add(
            words(OLD.strftime('%b %d'), 23, MUTED).move_to([-5.25, -2.48, 0]),
            words(NOW.strftime('%b %d'), 23, MUTED).move_to([5.35, -2.48, 0]),
        )
        self.outer = self.band(10, 90, 34/255)
        self.inner = self.band(25, 75, 62/255)
        self.median = DashedVMobject(self.curve(self.frame.p50, BLUE, 2), num_dashes=70)
        self.recorded = self.curve(self.actual, INK, 3)
        self.fan = VGroup(self.outer, self.inner, self.median)

    def point(self, day, value):
        return np.array([
            -5.25+10.6*day/(self.n-1),
            -2.0+3.65*(value-self.low)/(self.high-self.low), 0,
        ])

    def curve(self, values, color, width):
        return VMobject().set_points_as_corners([
            self.point(i, float(v)) for i, v in enumerate(values)
        ]).set_stroke(color, width)

    def band(self, lower, upper, alpha):
        a = [self.point(i, float(v)) for i, v in enumerate(self.frame[f'p{lower}'])]
        b = [self.point(i, float(v)) for i, v in enumerate(self.frame[f'p{upper}'])]
        return Polygon(*(a+b[::-1]), stroke_width=0, fill_color=BLUE, fill_opacity=alpha)


class ProgressExplainer(Scene):
    def section(self, title, explanation):
        if self.mobjects:
            self.play(*[FadeOut(m) for m in self.mobjects], run_time=.5)
        self.clear()
        self.chapters.append({'title': title, 'start': float(self.time)})
        self.add(words('Fictional example · calculated by Doug', 16, MUTED).to_corner(DOWN+LEFT, buff=.2))
        self.play(FadeIn(words(title, 35, width=12.6).move_to([0, 3.25, 0])), run_time=.6)
        self.play(FadeIn(words(explanation, 26, MUTED, width=12.2).move_to([0, 2.38, 0])), run_time=.6)

    def takeaway(self, text, color=MINT):
        box = panel(12.4, .8, color).move_to([0, -3.05, 0])
        label = words(text, 26, INK, width=11.8).move_to(box)
        self.play(FadeIn(VGroup(box, label)), run_time=.7)

    def hold(self, seconds=4):
        """Record reading beats for frame review, and catch clipped text early."""
        seen = set()
        for root in self.mobjects:
            for obj in root.get_family():
                if not isinstance(obj, Text) or id(obj) in seen:
                    continue
                seen.add(id(obj))
                if (obj.get_left()[0] < -7.1 or obj.get_right()[0] > 7.1
                        or obj.get_bottom()[1] < -3.98 or obj.get_top()[1] > 3.98):
                    raise ValueError(f'Text outside video frame: {obj.text!r}')
        self.review_times.append(float(self.time)+seconds/2)
        self.wait(seconds)

    def construct(self):
        self.example = build_progress_example()
        self.chapters = []
        self.review_times = []
        self.introduction()
        self.fixed_forecast()
        self.cash_result()
        self.weekly_comparison()
        self.dollar_range()
        self.more_history()
        self.summary_results()
        self.next_steps()
        if self.time >= MAX_DURATION_SECONDS:
            raise ValueError(f'Progress video must stay under two minutes: {self.time:.1f}s')
        e = self.example
        s = e.significance
        out = ROOT/'build'/'video'
        out.mkdir(parents=True, exist_ok=True)
        (out/'progress_timeline.json').write_text(json.dumps({
            'duration': float(self.time), 'chapters': self.chapters, 'captions': [],
            'review_times': self.review_times,
            'example': {
                'before_weeks': s.n_before, 'after_weeks': s.n_after,
                'weekly_change': s.diff, 'ci': [s.ci_low, s.ci_high],
                'p_value': s.p_value, 'coverage': e.check.inside_80,
                'cash_gap': e.check.delta,
            },
        }, indent=2)+'\n')

    def introduction(self):
        self.section('See how your finances are changing',
                     'A checkpoint saves your balances, score and forecast on one date.')
        earlier = card('April 6', 'Save a starting point', 'Your checkpoint for later comparisons', BLUE).move_to([-3.15, .15, 0])
        later = card('June 29', 'See what changed', 'Compare with your newer transactions', MINT).move_to([3.15, .15, 0])
        self.play(FadeIn(earlier), FadeIn(later), run_time=1)
        self.takeaway('Track record asks: How did your cash compare? Did spending change?')
        self.hold(7)

    def fixed_forecast(self):
        self.section('Compare the saved forecast with real cash',
                     'Blue shows the range Doug estimated earlier. White shows what happened.')
        p = CashPlot(self.example)
        legend = VGroup(words('Blue: saved range', 23, BLUE),
                        words('Dashed: middle estimate', 23, BLUE),
                        words('White: recorded cash', 23, INK)).arrange(RIGHT, buff=.55).move_to([0, 1.99, 0])
        self.play(Create(p.axes), FadeIn(p.fan), FadeIn(legend), run_time=1.2)
        self.hold(2.5)
        self.play(Create(p.recorded), run_time=4.5, rate_func=linear)
        self.takeaway('The original forecast stays fixed as new transactions arrive.')
        self.hold(4)

    def cash_result(self):
        e = self.example
        self.section('Compare cash on the same date',
                     'On June 29, the recorded balance was higher than the old estimate.')
        before = card('Saved middle estimate', money(e.check.predicted_now), 'Estimated on April 6 for June 29', BLUE).move_to([-3.15, .1, 0])
        after = card('Recorded cash', money(e.check.actual_now), 'Calculated from the later transactions', MINT).move_to([3.15, .1, 0])
        self.play(FadeIn(before), FadeIn(after), run_time=1)
        self.hold(2.5)
        self.takeaway(f'{money(e.check.delta)} more cash than expected. The forecast underestimated it.')
        self.hold(5)

    def weekly_comparison(self):
        e = self.example
        s = e.significance
        self.section('Are you spending less?',
                     'Compare wants outside regular bills, plus uncategorized spending.')
        headings = VGroup(
            words(f'Before April 6 · {len(e.before)} weeks', 27, BLUE).move_to([-3.1, 1.72, 0]),
            words(f'After April 6 · {len(e.after)} weeks', 27, MINT).move_to([3.1, 1.72, 0]),
        )
        before, after = week_grid(e.before, BLUE, -3.1), week_grid(e.after, MINT, 3.1)
        self.play(FadeIn(headings), FadeIn(before), FadeIn(after), run_time=1)
        self.play(FadeIn(words('One box = one complete week', 24, MUTED).move_to([0, -2.1, 0])), run_time=.5)
        self.hold(2.5)
        averages = VGroup(
            words(f'Average: {money(s.mean_before)} / week', 30, BLUE).move_to([-3.1, -1.3, 0]),
            words(f'Average: {money(s.mean_after)} / week', 30, MINT).move_to([3.1, -1.3, 0]),
        )
        self.play(FadeIn(averages), run_time=.7)
        self.takeaway(f'{money(s.mean_before)} − {money(s.mean_after)} = about {money(abs(s.diff))} less each week.')
        self.hold(6)

    def dollar_range(self):
        s = self.example.significance
        self.section('Treat the amount as an estimate',
                     'Weeks vary. Doug estimates a range for the size of the change.')
        def xy(value, y=0):
            return np.array([float(value)/300*5.55, y, 0])
        axis = VGroup(
            Line(xy(-300), xy(300), color=MUTED, stroke_width=1.4),
            DashedLine(xy(0, -.45), xy(0, .7), color=MUTED),
            words('Less spending', 25, MINT).move_to([-4.4, -.7, 0]),
            words('No change', 25, MUTED).move_to([0, -.7, 0]),
            words('More spending', 25, AMBER).move_to([4.4, -.7, 0]),
        )
        interval = Line(xy(s.ci_low), xy(s.ci_high), color=MINT, stroke_width=9)
        bounds = VGroup(
            words(f'{money(abs(s.ci_low))} less', 25, MINT).move_to(xy(s.ci_low, -1.17)),
            words(f'{money(abs(s.ci_high))} less', 25, MINT).move_to(xy(s.ci_high, -1.17)),
        )
        estimate = VGroup(Dot(xy(s.diff), color=INK, radius=.09),
                          words(f'Estimated drop: {money(abs(s.ci_high))}–{money(abs(s.ci_low))} / week', 30).move_to([0, 1.45, 0]))
        self.play(Create(axis), run_time=.7)
        self.play(Create(interval), FadeIn(bounds), FadeIn(estimate), run_time=.9)
        self.play(FadeIn(words('The whole range points toward less spending.', 26, MINT).move_to([0, -2, 0])), run_time=.6)
        self.takeaway('This describes past spending. Future weeks can be different.')
        self.hold(7)

    def more_history(self):
        self.section('Sometimes you need more history',
                     'A few quiet weeks do not establish a lasting change.')
        waiting = card('Not enough history', 'Keep importing', f'At least {MIN_WEEKS} full weeks before and {MIN_WEEKS} after', AMBER).move_to([-3.15, .1, 0])
        unclear = card('No clear change', 'Give it more time', 'The change is still hard to distinguish', BLUE).move_to([3.15, .1, 0])
        self.play(FadeIn(waiting), FadeIn(unclear), run_time=1)
        self.takeaway('Four weeks is the minimum. A clear result can take longer.')
        self.hold(8)

    def summary_results(self):
        e = self.example
        self.section('Each result answers a different question',
                     'Track record also shows changes in your score and net worth.')
        cards = [
            card('Weekly spending', f'{money(abs(e.significance.diff))} less', 'Wants + uncategorized spending', MINT, height=1.8).move_to([-3.15, .9, 0]),
            card('Cash vs. old forecast', f'{money(e.check.delta)} above', 'Above the old middle estimate', BLUE, height=1.8).move_to([3.15, .9, 0]),
            card('Habits score', f'{e.old.score:.1f} → {e.now.score:.1f} out of 10', 'A summary of saving and spending', MINT, height=1.8).move_to([-3.15, -1.3, 0]),
            card('Net worth', f'{money(e.old.networth)} → {money(e.now.networth)}', 'What you own minus what you owe', BLUE, height=1.8).move_to([3.15, -1.3, 0]),
        ]
        self.play(LaggedStart(*[FadeIn(c) for c in cards], lag_ratio=.25), run_time=1.5)
        self.takeaway('Your finances can improve even when the forecast misses the change.')
        self.hold(9)

    def next_steps(self):
        self.section('Keep tracking your progress',
                     'Return to Track record as you add new transactions.')
        steps = VGroup(
            words('1. Import your new transactions.', 31),
            words('2. Check categories, especially uncategorized spending.', 31, width=12),
            words('3. Compare with an earlier checkpoint in Track record.', 31, width=12),
        ).arrange(DOWN, aligned_edge=LEFT, buff=.65).move_to([0, .1, 0])
        self.play(LaggedStart(*[FadeIn(m) for m in steps], lag_ratio=.4), run_time=1.5)
        self.takeaway('Look for a sustained change over several weeks.')
        self.hold(8)
