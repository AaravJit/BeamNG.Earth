"""CI classification, manifest gates, offline socket policy and workflow invariants."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

import yaml

TOOLS = Path(__file__).resolve().parents[2] / 'tools'
sys.path.insert(0, str(TOOLS))
import ci_tests as ci  # noqa: E402


def successful():
    counts = dict(tests_run=1, passed=1, failures=0, errors=0, skipped=0,
                  expected_failures=0, unexpected_successes=0)
    return dict(status='finished', success=True, complete=True, counts=counts,
                modules=[dict(module='test_one', status='passed', complete=True, returncode=0,
                              timed_out=False, counts=counts.copy())])


class CiTest(unittest.TestCase):
    def test_hybrid_modules_are_partitioned_without_omissions_or_duplicates(self):
        ids = ['test_imagery.NetworkImageryTest.test_live', 'test_imagery.Local.test_fake',
               'test_mesh.TexturedShapeTest.test_uv2_convention_matches_engine',
               'test_mesh.TexturedShapeTest.test_local', 'test_simple.T.test_a', 'test_simple.T.test_b',
               'test_integration_drive.FollowDrive512Test.test_synthetic',
               'test_integration_drive.MeshDriveTest.test_live',
               'test_cache.ServerCacheTest.test_share_port',
               'test_server.A.test_session', 'test_server.B.test_session']
        plan = ci.plan(ids)['lanes']
        all_ids = [t for lane in plan.values() for t in lane['tests']]
        self.assertCountEqual(all_ids, ids)
        self.assertEqual(len(all_ids), len(set(all_ids)))
        self.assertIn('test_simple', plan['offline']['targets'])
        self.assertNotIn('test_server', plan['offline']['targets'])
        self.assertIn('test_server.A', plan['offline']['targets'])
        self.assertIn('test_server.B', plan['offline']['targets'])
        self.assertIn('test_imagery.Local', plan['offline']['targets'])
        self.assertIn('test_mesh.TexturedShapeTest.test_local', plan['offline']['targets'])
        self.assertEqual(len(plan['network']['tests']), 2)
        self.assertEqual(len(plan['beamng']['tests']), 1)
        self.assertEqual(len(plan['disabled']['tests']), 1)

    def test_inventory_rejects_an_importable_empty_module(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            tests = root / 'server/tests'
            tests.mkdir(parents=True)
            (tests / 'test_empty.py').write_text('# no tests\n')
            with mock.patch.object(ci, 'ROOT', root):
                with self.assertRaisesRegex(RuntimeError, 'Modules with no discovered tests: test_empty'):
                    ci.inventory()

    def test_manifest_gate_rejects_incomplete_timeout_empty_and_skipped_units(self):
        ci.verify(successful(), ['test_one'])
        for field, value in [('status', 'failed'), ('complete', False), ('returncode', 1), ('timed_out', True)]:
            with self.subTest(field=field):
                data = successful()
                data['modules'][0][field] = value
                with self.assertRaises(ValueError): ci.verify(data, ['test_one'])
        for field in ('tests_run', 'failures', 'errors', 'skipped', 'unexpected_successes'):
            with self.subTest(count=field):
                data = successful()
                data['modules'][0]['counts'][field] = 0 if field == 'tests_run' else 1
                with self.assertRaises(ValueError): ci.verify(data, ['test_one'])
        for field in ('success', 'complete'):
            data = successful()
            data[field] = False
            with self.assertRaises(ValueError): ci.verify(data, ['test_one'])

    def test_manifest_gate_requires_inventory_and_correct_totals(self):
        for expected in ([], ['test_other'], ['test_one', 'test_missing']):
            with self.assertRaises(ValueError): ci.verify(successful(), expected)
        data = successful()
        data['modules'] *= 2
        with self.assertRaises(ValueError): ci.verify(data, ['test_one'])
        data = successful()
        data['counts']['passed'] += 1
        with self.assertRaises(ValueError): ci.verify(data, ['test_one'])

    def test_socket_guard_allows_only_loopback_and_unix_sockets(self):
        # Isolate the irreversible audit hook in a child; no network is actually contacted.
        code = f'''import sys; sys.path.insert(0, {str(TOOLS)!r})
from ci_offline.sitecustomize import check
for address in [('127.0.0.1', 42), ('::1', 42), '/tmp/socket']:
 check('socket.connect', (None, address))
for host in ['localhost', '127.0.0.1', '::1']:
 check('socket.getaddrinfo', (host,))
for event, args in [('socket.connect', (None, ('8.8.8.8', 42))), ('socket.getaddrinfo', ('example.com',))]:
 try: check(event, args)
 except OSError: pass
 else: raise AssertionError('external access permitted')
'''
        p = subprocess.run([sys.executable, '-c', code], capture_output=True, text=True, timeout=10)
        self.assertEqual(p.returncode, 0, p.stderr)

    def test_lua_missing_summary_or_zero_checks_cannot_pass(self):
        for stdout in ('', 'test_fake: 0 passed, 0 failed\n', 'test_fake: 1 passed, 1 failed\n'):
            with tempfile.TemporaryDirectory() as tmp, mock.patch.object(Path, 'glob', return_value=[Path('test_fake.lua')]), \
                    mock.patch.object(ci.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, stdout, '')):
                with self.assertRaises(ValueError): ci.lua_lane(Path(tmp))
                data = json.loads((Path(tmp) / 'lua/results.json').read_text())
                self.assertFalse(data['success'])

    def test_lua_timeout_is_durable_and_unsuccessful(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(Path, 'glob', return_value=[Path('test_fake.lua')]), \
                mock.patch.object(ci.subprocess, 'run', side_effect=subprocess.TimeoutExpired('luajit', 90, b'partial')):
            with self.assertRaises(ValueError): ci.lua_lane(Path(tmp))
            data = json.loads((Path(tmp) / 'lua/results.json').read_text())
            self.assertEqual(data['modules'][0]['status'], 'timed_out')
            self.assertTrue(data['modules'][0]['timed_out'])
            self.assertFalse(data['success'])
            self.assertEqual((Path(tmp) / 'lua/test_fake.log').read_text(), 'partial')

    def test_workflow_runs_required_checks_and_always_uploads_artifacts(self):
        workflow = yaml.load((TOOLS.parent / '.github/workflows/tests.yml').read_text(), Loader=yaml.BaseLoader)
        self.assertIn('pull_request', workflow['on'])
        self.assertEqual(workflow['on']['push']['branches'], ['main'])
        self.assertEqual(workflow['permissions'], {'contents': 'read'})
        self.assertEqual(workflow['defaults']['run']['shell'], 'bash', 'pipelines must use pipefail')
        for name, job in workflow['jobs'].items():
            self.assertNotIn('continue-on-error', job)
            self.assertLessEqual(int(job['timeout-minutes']), 30)
            upload = [s for s in job['steps'] if s.get('uses', '').startswith('actions/upload-artifact@')]
            self.assertEqual(len(upload), 1)
            self.assertEqual(upload[0]['if'], 'always()')
            self.assertTrue(any('tools/ci_tests.py' in s.get('run', '') for s in job['steps']))


if __name__ == '__main__':
    unittest.main()
