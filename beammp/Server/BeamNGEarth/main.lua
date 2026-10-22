-- BeamNG.Earth's BeamMP server plugin (Resources/Server/BeamNGEarth/main.lua; BeamMP server Lua 5.3).
--
-- BeamMP sends every car's position in its driver's world coordinates, and BeamNG.Earth's world coordinates are
-- metres from an origin (the "anchor") that each player's game picks for itself and moves as they drive (a rebase
-- every 15 km). Players only see each other in the right place when they share one anchor. This plugin keeps every
-- player's anchor and position (the client's bngePose, every 2 s) and:
--   - groups the players: the same anchor = one group; the clients hide other groups' cars (they would stand in the
--     wrong place);
--   - merges groups that meet: players of different groups within MERGE_M of each other (linked in a chain) all move
--     to one anchor (bngeRebase: the client's smooth rebase), the group with the most players, then the oldest, whose
--     anchor is within MAX_ANCHOR_M of all of them (else the merge waits);
--   - sends a player who joins to the others (bngeGoTo: the place of the first player of the biggest group).
-- Positions are latitude / longitude: no player's coordinates are converted here. Nothing of BeamMP itself is
-- changed: only its events (MP.RegisterEvent, MP.TriggerClientEvent).
local G = {}

G.PROTO = 1
G.MERGE_M = 3000
G.MAX_ANCHOR_M = 12000      -- (the client rebases on its own at 15 km from its anchor)
G.STALE_S = 15              -- a player's pose this old: left out of the grouping
G.TICK_MS = 2000

G.players = {}              -- pid -> { name, aLat, aLon, lat, lon, t, joinedAt }
G.anchorSeen = {}           -- anchor key -> the time first seen (older wins a merge tie)
G.clock = 0

function G.key(aLat, aLon) return string.format('%.6f,%.6f', aLat, aLon) end

-- Metres between two latitude / longitude points (equirectangular: the distances here are a few km).
function G.dist(lat1, lon1, lat2, lon2)
  local k = math.pi / 180
  local x = (lon2 - lon1) * k * math.cos((lat1 + lat2) / 2 * k)
  local y = (lat2 - lat1) * k
  return 6371000 * math.sqrt(x * x + y * y)
end

-- Pure: players { pid -> { aLat, aLon, lat, lon } }, seen { key -> time } -> groups { pid -> key }, moves
-- { pid -> { aLat, aLon } } (the players to move to another anchor).
function G.plan(players, seen)
  local ids = {}
  for pid, p in pairs(players) do
    if p.aLat and p.lat then ids[#ids + 1] = pid end
  end
  table.sort(ids)
  local groups = {}
  for _, pid in ipairs(ids) do groups[pid] = G.key(players[pid].aLat, players[pid].aLon) end
  -- clusters: players within MERGE_M of each other, chained
  local parent = {}
  for _, pid in ipairs(ids) do parent[pid] = pid end
  local function root(i)
    while parent[i] ~= i do parent[i] = parent[parent[i]]; i = parent[i] end
    return i
  end
  for a = 1, #ids do
    for b = a + 1, #ids do
      local p, q = players[ids[a]], players[ids[b]]
      if G.dist(p.lat, p.lon, q.lat, q.lon) <= G.MERGE_M then parent[root(ids[a])] = root(ids[b]) end
    end
  end
  local clusters, order = {}, {}
  for _, pid in ipairs(ids) do
    local r = root(pid)
    if not clusters[r] then clusters[r] = {}; order[#order + 1] = r end
    table.insert(clusters[r], pid)
  end
  local moves = {}
  for _, r in ipairs(order) do
    local members = clusters[r]
    local anchors, count = {}, {}
    for _, pid in ipairs(members) do
      local k = groups[pid]
      if not count[k] then
        count[k] = 0
        anchors[#anchors + 1] = { key = k, lat = players[pid].aLat, lon = players[pid].aLon }
      end
      count[k] = count[k] + 1
    end
    if #anchors > 1 then
      table.sort(anchors, function(x, y)
        if count[x.key] ~= count[y.key] then return count[x.key] > count[y.key] end
        local sx, sy = seen[x.key] or math.huge, seen[y.key] or math.huge
        if sx ~= sy then return sx < sy end
        return x.key < y.key
      end)
      local target = nil
      for _, a in ipairs(anchors) do
        local ok = true
        for _, pid in ipairs(members) do
          local p = players[pid]
          if G.dist(a.lat, a.lon, p.lat, p.lon) > G.MAX_ANCHOR_M then ok = false; break end
        end
        if ok then target = a; break end
      end
      if target then
        for _, pid in ipairs(members) do
          if groups[pid] ~= target.key then moves[pid] = { aLat = target.lat, aLon = target.lon } end
        end
      end
    end
  end
  return groups, moves
end

-- Pure: where a joining player goes: the place of the longest-joined player of the biggest group (nil: alone).
-- { lat, lon } or nil
function G.meetingPlace(players, groups, newPid)
  local size = {}
  for pid, k in pairs(groups) do if pid ~= newPid then size[k] = (size[k] or 0) + 1 end end
  local best, bestPid = nil, nil
  for pid, k in pairs(groups) do
    if pid ~= newPid then
      local p = players[pid]
      if not best or size[k] > size[groups[best]]
        or (size[k] == size[groups[best]] and (p.joinedAt or 0) < (players[best].joinedAt or 0)) then
        best = pid
      end
    end
  end
  if not best then return nil end
  bestPid = best
  return { lat = players[bestPid].lat, lon = players[bestPid].lon }
end

-- A client's pose { v, aLat, aLon, lat, lon, joined }; returns the meeting place to send it on its first pose.
function G.onPose(pid, d, name)
  if type(d) ~= 'table' or tonumber(d.v) ~= G.PROTO then return nil end
  local aLat, aLon, lat, lon = tonumber(d.aLat), tonumber(d.aLon), tonumber(d.lat), tonumber(d.lon)
  if not (aLat and aLon and lat and lon) or math.abs(lat) > 90 or math.abs(lon) > 180 then return nil end
  local p = G.players[pid]
  local first = p == nil
  if first then
    p = { joinedAt = G.clock }
    G.players[pid] = p
  end
  p.name, p.aLat, p.aLon, p.lat, p.lon, p.t = name, aLat, aLon, lat, lon, G.clock
  local k = G.key(aLat, aLon)
  if not G.anchorSeen[k] then G.anchorSeen[k] = G.clock end
  if first and d.joined == true then
    local live = G.live()
    local groups = G.plan(live, G.anchorSeen)
    local place = G.meetingPlace(live, groups, pid)
    if place and G.dist(place.lat, place.lon, lat, lon) > G.MERGE_M then return place end
  end
  return nil
end

-- The players with a pose newer than STALE_S.
function G.live()
  local out = {}
  for pid, p in pairs(G.players) do
    if G.clock - (p.t or -math.huge) <= G.STALE_S then out[pid] = p end
  end
  return out
end

function G.onLeave(pid) G.players[pid] = nil end

-- Every TICK_MS: { groups, moves } for the clients.
function G.tick(dt)
  G.clock = G.clock + (dt or 0)
  return G.plan(G.live(), G.anchorSeen)
end

-- ---------------------------------------------------------------------------------------------------------------
-- BeamMP wiring (absent in the offline tests)
if MP and MP.RegisterEvent then
  local function encode(t) return Util.JsonEncode(t) end

  function BNGE_onPose(pid, data)
    local ok, d = pcall(Util.JsonDecode, data)
    if not ok then return end
    local known = G.players[pid] ~= nil
    local place = G.onPose(pid, d, MP.GetPlayerName(pid))
    local p = G.players[pid]
    if p and not known then
      print(string.format('[BeamNG.Earth] %s at %.5f, %.5f (anchor %s)', tostring(p.name), p.lat, p.lon,
        G.key(p.aLat, p.aLon)))
    end
    if place then
      print(string.format('[BeamNG.Earth] %s sent to the others at %.5f, %.5f', tostring(p and p.name), place.lat,
        place.lon))
      MP.TriggerClientEvent(pid, 'bngeGoTo', encode(place))
    end
  end

  function BNGE_onLeave(pid) G.onLeave(pid) end

  function BNGE_tick()
    local groups, moves = G.tick(G.TICK_MS / 1000)
    local g = {}
    for pid, k in pairs(groups) do g[tostring(pid)] = k end
    MP.TriggerClientEvent(-1, 'bngeGroups', encode(g))
    for pid, a in pairs(moves) do
      local k = G.key(a.aLat, a.aLon)
      local p = G.players[pid]
      if p and p.told ~= k then
        p.told = k
        print(string.format('[BeamNG.Earth] %s joins the group at anchor %s', tostring(p.name), k))
      end
      MP.TriggerClientEvent(pid, 'bngeRebase', encode(a))
    end
  end

  MP.RegisterEvent('bngePose', 'BNGE_onPose')
  MP.RegisterEvent('onPlayerDisconnect', 'BNGE_onLeave')
  MP.RegisterEvent('BNGE_tick', 'BNGE_tick')
  MP.CreateEventTimer('BNGE_tick', G.TICK_MS)
  print('[BeamNG.Earth] plugin loaded')
end

return G
