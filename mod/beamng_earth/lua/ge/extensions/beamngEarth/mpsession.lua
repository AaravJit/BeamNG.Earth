-- BeamMP sessions by area, with BeamNG.Earth's server plugin (beammp/Server/BeamNGEarth/main.lua; the user,
-- 2026-10-22: "If players are far from each other ... create a separate session near origin ... convert the positions").
-- BeamMP sends every car's position in its driver's world coordinates, and those are metres from the driver's own
-- anchor: players see each other in the right place only when they share one. Each POSE_S this sends the plugin the
-- anchor and the player's place (bngePose); the plugin answers with
--   - bngeGroups { pid -> anchor key }: the cars of players in another group (another anchor), or of players without
--     BeamNG.Earth (no group), are deactivated here: they would stand in the wrong place;
--   - bngeRebase { aLat, aLon }: this player meets another group: the world moves to that anchor (core.rebaseTo: the
--     smooth rebase, the player stays where it is on the ground);
--   - bngeGoTo { lat, lon }: on joining, a teleport to where the others are.
-- Nothing of BeamMP is changed: its client events only (AddEventHandler, TriggerServerEvent).
local M = {}

M.PROTO = 1                 -- as the plugin's G.PROTO
M.POSE_S = 2
M.HIDE_S = 0.5
M.GOTO_OFFSET_M = 12        -- a joining player lands this far from the one it goes to (not on top of it)

local handlers = false
local groups = {}           -- tostring(server player id) -> anchor key
local deactivated = {}      -- game vehicle id -> true: deactivated here
local poseIn, hideIn = 0, 0
local joined = false        -- BeamMP joined and no pose sent yet: the first pose asks for the meeting place
local wentTo = false        -- the session's one bngeGoTo done

function M.key(aLat, aLon) return string.format('%.6f,%.6f', aLat, aLon) end

-- Pure: the remote vehicles to deactivate: owners { game vehicle id -> owner's server player id }, groups, mine (this
-- player's anchor key, nil: none yet: nothing hidden). -> { game vehicle id -> true }
function M.toHide(owners, grp, mine)
  local out = {}
  if not mine then return out end
  for gid, owner in pairs(owners) do
    if grp[tostring(owner)] ~= mine then out[gid] = true end
  end
  return out
end

local function mp() return rawget(_G, 'MPVehicleGE') end

-- The other players' vehicles: { game vehicle id -> owner's server player id }
local function remoteOwners()
  local out = {}
  local v = mp()
  if not v or type(v.getVehicles) ~= 'function' then return out end
  local ok, all = pcall(v.getVehicles)
  if not ok or type(all) ~= 'table' then return out end
  for _, veh in pairs(all) do
    if type(veh) == 'table' and veh.gameVehicleID and veh.ownerID and not v.isOwn(veh.gameVehicleID) then
      out[veh.gameVehicleID] = veh.ownerID
    end
  end
  return out
end

local function core() return rawget(_G, 'beamngEarth_core') end

local function onGroups(data)
  local ok, d = pcall(jsonDecode, data)
  if ok and type(d) == 'table' then groups = d end
end

local function onRebase(data)
  local ok, d = pcall(jsonDecode, data)
  local c = core()
  if ok and type(d) == 'table' and c and c.rebaseTo then c.rebaseTo(d.aLat, d.aLon) end
end

local function onGoTo(data)
  local ok, d = pcall(jsonDecode, data)
  local c = core()
  if wentTo or not ok or type(d) ~= 'table' or not c then return end
  local lat, lon = tonumber(d.lat), tonumber(d.lon)
  if not lat or not lon then return end
  wentTo = true
  -- beside the other player, not on it (12 m east)
  lon = lon + M.GOTO_OFFSET_M / (111320 * math.max(0.01, math.cos(math.rad(lat))))
  c.teleport(lat, lon, nil, { record = false })
end

-- BeamMP's join done (core.lua onBeamMPPostJoin): the first pose asks the plugin where the others are.
function M.onPostJoin()
  joined, wentTo = true, false
  groups = {}
end

-- Each frame (core.lua) in a BeamMP session in our level: anchor (core's st.anchor), player { x, y, z } in its frame.
function M.update(dt, anchor, player)
  if not handlers and rawget(_G, 'AddEventHandler') then
    AddEventHandler('bngeGroups', onGroups, 'beamngEarth')
    AddEventHandler('bngeRebase', onRebase, 'beamngEarth')
    AddEventHandler('bngeGoTo', onGoTo, 'beamngEarth')
    handlers = true
  end
  if not anchor or not player then return end
  local mine = M.key(anchor.lat, anchor.lon)
  poseIn = poseIn - (dt or 0)
  if poseIn <= 0 and rawget(_G, 'TriggerServerEvent') then
    poseIn = M.POSE_S
    local lat, lon = anchor:enuToGeodetic(player.x, player.y, player.z or 0)
    TriggerServerEvent('bngePose', jsonEncode({ v = M.PROTO, aLat = anchor.lat, aLon = anchor.lon, lat = lat,
      lon = lon, joined = joined }))
    joined = false
  end
  hideIn = hideIn - (dt or 0)
  if hideIn > 0 then return end
  hideIn = M.HIDE_S
  local hide = M.toHide(remoteOwners(), groups, mine)
  for gid in pairs(hide) do
    if not deactivated[gid] then
      local veh = scenetree.findObjectById(gid)
      if veh then veh:setActive(0); deactivated[gid] = true end
    end
  end
  for gid in pairs(deactivated) do
    if not hide[gid] then
      local veh = scenetree.findObjectById(gid)
      if veh then veh:setActive(1) end
      deactivated[gid] = nil
    end
  end
end

-- The session's end (BeamMP left, the level's end): the deactivated cars back on, everything forgotten.
function M.reset()
  for gid in pairs(deactivated) do
    local veh = scenetree.findObjectById(gid)
    if veh then veh:setActive(1) end
  end
  deactivated, groups, poseIn, hideIn, joined, wentTo = {}, {}, 0, 0, false, false
end

-- (tests, the dev tools)
function M.state() return { groups = groups, deactivated = deactivated, joined = joined, wentTo = wentTo } end

return M
