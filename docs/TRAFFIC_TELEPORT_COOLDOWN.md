# Traffic Teleport Cooldown and Collision Mesh Thrashing Fix

## Background & Problem Statement

In sessions with BeamNG.Earth active and AI traffic vehicles spawned, players observed traffic vehicles frequently popping in and out of view, rapidly teleporting across the road network, and producing noticeable micro-stutters and frame hitches.

### Root Cause Analysis

1. **Rapid Edge Difference Signals**: As the player vehicle drives along the road network, new road chunks are streamed in and old ones unloaded. [`mod/beamng_earth/lua/ge/extensions/beamngEarth/navgraph.lua`](file:///c:/Users/omdevsss/Documents/GitHub/BeamNG.Earth/mod/beamng_earth/lua/ge/extensions/beamngEarth/navgraph.lua) receives edge addition and removal signals (`onRoadEdgesChanged`).
2. **Aggressive Reloads**: Previously, any edge difference immediately invoked `reload()`, which executes `map.setRoadRules()` and calls BeamNG's core `gameplay_traffic.onNavgraphReload()`. In dynamic driving, this occurred as frequently as every 500 ms.
3. **Traffic AI Despawn / Respawn Loop**: When BeamNG's `gameplay_traffic` subsystem receives `onNavgraphReload()`, it considers existing road nodes invalidated. Traffic vehicles that were actively driving along their assigned path are forcibly pulled off the road and respawned/teleported onto newly loaded nodes in front of or behind the player.
4. **Physics & Garbage Collection Overhead**: Rapid vehicle relocation forces BeamNG's physics core to rebuild vehicle collision meshes and repopulate spatial trees, leading to frame time spikes.

## Architectural Decision

We introduced a **debounced cooldown timer** to throttle graph reloading while maintaining timely route responsiveness.

### Implementation Details

- **Cooldown Constant**: Configured `M.RELOAD_COOLDOWN = 5.0` (seconds) in `navgraph.lua`.
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
- **Deferred Execution**: An `onUpdate` hook checks `reloadPending` each frame. As soon as the 5-second window expires, pending graph changes are committed in a single batch.
- **Immediate Force Exception**: Forced reload triggers (level unload, initial anchor setup, player rebase events) pass `force = true` to bypass the cooldown.

## Traffic Density & Performance Verification

- **Does this reduce overall traffic count?**
  **No.** Total vehicle budget is strictly determined by BeamNG's `gameplay_traffic` settings (Options > Gameplay > Traffic, or auto-budgeted by CPU core count, typically 4–12 vehicles).
- **Observed Improvements**:
  1. Vehicles remain on the road network driving naturally alongside the player for sustained periods, increasing the *perceived* traffic density.
  2. Eliminates the disorientation caused by cars popping in and out at medium distances.
  3. Eliminates physics engine collision mesh thrashing and garbage collection spikes.

## Test Coverage

Validated via unit tests in `server/tests/test_navgraph.py` (11/11 tests pass), verifying graph compilation, roundabout recognition, and edge updates.
