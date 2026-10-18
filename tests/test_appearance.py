"""Dark and Light: one palette per theme, checked for contrast, switched in place."""
from __future__ import annotations

import os
import re
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PyQt6.QtWidgets import QScrollArea

from finfore.ui import theme
from finfore.ui.main_window import MainWindow
from test_onboard import Fixture


def luminance(hexstr: str) -> float:
    h = hexstr.lstrip('#')
    r, g, b = (int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))
    f = lambda c: c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b)


def contrast(a: str, b: str) -> float:
    hi, lo = sorted((luminance(a), luminance(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


# Text and the fills it is read on. Each pair is a place in the app where one
# sits on the other: body and secondary text on every surface, the five hues as
# words (amounts, statuses) on the page and on cards, and each state's own fill.
TEXT_PAIRS = (
    [(f, b) for f in ('text', 'muted') for b in ('bg', 'surface', 'field', 'surface2', 'bg_darker')]
    + [('disabled_text', b) for b in ('bg', 'surface', 'field')]
    + [(f, b) for f in ('accent', 'amber', 'red', 'blue', 'purple', 'giving')
       for b in ('bg', 'surface', 'field')]
    + [('rail_text', 'bg_darker'), ('accent', 'rail_selected'), ('muted', 'rail_hover'),
       ('text', 'warning_wash'), ('text', 'info_wash'), ('selection_text', 'selection'),
       ('primary_text', 'accent_dim'), ('primary_text', 'primary_hover'), ('text', 'accent_dim'),
       ('text', 'hover_fill'), ('text', 'text_selection'), ('banner_ink', 'banner_fill'),
       ('danger_hover_text', 'surface2'), ('danger_hover_text', 'hover_fill'),
       ('red', 'surface2'), ('text', 'pressed_fill'), ('text', 'alt_row'), ('muted', 'alt_row')])
# Edges and lines that have to be seen against what is around them (3:1).
EDGE_PAIRS = ([('border_strong', b) for b in ('bg', 'surface', 'field')]
              + [('accent', 'bg'), ('accent', 'surface'), ('amber', 'warning_wash'),
                 ('blue', 'info_wash'), ('reference', 'bg'), ('reference', 'surface'),
                 ('comparison', 'bg'), ('comparison', 'surface')])


class PaletteTests(unittest.TestCase):
    def tearDown(self):
        theme.use(theme.DARK)

    def test_every_text_pair_reads_at_4_5_to_1_in_both_themes(self):
        for palette in (theme.DARK, theme.LIGHT):
            for fg, bg in TEXT_PAIRS:
                with self.subTest(theme=palette.bg, pair=(fg, bg)):
                    self.assertGreaterEqual(
                        contrast(getattr(palette, fg), getattr(palette, bg)), 4.5)

    def test_every_edge_and_line_shows_at_3_to_1_in_both_themes(self):
        for palette in (theme.DARK, theme.LIGHT):
            for fg, bg in EDGE_PAIRS:
                with self.subTest(theme=palette.bg, pair=(fg, bg)):
                    self.assertGreaterEqual(
                        contrast(getattr(palette, fg), getattr(palette, bg)), 3.0)

    def test_the_light_stylesheet_uses_no_dark_colour_except_the_shared_demo_bar(self):
        shared = {theme.DARK.banner_fill, theme.DARK.banner_ink, theme.DARK.banner_edge,
                  theme.DARK.banner_hover}
        dark_only = {v.upper() for v in theme.DARK.__dict__.values()} - shared
        used = {m.upper() for m in re.findall(r'#[0-9A-Fa-f]{6}', theme.stylesheet(theme.LIGHT))}
        self.assertEqual(used & dark_only, set())

    def test_switching_and_back_restores_dark_exactly(self):
        dark_sheet, dark_spending = theme.QSS, dict(theme.SPENDING_COLORS)
        theme.use(theme.LIGHT)
        self.assertEqual(theme.BG, theme.LIGHT.bg)
        self.assertEqual(theme.SPENDING_COLORS['Needs'], theme.LIGHT.blue)
        self.assertEqual(theme.GOAL_COLORS[0], theme.LIGHT.accent)
        theme.use(theme.DARK)
        self.assertEqual(theme.QSS, dark_sheet)
        self.assertEqual(theme.SPENDING_COLORS, dark_spending)

    def test_a_stored_colour_follows_its_role_and_anything_else_passes_through(self):
        theme.use(theme.LIGHT)
        self.assertEqual(theme.live(theme.DARK.amber), theme.LIGHT.amber)
        self.assertEqual(theme.live(theme.DARK.surface.lower()), theme.LIGHT.surface)
        self.assertEqual(theme.live('#123456'), '#123456')
        self.assertEqual(theme.live_stylesheet(f'color: {theme.DARK.red};'),
                         f'color: {theme.LIGHT.red};')


class AppearanceWindowTests(Fixture, unittest.TestCase):
    demo = True

    def tearDown(self):
        theme.use(theme.DARK, self.app)
        super().tearDown()

    def test_view_menu_offers_three_appearances_with_dark_chosen_by_default(self):
        actions = self.win.appearance_actions
        self.assertEqual([a.text() for a in actions.values()],
                         ['Dark', 'Light', 'Match my computer'])
        self.assertTrue(actions['dark'].isChecked())
        self.assertIs(theme.current(), theme.DARK)

    def test_choosing_light_repaints_in_place_and_is_remembered(self):
        self.win.mode_all.click()
        page = self.go('Transactions')
        page.search.setText('KROGER')
        self.settle()
        self.win.appearance_actions['light'].trigger()
        self.settle()
        self.assertIs(theme.current(), theme.LIGHT)
        self.assertEqual(self.app.styleSheet(), theme.stylesheet(theme.LIGHT))
        self.assertIs(self.win.current_page(), page)
        self.assertEqual(page.search.text(), 'KROGER')
        header = self.win.findChild(type(self.win.centralWidget()), 'header')
        self.assertIn(theme.LIGHT.bg_darker, header.styleSheet())
        self.assertEqual(self.win._settings.value('appearance'), 'light')

    def test_the_forecast_drawer_and_scroll_position_survive_a_switch(self):
        self.win.resize(980, 620)
        page = self.go('Projection')
        page.try_btn.click()
        self.settle()
        area = next(a for a in page.findChildren(QScrollArea) if a.verticalScrollBar().maximum() > 0)
        area.verticalScrollBar().setValue(area.verticalScrollBar().maximum())
        where = area.verticalScrollBar().value()
        self.win.set_appearance('light')
        self.settle()
        self.assertTrue(page.control_scroll.isVisibleTo(page))
        self.assertEqual(page.try_btn.text(), 'Hide changes')
        self.assertEqual(area.verticalScrollBar().value(), where)

    def test_charts_move_to_the_new_background(self):
        page = self.go('Overview')
        self.win.set_appearance('light')
        self.settle()
        plot = page.findChild(__import__('pyqtgraph').PlotWidget)
        self.assertEqual(plot.backgroundBrush().color().name().upper(), theme.LIGHT.bg)

    def test_a_window_opened_later_starts_in_the_saved_appearance(self):
        self.win.set_appearance('light')
        theme.use(theme.DARK, self.app)
        again = MainWindow(self.store)
        try:
            self.assertIs(theme.current(), theme.LIGHT)
            self.assertTrue(again.appearance_actions['light'].isChecked())
        finally:
            again.close()
            again.deleteLater()

    def test_match_my_computer_follows_the_system_and_dark_ignores_it(self):
        with patch.object(MainWindow, '_palette_for',
                          staticmethod(lambda key: theme.LIGHT if key == 'system' else theme.DARK)):
            self.win._on_color_scheme(None)
            self.assertIs(theme.current(), theme.DARK)
            self.win.set_appearance('system')
            self.assertIs(theme.current(), theme.LIGHT)

    def test_the_view_menu_and_the_header_switch_agree(self):
        self.win.mode_all.click()
        self.settle()
        self.assertTrue(self.win.a_everything.isChecked())
        self.win.a_simple.trigger()
        self.settle()
        self.assertTrue(self.win.mode_simple.isChecked())
        self.assertTrue(self.win.simple_mode)


if __name__ == '__main__':
    unittest.main()
