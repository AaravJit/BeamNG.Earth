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
-- Damage: BeamMP sends parts that break off (break groups) but not dents; its own full-damage message (nodesVE.getNodes:
-- every node and beam, applied on the other players' copies by its nodesGE) is never sent. This sends it for our own
-- cars when another player joins our group (they see the car as it is) and DAMAGE_SETTLE_S after our car's damage
-- changed by DAMAGE_STEP.
-- Nothing of BeamMP is changed: its client events (AddEventHandler, TriggerServerEvent) and its own functions only.
local M = {}

M.PROTO = 1                 -- as the plugin's G.PROTO
M.POSE_S = 2
M.HIDE_S = 0.5
M.GOTO_OFFSET_M = 12        -- a joining player lands this far from the one it goes to (not on top of it)
M.DAMAGE_STEP = 50          -- (the game's damage figure, map.objects[id].damage)
M.DAMAGE_SETTLE_S = 2

local handlers = false
local groups = {}           -- tostring(server player id) -> anchor key
local deactivated = {}      -- game vehicle id -> true: deactivated here
local poseIn, hideIn = 0, 0
local joined = false        -- BeamMP joined and no pose sent yet: the first pose asks for the meeting place
local wentTo = false        -- the session's one bngeGoTo done
local camPlaced = false     -- the free camera put on the ground once (BeamMP joins without a car)
local lastGroups = ''       -- (the log: the groups as last logged)
local members = {}          -- tostring(pid) -> true: the other players in our group at the last check
local sentDamage = {}       -- own game vehicle id -> the damage its state was last sent at
local damageSince = {}      -- own game vehicle id -> seconds its damage has differed from sentDamage

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

-- Pure: should our car's full damage go out now? newcomer: a player joined our group; damage, sent: now and at the last
-- send (nil: never); settled: seconds it has differed. -> send, settled (updated)
function M.damageDue(newcomer, damage, sent, settled, dt)
  if newcomer then return true, 0 end
  if not damage or math.abs(damage - (sent or 0)) < M.DAMAGE_STEP then return false, 0 end
  settled = (settled or 0) + dt
  return settled >= M.DAMAGE_SETTLE_S, settled
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
  if not ok or type(d) ~= 'table' then return end
  groups = d
  local n, keys = 0, {}
  for _, k in pairs(d) do n = n + 1; keys[k] = true end
  local g = 0
  for _ in pairs(keys) do g = g + 1 end
  local line = string.format('%d player(s) in %d group(s)', n, g)
  if line ~= lastGroups then
    lastGroups = line
    log('I', 'beamngEarth', 'BeamMP: ' .. line)
  end
end

local function onRebase(data)
  local ok, d = pcall(jsonDecode, data)
  local c = core()
  if ok and type(d) == 'table' and c and c.rebaseTo and c.rebaseTo(d.aLat, d.aLon) then
    log('I', 'beamngEarth', string.format('BeamMP: to the group\'s anchor %.6f, %.6f', tonumber(d.aLat) or 0,
      tonumber(d.aLon) or 0))
  end
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
  log('I', 'beamngEarth', string.format('BeamMP: to the other players at %.5f, %.5f', lat, lon))
  c.teleport(lat, lon, nil, { record = false })
end

-- No car yet (BeamMP joins without one): the free camera, left at the level's spawn point under the generated
-- ground, put once on the ground at the origin (where the vehicle selector spawns the car).
local function placeCamera()
  if camPlaced or (getPlayerVehicle and getPlayerVehicle(0)) or not be or not be.getSurfaceHeightBelow then return end
  local z = be:getSurfaceHeightBelow(vec3(0, 0, 9000))
  if type(z) ~= 'number' or z < -1e5 then return end      -- (the ground not there yet)
  camPlaced = true
  local q = quatFromDir(vec3(0, 1, -0.15), vec3(0, 0, 1))
  if core_camera and core_camera.setPosRot then core_camera.setPosRot(0, 0, -8, z + 3, q.x, q.y, q.z, q.w) end
end

-- BeamMP's join done (core.lua onBeamMPPostJoin): the first pose asks the plugin where the others are.
function M.onPostJoin()
  joined, wentTo, camPlaced = true, false, false
  groups = {}
end

-- Each frame (core.lua) in a BeamMP session in our level: anchor (core's st.anchor), player { x, y, z } in its frame
-- (the car; the camera when there is none).
function M.update(dt, anchor, player)
  if not handlers and rawget(_G, 'AddEventHandler') then
    AddEventHandler('bngeGroups', onGroups, 'beamngEarth')
    AddEventHandler('bngeRebase', onRebase, 'beamngEarth')
    AddEventHandler('bngeGoTo', onGoTo, 'beamngEarth')
    handlers = true
  end
  if not anchor then return end
  placeCamera()
  if not player then return end
  local mine = M.key(anchor.lat, anchor.lon)
  poseIn = poseIn - (dt or 0)
  if poseIn <= 0 and rawget(_G, 'TriggerServerEvent') then
    poseIn = M.POSE_S
    local lat, lon = anchor:enuToGeodetic(player.x, player.y, player.z or 0)
    TriggerServerEvent('bngePose', jsonEncode({ v = M.PROTO, aLat = anchor.lat, aLon = anchor.lon, lat = lat,
      lon = lon, joined = joined }))
    if joined then log('I', 'beamngEarth', string.format('BeamMP: first pose sent (%.5f, %.5f)', lat, lon)) end
    joined = false
  end
  hideIn = hideIn - (dt or 0)
  if hideIn > 0 then return end
  hideIn = M.HIDE_S
  local owners = remoteOwners()
  local hide = M.toHide(owners, groups, mine)
  M.sendDamage(owners, mine, M.HIDE_S)
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

-- Our own cars' full damage to the players of our group (see the top), with BeamMP's own nodesVE.getNodes.
function M.sendDamage(owners, mine, dt)
  local now, newcomer, any = {}, false, false
  for _, owner in pairs(owners) do
    local k = tostring(owner)
    if groups[k] == mine then
      now[k] = true
      any = true
      if not members[k] then newcomer = true end
    end
  end
  members = now
  local v = mp()
  if not any or not v or type(v.getVehicles) ~= 'function' then return end
  local objs = rawget(_G, 'map') and map.objects or {}
  local ok, all = pcall(v.getVehicles)
  if not ok or type(all) ~= 'table' then return end
  for _, veh in pairs(all) do
    local gid = type(veh) == 'table' and veh.gameVehicleID
    if gid and v.isOwn(gid) then
      local o = objs[gid]
      local dmg = o and tonumber(o.damage)
      local send
      send, damageSince[gid] = M.damageDue(newcomer, dmg, sentDamage[gid], damageSince[gid], dt)
      local obj = send and scenetree.findObjectById(gid)
      if obj then
        obj:queueLuaCommand('if nodesVE and nodesVE.getNodes then nodesVE.getNodes() end')
        sentDamage[gid] = dmg or 0
      end
    end
  end
end

-- The session's end (BeamMP left, the level's end): the deactivated cars back on, everything forgotten.
function M.reset()
  for gid in pairs(deactivated) do
    local veh = scenetree.findObjectById(gid)
    if veh then veh:setActive(1) end
  end
  deactivated, groups, poseIn, hideIn, joined, wentTo, camPlaced, lastGroups = {}, {}, 0, 0, false, false, false, ''
  members, sentDamage, damageSince = {}, {}, {}
end

-- (tests, the dev tools)
function M.state() return { groups = groups, deactivated = deactivated, joined = joined, wentTo = wentTo } end

return M
