#!/usr/bin/env python3
"""CI lane inventory, runner integration, strict manifest verification, and offline Lua checks.

New Python tests default to the required offline lane. External requirements must be registered in RULES.
Network/BeamNG lanes are inventoried as NOT RUN on hosted CI; they are never counted as passing coverage.
"""
import argparse
from collections import defaultdict
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import time
import unittest

import run_tests

ROOT = Path(__file__).resolve().parents[1]
# Several full synthetic session variants exceed a single module timeout; retain every class as its own unit.
CLASS_UNITS = {'test_server'}
# Longest prefixes first: the synthetic follow drive stays offline, unlike the real-DEM drives.
RULES = {
    'test_integration_drive.FollowDrive512Test': ('offline', 'Synthetic DEM and in-process server'),
    'test_integration_drive': ('network', 'Drive replay downloads DEM on a cold cache; not a real BeamNG drive'),
    'test_e2e': ('network', 'Real server with DEM downloads'),
    'test_imagery.NetworkImageryTest': ('network', 'Live imagery providers'),
    'test_gravity.RealEgm2008Test': ('network', '74 MB EGM2008 download; BNGE_GRAVITY_REAL=1'),
    'test_gasstations.RealSpotsTest': ('network', 'Real Overture data; BNGE_GAS_REAL=1'),
    'test_roadgeo.RealDataTest': ('network', 'Requires populated local map-data cache'),
    'test_fargeo.RealDataTest': ('network', 'Requires populated local map-data cache'),
    'test_mesh.TexturedShapeTest.test_uv2_convention_matches_engine': ('beamng', 'Installed BeamNG art_shapes.zip'),
    'test_colormatch.DetectTest.test_gpu_matches_cpu': ('beamng', 'GPU-dependent comparison; hosted CPU CI cannot verify it'),
    'test_deshadow.TimingTest': ('beamng', 'Opt-in performance probe; needs controlled hardware, DESHADOW_TIMING=1'),
    'test_peerwork.GroupTest': ('disabled', 'Shared-world building intentionally disabled in source'),
    'test_cache.ServerCacheTest.test_share_port': ('disabled', 'Shared-world building intentionally disabled in source'),
}


def classify(test_id):
    for prefix in sorted(RULES, key=len, reverse=True):
        if test_id == prefix or test_id.startswith(prefix + '.'):
            return RULES[prefix]
    return 'offline', 'Synthetic/local fixture test'


def flatten(suite):
    for test in suite:
        if isinstance(test, unittest.TestSuite):
            yield from flatten(test)
        else:
            yield test


def inventory():
    loader = unittest.TestLoader()
    suite = loader.discover(str(ROOT / 'server/tests'))
    if loader.errors:
        raise RuntimeError('\n'.join(loader.errors))
    ids = sorted({test.id() for test in flatten(suite)})
    discovered = {test.split('.')[0] for test in ids}
    modules = {p.stem for p in (ROOT / 'server/tests').glob('test_*.py')}
    if missing := modules - discovered:
        raise RuntimeError('Modules with no discovered tests: ' + ', '.join(sorted(missing)))
    for prefix in RULES:
        if not any(t == prefix or t.startswith(prefix + '.') for t in ids):
            raise RuntimeError('Stale lane rule: ' + prefix)
    return plan(ids)


def plan(ids):
    """Select the largest homogeneous module/class/method units, preserving unittest fixtures and grouping."""
    lanes = {lane: {'targets': [], 'tests': [], 'reasons': {}} for lane in ('offline', 'network', 'beamng', 'disabled')}
    def group(prefix, tests):
        kinds = {classify(t)[0] for t in tests}
        if len(kinds) == 1 and prefix not in CLASS_UNITS:
            lane = lanes[kinds.pop()]
            lane['targets'].append(prefix)
            lane['tests'].extend(tests)
            lane['reasons'][prefix] = sorted({classify(t)[1] for t in tests})
            return
        children = defaultdict(list)
        depth = len(prefix.split('.')) + 1
        for t in tests:
            children['.'.join(t.split('.')[:depth])].append(t)
        for child, cases in sorted(children.items()):
            group(child, cases)
    modules = defaultdict(list)
    for test_id in sorted(ids):
        modules[test_id.split('.')[0]].append(test_id)
    for module, tests in sorted(modules.items()):
        group(module, tests)
    return {'schema_version': 1, 'lanes': lanes}


def write(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + '.tmp')
    tmp.write_text(json.dumps(data, indent=2) + '\n', encoding='utf-8')
    tmp.replace(path)


def verify(data, expected):
    """Exit code alone is insufficient: require all planned execution units and non-skipped test outcomes."""
    modules = data.get('modules', [])
    if not expected or len(modules) != len(expected) or {m['module'] for m in modules} != set(expected):
        raise ValueError('Missing, duplicate, or unexpected execution units')
    if not data.get('success') or not data.get('complete') or data.get('status') != 'finished':
        raise ValueError('Run did not finish successfully')
    for m in modules:
        c = m['counts']
        if (m['status'] != 'passed' or not m['complete'] or m['returncode'] != 0 or m['timed_out']
                or c['tests_run'] <= 0 or any(c[k] for k in ('failures', 'errors', 'skipped', 'unexpected_successes'))):
            raise ValueError('Unsuccessful or skipped execution unit: ' + m['module'])
    for key in run_tests.COUNTS:
        if data['counts'][key] != sum(m['counts'][key] for m in modules):
            raise ValueError('Incorrect aggregate count: ' + key)


def python_lane(output):
    # The guard is inherited by test subprocesses too, allowing loopback fixtures but rejecting external sockets.
    guard = str(ROOT / 'tools/ci_offline')
    os.environ['PYTHONPATH'] = guard + os.pathsep + os.environ.get('PYTHONPATH', '')
    from ci_offline.sitecustomize import install
    install()
    catalog = inventory()
    write(output / 'inventory.json', catalog)
    for lane in ('network', 'beamng', 'disabled'):
        write(output / (lane + '.json'), dict(status='not_run', success=False, complete=False,
              reason='Not executed by hosted offline CI; see docs/CI.md for prerequisites', **catalog['lanes'][lane]))
    targets = catalog['lanes']['offline']['targets']
    code = run_tests.Runner(targets, 2, 180, str(output / 'python'), {}).all()
    data = json.loads((output / 'python/results.json').read_text())
    verify(data, targets)
    if data['counts']['tests_run'] != len(catalog['lanes']['offline']['tests']):
        raise ValueError('Executed test count differs from discovery inventory')
    return code


LUA_SUMMARY = re.compile(r'^\S+: (\d+) passed, (\d+) failed$', re.M)


def lua_lane(output):
    paths = sorted((ROOT / 'mod/tests').glob('test_*.lua'))
    names = [p.stem for p in paths]
    runner = run_tests.Runner(names, 1, 90, str(output / 'lua'), {})
    runner.manifest()
    for path in paths:
        name = path.stem
        runner.result(name, status='running')
        start = time.monotonic()
        log = Path(runner.results[name]['log'])
        log.parent.mkdir(parents=True, exist_ok=True)
        try:
            p = subprocess.run(['luajit', str(path)], cwd=ROOT / 'mod/tests', capture_output=True, text=True, timeout=90)
            text = p.stdout + p.stderr
            matches = LUA_SUMMARY.findall(text)
            passed, failed = map(int, matches[-1]) if len(matches) == 1 else (0, 0)
            status = 'passed' if p.returncode == 0 and passed > 0 and failed == 0 else 'failed'
            fields = dict(returncode=p.returncode, complete=bool(matches), status=status,
                          counts=dict(tests_run=passed + failed, passed=passed, failures=failed, errors=0,
                                      skipped=0, expected_failures=0, unexpected_successes=0))
        except subprocess.TimeoutExpired as e:
            text = (e.stdout or b'').decode(errors='replace') + (e.stderr or b'').decode(errors='replace')
            fields = dict(status='timed_out', timed_out=True)
        except OSError as e:
            text = str(e)
            fields = dict(status='error', error=str(e))
        log.write_text(text, encoding='utf-8')
        runner.result(name, duration_seconds=round(time.monotonic() - start, 3), **fields)
    data = runner.manifest('finished')
    verify(data, names)
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('lane', choices=('python', 'lua'))
    ap.add_argument('--output', type=Path, default=ROOT / 'build/ci')
    args = ap.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    def interrupt(signum, frame):
        raise KeyboardInterrupt

    previous = signal.signal(signal.SIGTERM, interrupt)
    try:
        return python_lane(args.output) if args.lane == 'python' else lua_lane(args.output)
    finally:
        signal.signal(signal.SIGTERM, previous)


if __name__ == '__main__':
    sys.exit(main())
