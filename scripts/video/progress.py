"""Silent visual explanation of Forecast → Progress, using application results."""
from __future__ import annotations

import json
from pathlib import Path
import sys

import numpy as np
from manim import (Scene, VGroup, RoundedRectangle, Rectangle, Line, Arrow, Dot,
                   VMobject, Polygon, DashedLine, DashedVMobject, Brace,
                   FadeIn, FadeOut, Create, Transform, TransformFromCopy,
                   LaggedStart, UP, DOWN, LEFT, RIGHT, linear)

sys.path.insert(0, str(Path(__file__).resolve().parent))
from monte_carlo import words, money, BG, PANEL, BORDER, INK, MUTED, MINT, BLUE, AMBER, PURPLE
from progress_example import build_progress_example, ROOT, OLD, NOW
from finfore.progress import N_PERMUTATIONS, N_BOOTSTRAP, MIN_WEEKS


def panel(width, height, color=BORDER):
    return RoundedRectangle(width=width, height=height, corner_radius=.10,
                            fill_color=PANEL, fill_opacity=1, stroke_color=color, stroke_width=1.4)


def stat(label, value, color=INK, width=3.6):
    return VGroup(words(label,22,MUTED,width=width), words(value,38,color,width=width)).arrange(DOWN,buff=.25)


def chip(amount, color=BLUE):
    box = panel(1.02,.46,color)
    return VGroup(box,words(money(amount),17,INK,width=.88))


class CashPlot:
    def __init__(self, example):
        self.frame = example.old.forecast_frame()
        self.actual = example.actual.to_numpy()
        lo = min(self.frame.p10.min(),self.actual.min())
        hi = max(self.frame.p90.max(),self.actual.max())
        self.low = np.floor((lo-600)/2000)*2000
        self.high = np.ceil((hi+600)/2000)*2000
        self.n = len(self.frame)
        self.axes = VGroup()
        for value in np.linspace(self.low,self.high,5):
            y = self.point(0,value)[1]
            self.axes.add(Line([-5.45,y,0],[5.5,y,0],color=BORDER,stroke_width=1),
                          words(money(value),17,MUTED).move_to([-6.12,y,0]))
        self.axes.add(words(OLD.strftime('%b %d'),20,MUTED).move_to([-5.45,-2.88,0]),
                      words(NOW.strftime('%b %d'),20,MUTED).move_to([5.5,-2.88,0]),
                      words('Cash',20,MUTED).move_to([-5.4,2.40,0],aligned_edge=LEFT))
        self.outer = self.band(10,90,34/255)
        self.inner = self.band(25,75,62/255)
        self.median = DashedVMobject(self.curve(self.frame.p50,BLUE,2),num_dashes=75)
        self.recorded = self.curve(self.actual,INK,3)
        self.fan = VGroup(self.outer,self.inner,self.median)

    def point(self,day,value):
        return np.array([-5.45+10.95*day/(self.n-1), -2.30+4.30*(value-self.low)/(self.high-self.low),0])

    def curve(self,values,color,width):
        return VMobject().set_points_as_corners([self.point(i,float(v)) for i,v in enumerate(values)]).set_stroke(color,width)

    def band(self,lower,upper,alpha):
        a=[self.point(i,float(v)) for i,v in enumerate(self.frame[f'p{lower}'])]
        b=[self.point(i,float(v)) for i,v in enumerate(self.frame[f'p{upper}'])]
        return Polygon(*(a+b[::-1]),stroke_width=0,fill_color=BLUE,fill_opacity=alpha)


def distribution(values, observed, color=BLUE, include_zero=True):
    lower = min(float(np.min(values)),observed,0)-25
    upper = max(float(np.max(values)),observed,0)+25
    def xy(value,height=0):
        return np.array([-5.4+10.8*(value-lower)/(upper-lower),-1.85+height,0])
    counts,edges=np.histogram(values,bins=42)
    bars=VGroup()
    max_count=max(counts)
    for count,left,right in zip(counts,edges[:-1],edges[1:]):
        height=max(.015,2.55*count/max_count)
        width=10.8*(right-left)/(upper-lower)*.92
        bar=Rectangle(width=width,height=height,stroke_width=0,fill_color=color,fill_opacity=.7)
        bar.move_to(xy((left+right)/2,height/2))
        bars.add(bar)
    axis=VGroup(Line(xy(lower),xy(upper),color=MUTED,stroke_width=1.3))
    for value in (round(lower/50)*50,0,round(upper/50)*50):
        if lower <= value <= upper:
            axis.add(words(money(value),18,MUTED).move_to(xy(value)-np.array([0,.32,0])))
    axis.add(words('Change in spending · dollars / week',20,MUTED).move_to([0,-2.63,0]))
    zero=DashedLine(xy(0),xy(0,2.85),color=MUTED,stroke_width=1.4)
    marker=Line(xy(observed),xy(observed,3.05),color=MINT,stroke_width=3)
    label=words(money(observed),23,MINT).move_to(xy(observed,3.45))
    return bars,axis,zero,marker,label,xy


class ProgressExplainer(Scene):
    def section(self,title):
        if self.mobjects:
            self.play(*[FadeOut(m) for m in self.mobjects],run_time=.5)
        self.clear()
        self.chapters.append({'title':title,'start':float(self.time)})
        self.add(words('Fictional example · actual app calculations',14,MUTED).to_corner(DOWN+LEFT,buff=.23))
        self.play(FadeIn(words(title,34,width=12.4).move_to([0,3.25,0])),run_time=.6)

    def construct(self):
        e=build_progress_example()
        s=e.significance
        self.chapters=[]
        self.section('Forecast → Progress')
        plot=CashPlot(e)
        mini=VGroup(plot.axes,plot.fan,plot.recorded).scale(.58).move_to([0,.5,0])
        self.play(Create(mini[0]),FadeIn(mini[1]),run_time=1.8)
        self.play(Create(mini[2]),run_time=3.4,rate_func=linear)
        labels=VGroup(words('An earlier forecast',24,BLUE),words('What happened',24,INK),
                      words('What changed',24,MINT)).arrange(RIGHT,buff=.7).move_to([0,-2.5,0])
        self.play(LaggedStart(*[FadeIn(m) for m in labels],lag_ratio=.5),run_time=2.2)
        self.wait(3)

        self.section('Save the forecast before the outcome')
        line=Line([-5,0,0],[5,0,0],color=BORDER,stroke_width=3)
        early=Dot([-4,0,0],color=BLUE)
        later=Dot([4,0,0],color=INK)
        dates=VGroup(words('Apr 06',23,BLUE).move_to([-4,-.5,0]),
                     words('Jun 29',23,INK).move_to([4,-.5,0]))
        file=VGroup(panel(2.15,1.25),words('CSV import',24,INK)).move_to([-4,1.55,0])
        snapshot=VGroup(panel(4.8,2.4,BLUE),
                        stat('Cash',money(e.old.cash),BLUE).scale(.65).move_to([-1.1,.4,0]),
                        stat('Habits score',f'{e.old.score:.1f} / 10',BLUE).scale(.65).move_to([1.1,.4,0]),
                        words('Saved forecast',23,BLUE).move_to([0,-.6,0])).move_to([0,1.6,0])
        self.play(Create(line),FadeIn(dates),FadeIn(early),FadeIn(later),FadeIn(file),run_time=1.5)
        self.play(TransformFromCopy(file,snapshot),run_time=2)
        self.play(Create(Arrow(early.get_center(),snapshot.get_bottom(),buff=.2,color=BLUE)),run_time=.7)
        self.play(FadeIn(words('capture(store)',22,MUTED).move_to([0,-2.6,0])),run_time=.5)
        self.wait(3.5)
        self.play(FadeIn(words('Checkpoint',27,BLUE).move_to([0,-1.5,0])),run_time=.7)
        self.wait(2)

        self.section('Keep that saved forecast fixed')
        p=CashPlot(e)
        self.play(Create(p.axes),FadeIn(p.outer),FadeIn(p.inner),Create(p.median),run_time=2.3)
        legend=VGroup(words('Saved middle · P50',20,BLUE),words('Saved 80% band · P10–P90',20,BLUE),
                      words('Recorded cash',20,INK)).arrange(RIGHT,buff=.65).move_to([0,2.65,0])
        self.play(FadeIn(legend),run_time=.7)
        self.wait(2)
        cursor=Line(p.point(0,p.low),p.point(0,p.high),color=MUTED,stroke_width=1)
        self.add(cursor)
        self.play(Create(p.recorded),cursor.animate.shift(RIGHT*10.95),run_time=8,rate_func=linear)
        self.wait(2)
        self.play(FadeIn(words('check_path(store, checkpoint)',20,MUTED).move_to([0,-3.45,0])),run_time=.5)
        self.wait(2)

        self.section('Compare cash on the same date')
        p=CashPlot(e)
        self.add(p.axes,p.fan,p.recorded)
        predicted=p.point(p.n-1,e.check.predicted_now)
        recorded=p.point(p.n-1,e.check.actual_now)
        self.play(FadeIn(Dot(predicted,color=BLUE,radius=.065)),FadeIn(Dot(recorded,color=INK,radius=.065)),run_time=.7)
        gap=Line(predicted+LEFT*.20,recorded+LEFT*.20,color=MINT,stroke_width=4)
        self.play(Create(gap),run_time=1)
        result=VGroup(panel(5.3,1.5),words(f'+{money(e.check.delta)} above the old middle',27,MINT,width=4.9),
                      words('Outside the saved 80% band',21,PURPLE,width=4.9).shift(DOWN*.44)).move_to([-1.8,1.75,0])
        result[1].move_to(result[0].get_center()+UP*.23)
        result[2].move_to(result[0].get_center()+DOWN*.34)
        self.play(FadeIn(result),run_time=1)
        self.wait(4)
        self.play(FadeIn(words(f'Saved middle {money(e.check.predicted_now)}   →   Recorded {money(e.check.actual_now)}',24,width=12).move_to([0,-3.18,0])),run_time=.8)
        self.wait(3)

        self.section('Check the whole path, too')
        f=e.old.forecast_frame()
        inside=(e.actual >= f.p10)&(e.actual <= f.p90)
        dots=VGroup(*[Dot(radius=.047,color=BLUE if ok else PURPLE) for ok in inside]).arrange_in_grid(rows=5,cols=17,buff=.17).move_to([0,.8,0])
        count=int(inside.sum())
        self.play(LaggedStart(*[FadeIn(dot) for dot in dots],lag_ratio=.015),run_time=3)
        key=VGroup(words('Inside the band',22,BLUE),words('Outside the band',22,PURPLE)).arrange(RIGHT,buff=1).move_to([0,-.4,0])
        self.play(FadeIn(key),run_time=.6)
        self.play(FadeIn(words(f'{count} / {len(inside)} recorded days inside',32).move_to([0,-1.35,0])),run_time=.8)
        self.play(FadeIn(words(f'{e.check.inside_80:.0%} coverage in this example',25,PURPLE).move_to([0,-2.25,0])),run_time=.7)
        self.wait(4)

        self.section('Which weeks enter the spending comparison?')
        rows=[('Rent','Recurring',False),('Groceries','Necessities',False),
              ('Dining','Wants',True),('Uncategorized outflows','Unknown',True),
              ('Charitable donation','Giving',False),('Business travel','Reimbursable',False),
              ('Account transfer','Transfer',False)]
        ledger=VGroup()
        selected=VGroup()
        for i,(name,kind,keep) in enumerate(rows):
            y=2.05-i*.61
            row=VGroup(words(name,21,MINT if keep else MUTED,width=3.5).move_to([-4.5,y,0],aligned_edge=LEFT),
                       words(kind,18,MINT if keep else MUTED,width=2.9).move_to([-.3,y,0],aligned_edge=LEFT))
            ledger.add(row)
            if keep:
                selected.add(words(name,22,MINT,width=3).move_to([4.1,.55-len(selected)*.9,0]))
        self.play(LaggedStart(*[FadeIn(row) for row in ledger],lag_ratio=.12),run_time=2.5)
        self.play(Create(Arrow([1.45,.5,0],[2.6,.5,0],color=MINT)),run_time=.7)
        self.play(*[TransformFromCopy(ledger[i][0],selected[j]) for j,i in enumerate((2,3))],run_time=1.7)
        self.play(FadeIn(words('Non-recurring wants + unknown',23,MINT,width=6).move_to([0,-2.75,0])),run_time=.7)
        self.wait(4)

        self.section('A checkpoint divides the weeks')
        before=VGroup(*[chip(v,BLUE) for v in e.before]).arrange_in_grid(rows=3,cols=4,buff=.18).move_to([-3,.5,0])
        after=VGroup(*[chip(v,MINT) for v in e.after]).arrange_in_grid(rows=3,cols=4,buff=.18).move_to([3,.5,0])
        self.play(Create(Line([0,-2,0],[0,2.1,0],color=BORDER)),run_time=.5)
        self.play(FadeIn(words(f'Before · {len(e.before)} complete weeks',24,BLUE,width=5).move_to([-3,2.1,0])),
                  FadeIn(words(f'After · {len(e.after)} complete weeks',24,MINT,width=5).move_to([3,2.1,0])),run_time=.7)
        self.play(LaggedStart(*[FadeIn(c) for c in before],lag_ratio=.1),LaggedStart(*[FadeIn(c) for c in after],lag_ratio=.1),run_time=2.4)
        means=VGroup(stat('Average / week',money(s.mean_before),BLUE).move_to([-3,-1.6,0]),
                     stat('Average / week',money(s.mean_after),MINT).move_to([3,-1.6,0]))
        self.play(FadeIn(means),run_time=1)
        self.wait(4)
        self.play(FadeIn(words(f'{money(abs(s.diff))} less / week',28,MINT).move_to([0,-3.15,0])),run_time=.7)
        self.wait(3)

        self.section('Could normal weekly variation explain the change?')
        combined=np.r_[e.before,e.after]
        chips=VGroup(*[chip(v,BLUE if i<len(e.before) else MINT) for i,v in enumerate(combined)])
        targets=np.array([[-5.2+i*.98,.9,0] for i in range(len(e.before))] +
                         [[-5.4+i*.98,-.65,0] for i in range(len(e.after))])
        for c,t in zip(chips,targets): c.move_to(t)
        self.play(FadeIn(chips),run_time=1)
        instruction=words('Same weeks · shuffle the period labels',25,MUTED,width=11).move_to([0,2.4,0])
        groups=VGroup(words('Before',21,BLUE).move_to([0,1.65,0]),
                      words('After',21,MINT).move_to([0,.1,0]))
        self.play(FadeIn(instruction),FadeIn(groups),run_time=.6)
        rng=np.random.default_rng(11)
        for _ in range(4):
            permutation=rng.permutation(len(combined))
            self.play(*[c.animate.move_to(targets[permutation[i]]) for i,c in enumerate(chips)],run_time=1.1)
            for i,c in enumerate(chips):
                c[0].set_stroke(BLUE if permutation[i]<len(e.before) else MINT)
            self.wait(.4)
        counter=words('20,000 shuffles',30,BLUE).move_to([0,-2,0])
        self.play(FadeIn(counter),run_time=.7)
        self.wait(2)
        self.play(FadeOut(chips),FadeOut(counter),FadeOut(instruction),FadeOut(groups),run_time=.7)
        self.play(FadeIn(words('20,000 reshuffled comparisons',25,MUTED).move_to([0,2.4,0])),run_time=.6)
        bars,axis,zero,marker,label,xy=distribution(e.permutations,s.diff,BLUE)
        self.play(Create(axis),Create(zero),LaggedStart(*[FadeIn(bar) for bar in bars],lag_ratio=.025),run_time=2)
        self.play(Create(marker),FadeIn(label),run_time=1)
        self.play(FadeIn(words('Observed change',20,MINT).move_to([label.get_center()[0],label.get_center()[1]+.38,0])),run_time=.5)
        self.wait(3)
        extreme=int(np.count_nonzero(abs(e.permutations)>=abs(s.diff)-1e-12))
        self.play(FadeIn(words(f'{extreme:,} / {N_PERMUTATIONS:,} shuffles as extreme',25,MINT).move_to([0,-3.2,0])),run_time=.7)
        self.wait(4)

        self.section('Estimate how much spending changed')
        originals=VGroup()
        headings=VGroup(words('Before',22,BLUE).move_to([-3,2.1,0]),
                        words('After',22,MINT).move_to([3,2.1,0]))
        for vals,x,color in ((e.before,-3,BLUE),(e.after,3,MINT)):
            originals.add(VGroup(*[chip(v,color) for v in vals]).arrange_in_grid(rows=3,cols=4,buff=.10).scale(.8).move_to([x,1.0,0]))
        arrows=VGroup(Arrow([-3,.15,0],[-3,-.30,0],buff=0,color=BLUE),
                      Arrow([3,.15,0],[3,-.30,0],buff=0,color=MINT))
        self.play(FadeIn(originals),FadeIn(headings),Create(arrows),run_time=1.2)
        draws=None
        for sampled_before,sampled_after in e.resamples:
            if draws is not None: self.play(FadeOut(draws),run_time=.4)
            draws=VGroup()
            animations=[]
            for j,(sampled,original_values,x,color) in enumerate(((sampled_before,e.before,-3,BLUE),(sampled_after,e.after,3,MINT))):
                group=VGroup(*[chip(v,color) for v in sampled]).arrange_in_grid(rows=3,cols=4,buff=.1).scale(.8).move_to([x,-1.0,0])
                for v,tile in zip(sampled,group):
                    source=int(np.flatnonzero(original_values==v)[0])
                    animations.append(TransformFromCopy(originals[j][source],tile))
                draws.add(group)
            self.play(LaggedStart(*animations,lag_ratio=.015),run_time=1.8)
            self.wait(1)
        self.play(FadeIn(words('Whole weeks · repeats allowed',24,MUTED).move_to([0,-2.35,0])),run_time=.6)
        self.wait(2)
        content=[m for m in self.mobjects if m.get_center()[1]<3.0 and m.get_center()[1]>-3.5]
        self.play(*[FadeOut(m) for m in content],run_time=.7)
        bars,axis,zero,marker,label,xy=distribution(e.bootstraps,s.diff,MINT)
        self.play(FadeIn(words('Resample each period · 4,000 times',25,MUTED).move_to([0,2.42,0])),run_time=.7)
        self.play(Create(axis),Create(zero),LaggedStart(*[FadeIn(bar) for bar in bars],lag_ratio=.025),run_time=2)
        self.play(Create(marker),FadeIn(label),run_time=.9)
        interval=Line(xy(s.ci_low,.15),xy(s.ci_high,.15),stroke_width=6,color=MINT)
        self.play(Create(interval),run_time=1)
        self.wait(3)
        self.play(FadeIn(words(f'95% bootstrap interval: {money(abs(s.ci_high))}–{money(abs(s.ci_low))} less / week',25,MINT,width=12).move_to([0,-3.25,0])),run_time=.9)
        self.wait(5)

        self.section('Sometimes the evidence is still uncertain')
        axis=Line([-5.2,.7,0],[5.2,.7,0],color=MUTED)
        zero=DashedLine([0,-.1,0],[0,1.6,0],color=MUTED)
        interval=Line([-2.3,.7,0],[2.3,.7,0],color=AMBER,stroke_width=6)
        self.play(Create(axis),Create(zero),Create(interval),FadeIn(Dot([0,.7,0],color=AMBER)),run_time=1.6)
        self.play(FadeIn(words('Less spending',21,MINT).move_to([-4,-.0,0])),
                  FadeIn(words('No change',21,MUTED).move_to([0,-.0,0])),
                  FadeIn(words('More spending',21,AMBER).move_to([4,-.0,0])),run_time=.8)
        self.play(FadeIn(words('No clear change',30,AMBER).move_to([0,2.3,0])),run_time=.7)
        self.wait(3)
        self.play(FadeIn(words('At least 4 complete weeks on each side',25,MUTED).move_to([0,-1.2,0])),run_time=.7)
        slots=VGroup(*[panel(.65,.65,BLUE) for _ in range(4)]).arrange(RIGHT,buff=.15).move_to([-2.5,-2.2,0])
        newslots=VGroup(*[panel(.65,.65,MINT if i<3 else BORDER) for i in range(4)]).arrange(RIGHT,buff=.15).move_to([2.5,-2.2,0])
        self.play(FadeIn(slots),FadeIn(newslots),run_time=.8)
        self.play(FadeIn(words('Not enough data yet',24,AMBER).move_to([0,-3.1,0])),run_time=.7)
        self.wait(3)

        self.section('Put the changes side by side')
        score=VGroup(stat('Habits score',f'{e.old.score:.1f} → {e.now.score:.1f}',MINT),
                     words('Out of 10 · recency-weighted',19,MUTED)).arrange(DOWN,buff=.25).move_to([-3.2,1.45,0])
        worth=VGroup(stat('Net worth',f'{money(e.old.networth)} → {money(e.now.networth)}',INK,width=5.8),
                     words('Balances · assets minus debt',19,MUTED)).arrange(DOWN,buff=.25).move_to([3.2,1.45,0])
        spending=stat('Discretionary spending',f'{money(abs(s.diff))} less / week',MINT,width=5.6).move_to([-3.2,-1.3,0])
        accuracy=stat('Against the old projection','Off the map',PURPLE,width=5.6).move_to([3.2,-1.3,0])
        self.play(FadeIn(score),FadeIn(worth),run_time=1)
        self.play(FadeIn(spending),FadeIn(accuracy),run_time=1)
        self.wait(5)
        self.play(FadeIn(words('Different measures · compared with the same checkpoint',23,MUTED,width=12).move_to([0,-3,0])),run_time=.7)
        self.wait(3)

        self.section('Reconstruct history uses the data available then')
        dates=['Feb 23','Mar 09','Mar 23','Apr 05','May 11','Jun 22']
        months=VGroup(*[VGroup(panel(1.35,1.5,BLUE if i<4 else BORDER),
                                   words(label,20,BLUE if i<4 else MUTED)) for i,label in enumerate(dates)]).arrange(RIGHT,buff=.24).move_to([0,1.1,0])
        self.play(FadeIn(months),run_time=1)
        cutoff=DashedLine([1.55,.1,0],[1.55,2.4,0],color=BLUE)
        self.play(Create(cutoff),FadeIn(words('As of Apr 06',23,BLUE).move_to([1.55,2.6,0])),run_time=.8)
        past=VGroup(*months[:4]).copy()
        self.play(FadeOut(VGroup(*months[4:])),past.animate.move_to([-2.9,-1.35,0]).scale(.65),run_time=1.8)
        target=VGroup(panel(3.5,1.4,BLUE),words('Historical checkpoint',23,BLUE,width=3.1)).move_to([3,-1.35,0])
        self.play(Create(Arrow([-.8,-1.35,0],[1,-1.35,0],color=BLUE)),FadeIn(target),run_time=1)
        self.play(FadeIn(words('capture_as_of(store, when)',22,MUTED).move_to([0,-2.65,0])),run_time=.7)
        self.wait(3)
        self.play(FadeIn(words('Retrospective test',25,AMBER).move_to([3,-.35,0])),run_time=.7)
        self.wait(3)

        self.section('Progress comes from your real history')
        ending=VGroup(words('Your real weeks.',38,MINT),words('Your saved forecasts.',38,BLUE),
                      words('Measured against what actually happened.',30,INK,width=12)).arrange(DOWN,buff=.65).move_to([0,.5,0])
        self.play(LaggedStart(*[FadeIn(m) for m in ending],lag_ratio=.8),run_time=3)
        self.wait(6)
        self.play(FadeOut(ending),run_time=1.3)
        out=ROOT/'build'/'video'
        out.mkdir(parents=True,exist_ok=True)
        (out/'progress_timeline.json').write_text(json.dumps({
            'duration':float(self.time),'chapters':self.chapters,'captions':[],
            'example':{'before_weeks':s.n_before,'after_weeks':s.n_after,
                       'weekly_change':s.diff,'ci':[s.ci_low,s.ci_high],
                       'p_value':s.p_value,'coverage':e.check.inside_80,
                       'cash_gap':e.check.delta}},indent=2)+'\n')
