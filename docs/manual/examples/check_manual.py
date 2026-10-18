"""Check the standalone manual without opening the application or a database."""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import re
import sys

sys.dont_write_bytecode = True
MANUAL = Path(__file__).resolve().parents[1]
REPO = MANUAL.parents[1]
MAIN = 'financing_with_doug_manual'


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check-source', action='store_true', help='also report application source drift')
    args = parser.parse_args()
    errors = []
    def require(condition, message):
        if not condition:
            errors.append(message)

    # Walk actual inputs, including generated tables and original figures.
    visited = set()
    texts = {}
    def visit(path):
        if path in visited:
            return
        visited.add(path)
        if not path.is_file():
            errors.append(f'Missing TeX input: {path.relative_to(MANUAL)}')
            return
        body = path.read_text(encoding='utf-8')
        texts[path] = body
        for name in re.findall(r'\\input\{([^}]+)\}', body):
            target = MANUAL / name
            if not target.suffix:
                target = target.with_suffix('.tex')
            visit(target)
    visit(MANUAL / f'{MAIN}.tex')
    body = '\n'.join(texts.values())
    labels = re.findall(r'\\label\{([^}]+)\}', body)
    for label, count in Counter(labels).items():
        require(count == 1, f'Duplicate label: {label}')
    for label in re.findall(r'\\(?:ref|pageref|eqref|seeref)\{([^}]+)\}', body):
        if not label.startswith('#'):
            require(label in labels, f'Unresolved source reference: {label}')
    chapters = {str(p.relative_to(MANUAL)) for p in (MANUAL / 'chapters').glob('*.tex')}
    require(chapters <= {str(p.relative_to(MANUAL)) for p in visited}, 'A chapter exists but is not included.')
    require(not re.search(r'\b(?:TODO|TBD|FIXME)\b', body), 'Unresolved drafting placeholder.')

    mapping = json.loads((MANUAL / 'source_map.json').read_text())
    mapped = {entry['chapter'] for entry in mapping['chapters']}
    require(chapters == mapped, 'Source map must contain every chapter exactly once.')
    require(len(mapped) == len(mapping['chapters']), 'Duplicate source-map chapter.')
    for entry in mapping['chapters']:
        for name in entry['sources'] + entry['verification_files']:
            require((REPO / name).is_file(), f'Missing source-map path: {name}')
    for paths in re.findall(r'\\source\{([^}]+)\}', body):
        if paths.startswith('#'):
            continue
        for name in paths.split(';'):
            require((REPO / name.strip()).is_file(), f'Missing implementation reference: {name.strip()}')

    manifest = json.loads((MANUAL / 'generated/provenance.json').read_text())
    require(manifest['generator_sha256'] == sha(MANUAL / 'examples/build_examples.py'),
            'Generator changed after the retained evidence was built; run make examples.')
    for name, expected in manifest['output_hashes'].items():
        path = MANUAL / 'generated' / name
        require(path.is_file() and sha(path) == expected, f'Changed or missing generated artifact: {name}')
    results = json.loads((MANUAL / 'generated/results.json').read_text())
    require(results['checks'] == manifest['independent_checks'], 'Example check records disagree.')
    require([results['qualification']] == manifest['diagnostic_qualifications'], 'Diagnostic records disagree.')

    pdf = MANUAL / f'{MAIN}.pdf'
    log = MANUAL / f'{MAIN}.log'
    require(pdf.is_file(), 'Build the PDF before checking the manual.')
    require(log.is_file(), 'Build log absent; run make pdf (use latexmk -g if already current).')
    if pdf.exists():
        require(pdf.read_bytes().startswith(b'%PDF-'), 'Output is not a PDF.')
        dependencies = [*visited, MANUAL / 'generated/forecast.dat']
        newer = [str(p.relative_to(MANUAL)) for p in dependencies if p.exists() and p.stat().st_mtime > pdf.stat().st_mtime]
        require(not newer, 'PDF predates inputs: ' + ', '.join(newer))
    if log.exists():
        log_text = log.read_text(errors='replace')
        for pattern in [r'Overfull \\[hv]box[^\n]*', r'LaTeX Warning: Reference[^\n]*',
                        r'LaTeX Warning: There were undefined references[^\n]*',
                        r'LaTeX Warning: Label\(s\) may have changed[^\n]*',
                        r'! (?:LaTeX|Package|Undefined)[^\n]*']:
            for issue in re.findall(pattern, log_text):
                errors.append('PDF log: ' + issue)

    drift = []
    if args.check_source:
        current = {str(p.relative_to(REPO)) for p in (REPO / 'finfore').rglob('*.py')}
        recorded = set(manifest['source_hashes'])
        for name, expected in manifest['source_hashes'].items():
            path = REPO / name
            if not path.exists():
                drift.append('removed: ' + name)
            elif sha(path) != expected:
                drift.append('changed: ' + name)
        drift += ['added: ' + name for name in sorted(current - recorded)]
    if errors:
        for error in errors:
            print('ERROR:', error)
    else:
        print(f'Document checks passed: {len(chapters)} chapter files, {len(labels)} labels, '
              f'{len(manifest["output_hashes"])} retained artifacts, PDF and build log.')
        print(f'Retained example assertions: {len(results["checks"])}; diagnostic comparisons: 1.')
    if args.check_source:
        if drift:
            print('Application source differs from the recorded baseline:')
            for line in drift:
                print('  ' + line)
        else:
            print('Application source matches the recorded hashes.')
    if errors:
        return 1
    return 2 if drift else 0


if __name__ == '__main__':
    raise SystemExit(main())
