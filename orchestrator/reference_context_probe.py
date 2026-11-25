"""Read-only public DOM observations in the original full test order; never acceptance."""
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import subprocess
import sys
import unittest

POLICY = 'full-reference-context-diagnostic-v1'
OUTPUT = Path('/tmp/workbench-reference-context.json')


def selected_cases():
    cases = json.loads(os.environ['WORKBENCH_REPAIR_CASES'])
    if (not isinstance(cases, list) or not cases or len(cases) > 8
            or len(cases) != len(set(cases))
            or any(not isinstance(c, str) or not re.fullmatch(r'(?:[A-Za-z_]\w*\.)+[A-Za-z_]\w*', c) for c in cases)):
        raise ValueError('Context observations require distinct literal failed test identities')
    return sorted(cases)


def instrument(spec, marker):
    # Observe semantic DOM only: no input values, storage, HTML, source or credentials.
    return spec + r'''
import { afterEach as __workbenchContextAfterEach } from 'vitest';
__workbenchContextAfterEach(() => {
  try {
    const text = (el, limit) => String(el?.textContent ?? '').replace(/\s+/g, ' ').trim().slice(0, limit);
    const buttons = [...document.querySelectorAll('button,[role="button"]')].map(el => ({
      text: text(el, 80), aria: String(el.getAttribute('aria-label') ?? '').slice(0, 120),
      disabled: Boolean(el.disabled || el.getAttribute('aria-disabled') === 'true')
    }));
    const nav = buttons.filter(b => /page|next|prev|first|last|\d/.test((b.text + ' ' + b.aria).toLowerCase()));
    const rows = [...document.querySelectorAll('tbody tr')];
    console.log(''' + json.dumps(marker) + r''' + JSON.stringify({
      pathname: window.location.pathname,
      body_text: text(document.body, 2200),
      row_count: rows.length, rows: rows.slice(0, 5).map(r => text(r, 240)),
      navigation: nav.slice(-16), buttons: buttons.slice(-8),
      headings: [...document.querySelectorAll('h1,h2,[role="heading"]')].slice(0, 8).map(e => text(e, 100))
    }));
  } catch (_) { /* A diagnostic must not change the original assertion outcome. */ }
});
'''


def run_suite(tests):
    cases = selected_cases()
    sys.path.insert(0, tests)
    sequence, observations = [], []
    active = [None]
    original = subprocess.run

    def observed_run(command, *args, **kwargs):
        spec = kwargs.get('input')
        observe = (active[0] in cases and command == ['node', '/opt/evaluation/run_vitest.mjs']
                   and isinstance(spec, str) and '#workbench-dom' in spec)
        if not observe:
            return original(command, *args, **kwargs)
        marker = 'WORKBENCH_PUBLIC_DOM_' + secrets.token_hex(16) + ' '
        revised = instrument(spec, marker)
        result = original(command, *args, **{**kwargs, 'input': revised})
        snapshots = []
        for line in (result.stdout or '').splitlines():
            if marker in line:
                try:
                    value = json.loads(line.split(marker, 1)[1])
                    if isinstance(value, dict) and len(json.dumps(value)) <= 18000:
                        snapshots.append(value)
                except (ValueError, TypeError):
                    pass
        observations.append({'case': active[0], 'original_spec_sha256': hashlib.sha256(spec.encode()).hexdigest(),
            'instrumented_spec_sha256': hashlib.sha256(revised.encode()).hexdigest(),
            'exit_code': result.returncode, 'snapshots': snapshots[:2]})
        return result

    class Result(unittest.TextTestResult):
        def startTest(self, test):
            active[0] = test.id()
            sequence.append(active[0])
            return super().startTest(test)

        def stopTest(self, test):
            try:
                return super().stopTest(test)
            finally:
                active[0] = None

    subprocess.run = observed_run
    try:
        suite = unittest.defaultTestLoader.discover(tests)
        result = unittest.TextTestRunner(verbosity=2, resultclass=Result).run(suite)
    finally:
        subprocess.run = original
        OUTPUT.write_text(json.dumps({'policy': POLICY, 'cases': cases, 'sequence': sequence,
            'observations': observations, 'acceptance_credit': False}), encoding='utf8')
    return int(not result.wasSuccessful())


def main():
    if len(sys.argv) == 3 and sys.argv[1] == '--child':
        return run_suite(sys.argv[2])
    sys.path.insert(0, '/opt/evaluation')
    import quality_entrypoint as quality
    cases = selected_cases()

    class Probe(quality.Verification):
        def verify_api(self, name, command, environment, jar, **kwargs):
            if name == 'python-unittest':
                command = ['python3', '-I', __file__, '--child', str(quality.TESTS)]
            return super().verify_api(name, command, environment, jar, **kwargs)

        def finish(self):
            self.result['reference_context_probe'] = (json.loads(OUTPUT.read_text(encoding='utf8'))
                if OUTPUT.exists() else {'policy': POLICY, 'cases': cases, 'sequence': [],
                    'observations': [], 'acceptance_credit': False})
            return super().finish()

    quality.Verification = Probe
    sys.argv = [__file__, '/bin/sh', '-lc', 'python3 -m unittest discover -s tests -v']
    return quality.main()


if __name__ == '__main__':
    raise SystemExit(main())
