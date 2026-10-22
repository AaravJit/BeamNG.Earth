-- luajit mod/tests/test_mpsession.lua  (BeamMP sessions, mpsession.lua): poses sent each POSE_S with the anchor and
-- the place (the first after joining asks for the meeting place; at once when our anchor changes); the cars of players
-- whose anchor is unknown deactivated, back on when it is known (mpanchor converts their positions); the go-to asked of
-- core; damage sent when a player comes near; reset turns the cars back on.
local dir = (arg[0]:match('^(.*)[/\\]') or '.')
local T = dofile(dir .. '/testlib.lua')
local S = dofile(dir .. '/stubs.lua')
local ms = require('beamngEarth/mpsession')

-- pure: who is hidden
local hide = ms.toHide({ [10] = 1, [11] = 2, [12] = 3 }, function(pid) return pid == 1 or pid == 2 end)
T.check(not hide[10] and not hide[11] and hide[12], 'toHide: an unknown anchor hidden, known ones not')

-- BeamMP's client API, stubbed
local handlers, sent = {}, {}
function AddEventHandler(name, fn) handlers[name] = fn end
function TriggerServerEvent(name, data) sent[#sent + 1] = { name = name, data = data } end
function jsonEncode(t)
  local parts = {}
  for k, v in pairs(t) do parts[#parts + 1] = string.format('%q:%s', k, tostring(v)) end
  table.sort(parts)
  return '{' .. table.concat(parts, ',') .. '}'
end
local decoded = {}
function jsonDecode(s) return decoded[s] end
local cars = {}
local function car(gid, owner, own)
  local o = S.newObject('BeamNGVehicle')
  o.active = 1
  function o:setActive(a) self.active = a end
  function o:queueLuaCommand(c) self.cmds = self.cmds or {}; self.cmds[#self.cmds + 1] = c end
  S.objects[gid] = o
  cars[gid] = { gameVehicleID = gid, ownerID = owner, own = own }
  return o
end
MPVehicleGE = { getVehicles = function() return cars end, isOwn = function(id) return cars[id] and cars[id].own end }
local asked = {}
beamngEarth_core = { rebaseTo = function(lat, lon) asked.rebase = { lat, lon } end,
  teleport = function(lat, lon) asked.go = { lat, lon } end }
local anchor = { lat = 36.5, lon = -118.1 }
function anchor:enuToGeodetic(x, y) return self.lat + y / 1e5, self.lon + x / 1e5, 0 end

local mine = car(20, 1, true)
local other = car(21, 2, false)
local stranger = car(22, 3, false)
ms.onPostJoin()
ms.update(0.1, anchor, { x = 100, y = 0, z = 0 })
T.check(handlers.bngeAnchors and handlers.bngeGoTo, 'the plugin\'s events handled')
T.check(#sent == 1 and sent[1].name == 'bngePose' and sent[1].data:find('"joined":true', 1, true)
  and sent[1].data:find('"aLat":36.5', 1, true), 'the first pose: anchor, place, joined')
T.check(other.active == 0 and stranger.active == 0 and mine.active == 1, 'no anchors yet: the others\' cars off')
ms.update(1, anchor, { x = 100, y = 0, z = 0 })
T.check(#sent == 1, 'the next pose after POSE_S')
ms.update(1.1, anchor, { x = 100, y = 0, z = 0 })
T.check(#sent == 2 and sent[2].data:find('"joined":false', 1, true), 'the next pose, not joining any more')

-- the plugin's anchors: player 2 known (another anchor: mpanchor converts its positions), player 3 not
decoded.g = { ['1'] = { aLat = 36.5, aLon = -118.1, lat = 36.5, lon = -118.1 },
  ['2'] = { aLat = 36.6, aLon = -118.0, lat = 36.6, lon = -118.0 } }
handlers.bngeAnchors('g')
ms.update(ms.HIDE_S, anchor, { x = 100, y = 0, z = 0 })
T.check(other.active == 1 and stranger.active == 0, 'a known anchor: on (converted); unknown: still off')
T.check(require('beamngEarth/mpanchor').knows(2) and not require('beamngEarth/mpanchor').knows(3),
  'mpanchor told the anchors')

-- our anchor changes (a rebase): a pose at once, not after POSE_S
local n0 = #sent
require('beamngEarth/mpanchor').announceOwn(36.7, -118.1)
ms.update(0.01, anchor, { x = 100, y = 0, z = 0 })
T.check(#sent == n0 + 1, 'our anchor changed: a pose at once')

-- the go-to asked of core (once a session, a little beside the other player)
decoded.t = { lat = 36.7, lon = -118.3 }
handlers.bngeGoTo('t')
T.check(asked.go and asked.go[1] == 36.7 and asked.go[2] > -118.3, 'go-to: beside the other player')
asked.go = nil
handlers.bngeGoTo('t')
T.check(asked.go == nil, 'go-to once a session')

-- damage: due when a player comes near; after a change of DAMAGE_STEP that held DAMAGE_SETTLE_S
T.check(ms.damageDue(true, 0, 0, 0, 0.5) == true, 'damage: a newcomer: sent')
local due, settled = ms.damageDue(false, 10, 0, 0, 0.5)
T.check(due == false and settled == 0, 'damage: a small change: not sent')
due, settled = ms.damageDue(false, 500, 0, 0, 0.5)
T.check(due == false and settled == 0.5, 'damage: a big change: waits to settle')
due = ms.damageDue(false, 500, 0, ms.DAMAGE_SETTLE_S, 0.5)
T.check(due == true, 'damage: settled: sent')
-- sendDamage: our car's state asked of BeamMP's nodesVE when player 2 comes near (we are player 1)
MPConfig = { getPlayerServerID = function() return 1 end }
ms.reset()
decoded.n = { ['1'] = { aLat = 36.5, aLon = -118.1, lat = 36.5, lon = -118.1 },
  ['2'] = { aLat = 36.6, aLon = -118.0, lat = 36.5005, lon = -118.1 } }
decoded.f = { ['1'] = decoded.n['1'], ['2'] = { aLat = 36.6, aLon = -118.0, lat = 36.6, lon = -118.0 } }
local cmds = {}
function mine:queueLuaCommand(c) cmds[#cmds + 1] = c end
map = { objects = { [20] = { damage = 0 } } }
handlers.bngeAnchors('f')
ms.sendDamage(36.5, -118.1, 0.5)
T.check(#cmds == 0, 'damage: nobody near: nothing sent')
handlers.bngeAnchors('n')
ms.sendDamage(36.5, -118.1, 0.5)
T.check(#cmds == 1 and cmds[1]:find('nodesVE.getNodes', 1, true), 'damage: sent to a player who came near')
ms.sendDamage(36.5, -118.1, 0.5)
T.check(#cmds == 1, 'damage: not again while nothing changes')
map.objects[20].damage = 900
for _ = 1, 6 do ms.sendDamage(36.5, -118.1, 0.5) end
T.check(#cmds == 2, 'damage: sent once after it changed and settled: ' .. #cmds)

-- reset: every car back on
ms.reset()
T.check(stranger.active == 1 and next(ms.state().deactivated) == nil, 'reset: the cars back on')
T.done('test_mpsession')
