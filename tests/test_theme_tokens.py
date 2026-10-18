"""Every colour comes from one palette, so a second theme is a second palette."""
from __future__ import annotations

import os
import re
import sys
import unittest
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PyQt6.QtGui import QPalette

from finfore.ui import theme
from test_usability import desktop_test_app

UI = Path(__file__).resolve().parent.parent / 'finfore' / 'ui'


class PaletteTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = desktop_test_app()

    def test_the_stylesheet_is_built_from_the_dark_palette_with_nothing_left_unfilled(self):
        self.assertEqual(theme.stylesheet(theme.DARK), theme.QSS)
        self.assertNotRegex(theme.QSS, r'\{[A-Z_]+\}')

    def test_no_screen_hard_codes_a_colour(self):
        offenders = []
        for path in sorted(UI.glob('*.py')):
            if path.name == 'theme.py':
                continue
            for n, line in enumerate(path.read_text(encoding='utf-8').splitlines(), 1):
                if not line.lstrip().startswith('#') and re.search(r'#[0-9A-Fa-f]{6}\b', line):
                    offenders.append(f'{path.name}:{n}: {line.strip()}')
        self.assertEqual(offenders, [])

    def test_the_old_names_are_the_dark_palettes_values(self):
        d = theme.DARK
        for name, value in (('BG', d.bg), ('TEXT', d.text), ('ACCENT', d.accent), ('AMBER', d.amber),
                            ('RED', d.red), ('COMPARISON', d.comparison), ('GIVING', d.giving),
                            ('DISABLED_TEXT', d.disabled_text), ('WARNING_WASH', d.warning_wash)):
            self.assertEqual(getattr(theme, name), value, name)

    def test_a_second_palette_restyles_the_application(self):
        other = replace(theme.DARK, bg='#FAFAF7', text='#1C2430')
        previous = self.app.styleSheet()
        try:
            theme.apply(self.app, other)
            self.assertEqual(self.app.palette().color(QPalette.ColorRole.Window).name().upper(), '#FAFAF7')
            self.assertIn('#1C2430', self.app.styleSheet())
            self.assertNotIn(theme.DARK.bg, self.app.styleSheet().split('QWidget#page')[1][:80])
        finally:
            theme.apply(self.app)
            self.app.setStyleSheet(previous)

    def test_shared_colour_sets_use_the_tokens(self):
        self.assertEqual(theme.GOAL_COLORS[0], theme.ACCENT)
        self.assertEqual(theme.SPENDING_COLORS['Needs'], theme.BLUE)
        self.assertEqual(theme.SPENDING_COLORS['Giving'], theme.GIVING)

    def test_headlines_are_one_rule_not_inline_sizes(self):
        self.assertIn('QLabel#headline', theme.QSS)
        inline = [f'{p.name}:{n}' for p in UI.glob('*.py') if p.name != 'theme.py'
                  for n, line in enumerate(p.read_text(encoding='utf-8').splitlines(), 1)
                  if 'font-size: 17px' in line]
        self.assertEqual(inline, [])


if __name__ == '__main__':
    unittest.main()
