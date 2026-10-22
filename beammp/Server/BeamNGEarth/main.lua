-- BeamNG.Earth's BeamMP server plugin (Resources/Server/BeamNGEarth/main.lua; BeamMP server Lua 5.3).
--
-- BeamMP sends every car's position in its driver's world coordinates, and BeamNG.Earth's world coordinates are
-- metres from an origin (the "anchor") that each player's game picks for itself and moves as they drive (a rebase
-- every 15 km). Each client converts the other players' positions from their owner's anchor into its own
-- (mpanchor.lua, from Thai-v4's PR #88); this plugin tells every client where everyone's anchor is:
--   - bngeAnchors { pid -> { aLat, aLon, lat, lon } }: every player's anchor and place (from the client's bngePose,
--     every 2 s and at once when its anchor changes), to everyone each TICK_MS and at once when an anchor changes;
--   - bngeGoTo { lat, lon }: a player who joins is sent to the others (the longest-joined player's place).
-- Positions stay latitude / longitude here: no coordinates are converted on the server. Nothing of BeamMP itself is
-- changed: only its events (MP.RegisterEvent, MP.TriggerClientEvent).
local G = {}

G.PROTO = 2                 -- (2: anchors for the clients' conversion; 1 grouped players by anchor)
G.MEET_M = 3000             -- a joining player farther than this from the others is sent to them
G.STALE_S = 15              -- a player's pose this old: left out
G.TICK_MS = 2000

G.players = {}              -- pid -> { name, aLat, aLon, lat, lon, t, joinedAt }
G.clock = 0

function G.key(aLat, aLon) return string.format('%.6f,%.6f', aLat, aLon) end

-- Metres between two latitude / longitude points (equirectangular: the distances here are a few km).
function G.dist(lat1, lon1, lat2, lon2)
  local k = math.pi / 180
  local x = (lon2 - lon1) * k * math.cos((lat1 + lat2) / 2 * k)
  local y = (lat2 - lat1) * k
  return 6371000 * math.sqrt(x * x + y * y)
end

-- The players with a pose newer than STALE_S.
function G.live()
  local out = {}
  for pid, p in pairs(G.players) do
    if G.clock - (p.t or -math.huge) <= G.STALE_S then out[pid] = p end
  end
  return out
end

-- Pure: the anchors message { tostring(pid) -> { aLat, aLon, lat, lon } } of players.
function G.anchors(players)
  local out = {}
  for pid, p in pairs(players) do
    out[tostring(pid)] = { aLat = p.aLat, aLon = p.aLon, lat = p.lat, lon = p.lon }
  end
  return out
end

-- Pure: where a joining player goes: the place of the longest-joined other player (nil: alone). { lat, lon }
function G.meetingPlace(players, newPid)
  local best = nil
  for pid, p in pairs(players) do
    if pid ~= newPid and (not best or (p.joinedAt or 0) < (players[best].joinedAt or 0)
        or ((p.joinedAt or 0) == (players[best].joinedAt or 0) and pid < best)) then
      best = pid
    end
  end
  return best and { lat = players[best].lat, lon = players[best].lon } or nil
end

-- A client's pose { v, aLat, aLon, lat, lon, joined }. Returns place (the meeting place for its first pose after
-- joining, or nil) and moved (its anchor is new or changed: the anchors go out at once).
function G.onPose(pid, d, name)
  if type(d) ~= 'table' or tonumber(d.v) ~= G.PROTO then return nil, false end
  local aLat, aLon, lat, lon = tonumber(d.aLat), tonumber(d.aLon), tonumber(d.lat), tonumber(d.lon)
  if not (aLat and aLon and lat and lon) or math.abs(lat) > 90 or math.abs(lon) > 180
      or math.abs(aLat) > 90 or math.abs(aLon) > 180 then
    return nil, false
  end
  local p = G.players[pid]
  local first = p == nil
  if first then
    p = { joinedAt = G.clock }
    G.players[pid] = p
  end
  local moved = first or p.aLat ~= aLat or p.aLon ~= aLon
  p.name, p.aLat, p.aLon, p.lat, p.lon, p.t = name, aLat, aLon, lat, lon, G.clock
  if first and d.joined == true then
    local place = G.meetingPlace(G.live(), pid)
    if place and G.dist(place.lat, place.lon, lat, lon) > G.MEET_M then return place, moved end
  end
  return nil, moved
end

function G.onLeave(pid) G.players[pid] = nil end

-- Every TICK_MS: the anchors message for the clients.
function G.tick(dt)
  G.clock = G.clock + (dt or 0)
  return G.anchors(G.live())
end

-- ---------------------------------------------------------------------------------------------------------------
-- BeamMP wiring (absent in the offline tests)
if MP and MP.RegisterEvent then
  local function encode(t) return Util.JsonEncode(t) end

  function BNGE_onPose(pid, data)
    local ok, d = pcall(Util.JsonDecode, data)
    if not ok then return end
    local known = G.players[pid] ~= nil
    local place, moved = G.onPose(pid, d, MP.GetPlayerName(pid))
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
    if moved then MP.TriggerClientEvent(-1, 'bngeAnchors', encode(G.anchors(G.live()))) end
  end

  function BNGE_onLeave(pid) G.onLeave(pid) end

  function BNGE_tick()
    MP.TriggerClientEvent(-1, 'bngeAnchors', encode(G.tick(G.TICK_MS / 1000)))
  end

  MP.RegisterEvent('bngePose', 'BNGE_onPose')
  MP.RegisterEvent('onPlayerDisconnect', 'BNGE_onLeave')
  MP.RegisterEvent('BNGE_tick', 'BNGE_tick')
  MP.CreateEventTimer('BNGE_tick', G.TICK_MS)
  print('[BeamNG.Earth] plugin loaded')
end

return G
