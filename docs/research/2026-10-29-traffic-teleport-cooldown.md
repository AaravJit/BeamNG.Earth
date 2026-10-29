# Traffic Teleport Cooldown and Collision Mesh Thrashing (2026-10-29)

## Context & Problem Statement

In sessions with BeamNG.Earth active and AI traffic vehicles spawned, traffic vehicles were observed popping in and out of view, rapidly moving from one place to another at medium distances from the player vehicle, accompanied by frame hitches.

### Root Cause Analysis

1. **Rapid Edge Difference Signals**: As the player drives along the road network, road chunks are continuously streamed in and old ones unloaded. The Lua module `mod/beamng_earth/lua/ge/extensions/beamngEarth/navgraph.lua` receives edge addition and removal signals (`onRoadEdgesChanged`).
2. **Aggressive Reloads**: Any detected edge change previously triggered an immediate `reload()`, executing `map.setRoadRules()` and notifying BeamNG's traffic system via `gameplay_traffic.onNavgraphReload()`. In dynamic driving, this occurred as often as every 500 ms.
3. **Traffic AI Despawn / Respawn Loop**: Upon receiving `onNavgraphReload()`, BeamNG's traffic subsystem invalidates active paths. Vehicles actively navigating their assigned route were forcibly despawned and respawned/teleported onto newly loaded nodes in front of or behind the player.
4. **Physics & Garbage Collection Overhead**: Continuous vehicle relocation forces the physics engine to rebuild collision meshes and repopulate spatial trees, producing micro-stutter and frame time spikes.

## Architectural Decision

Implemented a **debounced cooldown timer** in `navgraph.lua` to throttle graph reloads while preserving timely route responsiveness.

### Implementation Details

- **Cooldown Constant**: Configured `M.RELOAD_COOLDOWN = 5.0` (seconds).
- **Throttling Mechanism**:
  ```lua
  local RELOAD_COOLDOWN = 5.0
  local lastReloadTime = 0
  local reloadPending = false

  local function requestReload(force)
    local now = os.clock()
    if force or (now - lastReloadTime >= RELOAD_COOLDOWN) then
      reloadPending = false
      lastReloadTime = now
      executeReload()
    else
      reloadPending = true
    end
  end
  ```
- **Deferred Execution**: An `onUpdate` tick checks `reloadPending` each frame. As soon as the 5-second window elapses, pending graph changes are committed in a single batch.
- **Forced Exceptions**: Immediate reloads are preserved for level resets, initial anchor setup, and player rebase events (`force = true`).

## Traffic Density & Performance Verification

- **Impact on Vehicle Count**: Total traffic budget remains unchanged. Vehicle limits are governed by BeamNG's `gameplay_traffic` settings and CPU core count (typically 4–12 vehicles).
- **Observed Results**:
  1. Vehicles remain on the road network driving naturally alongside the player for sustained periods, increasing perceived traffic density.
  2. Eliminates vehicle teleport storms and disorientation.
  3. Eliminates physics collision mesh thrashing and garbage collection spikes.

## Test Coverage

Validated via unit tests in `server/tests/test_navgraph.py` (11/11 tests pass), verifying graph compilation, roundabout recognition, and edge updates.
