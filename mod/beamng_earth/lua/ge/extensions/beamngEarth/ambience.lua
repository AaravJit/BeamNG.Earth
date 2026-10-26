-- Forest wind and room tone (2026-10-26, the user: "Start with 1"): the stock maps' ambience
-- (lua/ge/extensions/core/audioForest.lua: one wind emitter at the trees round the listener, louder and denser with
-- more and bigger trees, and a room tone everywhere) fed from the vegetation tiles' sound nodes (server
-- vegetation._ambience: a tile's trees and bushes in ~21 m squares, its ambience set by climate) instead of a
-- level's Forest: ours are many small ones made and dropped as the player moves, the stock module reads one at load.
local M = {}

local kdTree = nil       -- the stock kdtreepoint3d (required at the first cloud: not there outside the game)

M.RADIUS_M = 600      -- the node cloud: the nodes this close to the listener (the stock emitter hears 150 m) ...
M.MOVE_M = 300        -- ... made again once the listener is this far from where it was made, or a tile near changed
M.GAP_S = 1           -- at most this often
M.VOTE_M = 1000       -- tiles this close choose the ambience set, by their sound items
M.SWITCH = 1.5        -- another set takes over once it has this many times the current one's items
M.PITCH_FULL = 45     -- items of a node at full density (pitch 1). Measured 2026-10-26: East Coast USA's own cloud at
                      -- its forest spots gives volume 0.23, pitch 0.29, colour 0.45; ours in a Pennsylvania forest
                      -- 0.26, 0.14 at 90 (the stock nodes count a 20 m circle, ours a ~21 m square), 0.43
M.GHOST_MAX_S = 120   -- a rebase's previous tiles stay at most this long (trees.lua GHOST_MAX_S)
local TILE = 256

-- [set][variant + 1] = { wind, room tone }: the server's sets (native ambience.rs: 0 temperate, 1 Mediterranean,
-- 2 desert, 3 tropical) as the stock maps' (East Coast USA's _01, West Coast USA's _02; Italy's; Utah's _01,
-- Johnson Valley's _02; Jungle Rock Island's)
local W, R = 'event:>Ambient>Wind>Wind_', 'event:>Ambient>Roomtones>Roomtone_'
M.SETS = {
  [0] = { { W .. 'ConiferousDeciduous_01', R .. 'ConiferousDeciduous_01' },
          { W .. 'ConiferousDeciduous_02', R .. 'ConiferousDeciduous_02' } },
  [1] = { { W .. 'Mediterranean_01', R .. 'Mediterranean_01' }, { W .. 'Mediterranean_02', R .. 'Mediterranean_02' } },
  [2] = { { W .. 'Desert_01', R .. 'Desert_01' }, { W .. 'Desert_02', R .. 'Desert_02' } },
  [3] = { { W .. 'Tropical', R .. 'Tropical' } },
}

local tiles = {}        -- "anchorId:key" -> { nodes (flat x, y, z, depth, size, count rows), set, weight, cx, cy, gx, gy, ghost }
local dirty = false
local builtAt = nil     -- { x, y } where the cloud was made
local lastBuild = -1e9
local current = nil     -- { wind, roomtone } playing
local roomtone = nil    -- its SFXEmitter (ours: it follows the listener; the stock one stays at the level's origin)
local listener = vec3()
M.nodes, M.builds, M.buildMs = 0, 0, 0

-- Pure: the events of a tile's biome { set, variant } (nil for an unknown set).
function M.events(biome)
  local s = M.SETS[tonumber(biome and biome[1]) or -1]
  if not s then return nil end
  return s[(tonumber(biome[2]) or 0) + 1] or s[1]
end

-- A vegetation tile's ambience (core.lua, after trees.lua read its file: data.audio, data.biome).
function M.onTile(anchorId, key, data)
  if type(data) ~= 'table' or type(data.biome) ~= 'table' then return end
  local nodes = type(data.audio) == 'table' and data.audio or {}
  local w = 0
  for i = 6, #nodes, 6 do w = w + (nodes[i] or 0) end
  local tx, ty = tostring(key):match('(%-?%d+)_(%-?%d+)$')
  tx, ty = tonumber(tx) or 0, tonumber(ty) or 0
  tiles[anchorId .. ':' .. tostring(key)] = { anchor = anchorId, nodes = nodes, biome = data.biome, weight = w,
    cx = tx * TILE + TILE / 2, cy = ty * TILE + TILE / 2, gx = tx, gy = ty }
  dirty = true
end

function M.onDrop(anchorId, key)
  if tiles[anchorId .. ':' .. tostring(key)] then
    tiles[anchorId .. ':' .. tostring(key)] = nil
    dirty = true
  end
end

-- Origin rebase (core.lua finishRebase): the tiles' nodes move with the world (p_new = R*p + t) and stay as ghosts
-- until the new anchor's tiles cover them (as trees.lua's).
function M.transform(xf)
  for _, t in pairs(tiles) do
    local a = t.nodes
    for i = 1, #a, 6 do
      a[i], a[i + 1], a[i + 2] = xf:apply(a[i], a[i + 1], a[i + 2])
    end
    t.cx, t.cy = xf:apply(t.cx, t.cy, 0)
    t.ghost = t.ghost or os.clockhp()
  end
  dirty = true
end

local function covered()
  local c = {}
  for _, t in pairs(tiles) do
    if not t.ghost then c[t.gx .. '_' .. t.gy] = true end
  end
  return c
end

local function heard(t, cov)
  return not (t.ghost and cov[math.floor(t.cx / TILE) .. '_' .. math.floor(t.cy / TILE)])
end

-- Pure: the ambience set the tiles near (x, y) choose: { set, variant } with the most sound items (every tile
-- counts a little: a place without trees still has its set), kept unless another has M.SWITCH times its items.
function M.vote(list, x, y, keep)
  local by = {}
  for _, t in ipairs(list) do
    if math.max(math.abs(t.cx - x), math.abs(t.cy - y)) <= M.VOTE_M then
      local k = tostring(t.biome[1]) .. ':' .. tostring(t.biome[2] or 0)
      local e = by[k] or { biome = t.biome, w = 0 }
      e.w = e.w + t.weight + 0.001
      by[k] = e
    end
  end
  local best = nil
  for _, e in pairs(by) do
    if not best or e.w > best.w then best = e end
  end
  if not best then return keep end
  if keep then
    local k = by[tostring(keep[1]) .. ':' .. tostring(keep[2] or 0)]
    if k and best.w < k.w * M.SWITCH then return keep end
  end
  return best.biome
end

local function forest()
  return extensions and extensions.core_audioForest
end

-- The room tone emitter of event ev (none for nil): a new one for another event (an SFXEmitter given another track
-- kept the old one playing, and stop() no longer stopped it: recorded 2026-10-26).
local function setRoomtone(ev)
  if roomtone and simObjectExists(roomtone) then roomtone:delete() end
  roomtone = nil
  if not ev then return end
  local e = Sim.upcast(createObject('SFXEmitter'))
  if not e then return end
  e:setTransform(MatrixF(true))
  e:setField('track', 0, ev)
  e.canSave = false
  e:registerObject('bngEarthRoomtone_' .. Engine.generateUUID())
  e:play()
  roomtone = e
end

local biome = nil
local function build(x, y)
  local t0 = os.clockhp()
  local cov = covered()
  local list = {}
  for _, t in pairs(tiles) do
    if heard(t, cov) then list[#list + 1] = t end
  end
  local r = M.RADIUS_M
  local r2, reach = r * r, r + TILE * 0.71
  local X, Y, Z, D, S, C = {}, {}, {}, {}, {}, {}
  local n = 0
  for _, t in ipairs(list) do
    if math.abs(t.cx - x) <= reach and math.abs(t.cy - y) <= reach then
      local a = t.nodes
      for i = 1, #a, 6 do
        local dx, dy = a[i] - x, a[i + 1] - y
        if dx * dx + dy * dy <= r2 then
          n = n + 1
          X[n], Y[n], Z[n], D[n], S[n], C[n] = a[i], a[i + 1], a[i + 2], a[i + 3], a[i + 4], a[i + 5]
        end
      end
    end
  end
  biome = M.vote(list, x, y, biome)
  local ev = M.events(biome)
  local af = forest()
  if af then
    local kd = nil
    if n > 0 then
      kdTree = kdTree or require('kdtreepoint3d')
      kd = kdTree.new(n)
      for i = 1, n do kd:preLoad(i, X[i], Y[i], Z[i] + D[i]) end
      kd:build()
    end
    M.lastCloud = { nodeCount = n, nodeX = X, nodeY = Y, nodeZ = Z, nodeDepth = D, nodeAvgNormVol = S,
      nodeSourceCount = C, pitchDensityInv = 1 / M.PITCH_FULL, kdNodes = kd }
    af.setNodeCloud(M.lastCloud)
    if ev and (not current or current[1] ~= ev[1]) then
      af.setEnabled(false)                  -- (a new emitter for it: setRoomtone)
      af.setMainEvent(ev[1])
    end
    af.setEnabled(n > 0 and ev ~= nil)
  end
  if not current or not ev or current[2] ~= (ev and ev[2]) then setRoomtone(ev and ev[2]) end
  current = ev
  M.nodes, M.builds, M.buildMs = n, M.builds + 1, (os.clockhp() - t0) * 1000
end

-- Every frame (core.lua): the cloud made again when the listener has moved or the tiles near it changed; the room
-- tone follows the listener. Another anchor's tiles go (a teleport), but a rebase's ghosts.
function M.update(anchorId)
  if not next(tiles) and not current then return end
  listener.x, listener.y, listener.z = SFXSystem.getPositionXYZ()
  local now = os.clockhp()
  for k, t in pairs(tiles) do
    if (t.ghost and now - t.ghost > M.GHOST_MAX_S) or (not t.ghost and t.anchor ~= anchorId) then
      tiles[k] = nil
      dirty = true
    end
  end
  if roomtone then
    if simObjectExists(roomtone) then roomtone:setPosition(listener) else roomtone, current = nil, nil; dirty = true end
  end
  if now - lastBuild < M.GAP_S then return end
  local moved = not builtAt or math.max(math.abs(listener.x - builtAt.x), math.abs(listener.y - builtAt.y)) > M.MOVE_M
  if not (moved or dirty) then return end
  dirty, lastBuild = false, now
  builtAt = { x = listener.x, y = listener.y }
  build(listener.x, listener.y)
end

-- The server's tiles go (disconnect): the ambience with them.
function M.clear()
  tiles, dirty, builtAt, biome, current = {}, false, nil, nil, nil
  setRoomtone(nil)
  local af = forest()
  if af then af.setEnabled(false) end
end

-- The level goes.
function M.reset()
  M.clear()
end

function M.status()
  local n = 0
  for _ in pairs(tiles) do n = n + 1 end
  return { tiles = n, nodes = M.nodes, builds = M.builds, buildMs = M.buildMs, wind = current and current[1],
    roomtone = current and current[2] }
end

return M
