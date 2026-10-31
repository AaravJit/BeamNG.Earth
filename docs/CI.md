# Pull-request CI

`.github/workflows/tests.yml` runs on pull requests and pushes to `main`. It has two required, ordinary Linux
jobs: **Required offline Python** and **Required offline Lua (stubbed engine)**. Neither uses
`continue-on-error`. Configure these two check names in branch protection if merges must require CI; a workflow
file alone cannot enable repository branch-protection rules. This change does not alter the release workflow.

| Lane | Automatic execution | Coverage and prerequisites |
| --- | --- | --- |
| Offline Python | Required | All discovered Python tests except the explicit external/disabled rules in `tools/ci_tests.py`. Python 3.12, application requirements, fonttools, osmium, native Rust kernels, LuaJIT (bridge checks), Node 22 (map math). |
| Offline Lua | Required | Every `mod/tests/test_*.lua`, run from `mod/tests` with LuaJIT and engine stubs. This does not run BeamNG. |
| Network / cached data | **NOT RUN** | Real imagery, EGM2008, Overture stations, populated map-data caches, end-to-end DEM downloads, and drive replays that need a cold-cache download. Enumerated in `network.json`; no passing network coverage is claimed. |
| BeamNG / hardware | **NOT RUN** | A stock shape compared with installed BeamNG assets, GPU comparison, and opt-in timing probe. Hosted CI has no licensed game assets or controlled hardware. Enumerated in `beamng.json`; no passing engine/hardware coverage is claimed. |

Intentionally disabled shared-world tests are recorded separately in `disabled.json`. They are not executed
or reported as passing. The synthetic `FollowDrive512Test` remains in the offline lane; the other integration
drives use network DEM data. Node-backed map-math checks run through the existing Python test wrapper.

## Selection and results

The Python lane discovers **every** `server/tests/test_*.py`; an import failure or empty module fails inventory.
Tests default to offline. Explicit prefixes assign external requirements; stale prefixes fail inventory too.
Mixed modules split into class/method execution units so their offline tests are still required. `inventory.json`
lists every discovered test ID exactly once, its lane, execution targets, and exclusion reasons. New tests that
unexpectedly need services/assets fail offline CI (network access is denied or skips are rejected), prompting an
explicit classification review rather than automatic exclusion.

Offline Python uses the existing `tools/run_tests.py` runner and its atomic schema-1 JSON manifest. Two workers
and a 180-second per-unit timeout bound CPU/memory usage. It retains process isolation, stack dumps, and live
logs. Python socket audit hooks reject non-loopback connections in the lane and inherited Python children;
loopback/Unix fixtures still work. This is a test guard, not an OS sandbox for native code or other executables.

The lane additionally verifies the complete expected target inventory, aggregate counts, discovered/executed test
count, nonzero test counts, zero exit codes, and no failures/errors/timeouts/unexpected successes. **Any skip in
a required lane fails the gate**, exposing missing optional dependencies. Expected failures keep unittest's
semantics and remain visible in their own count. Lua results use the same manifest shape, with a required
positive check count and exactly one `testlib` summary per file; missing summaries, failures, startup errors and
timeouts fail the lane. A queued/running/incomplete result never satisfies the gate.

Both jobs initialize an unsuccessful setup manifest before installing dependencies and use `always()` artifact
uploads. Artifacts include logs, manifests and (for Python) the inventory and external-lane reports, retained
for seven days. Even if dependency setup fails, its placeholder cannot be mistaken for a passing test run.
A hard runner termination may prevent upload; an artifact that was never produced is not evidence of success.
Workflow shell steps explicitly use Bash with pipefail so `tee` cannot hide failing test/setup commands.

The workflow uses pip and Cargo caches, a single Python/OS configuration, no schedule, and cancels superseded
runs on the same PR/ref. Lua does not install Python application packages or build Rust. Job timeouts are
30 minutes for Python including its native build and 10 minutes for Lua. Setup downloads are outside the
offline test boundary. The network/engine reports are steps of the Python job, avoiding extra hosted jobs
whose only purpose would be to claim unavailable coverage.

## Local validation

```sh
python -m pip install -r server/requirements-ci.txt
python tools/build_native.py
# Install LuaJIT and Node using the platform package manager, then:
python tools/ci_tests.py python --output build/ci
python tools/ci_tests.py lua --output build/ci
python -m unittest discover -s server/tests -p test_ci_tests.py -v
```

To run external tests deliberately, take targets from `network.json` or `beamng.json` and pass them to
`python tools/run_tests.py TARGET ... --logs build/external-tests`. Run outside the offline socket guard.
For real gravity/stations set `BNGE_GRAVITY_REAL=1` / `BNGE_GAS_REAL=1`; cached-data cases require a populated
`~/.cache/beamng-earth/mapdata` directory. The engine comparison expects the game's asset path defined in
`test_mesh.py`; GPU and timing cases need their actual hardware/opt-in configuration. Inspect the resulting
manifest and logs for skips and unmet prerequisites. These manual checks are not hosted CI coverage and do
not replace the required offline checks. No licensed assets, bulk map downloads, credentials, deployment,
or automatic network probes are added to the workflow.
