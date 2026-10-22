-- luajit mod/tests/test_mpsession.lua  (BeamMP sessions by area, mpsession.lua): poses sent each POSE_S with the
-- anchor and the place (the first after joining asks for the meeting place); other groups' and unknown players' cars
-- deactivated, back on when they share our anchor; the plugin's rebase and go-to asked of core; reset turns them on.
local dir = (arg[0]:match('^(.*)[/\\]') or '.')
local T = dofile(dir .. '/testlib.lua')
local S = dofile(dir .. '/stubs.lua')
local ms = require('beamngEarth/mpsession')

-- pure: who is hidden
local hide = ms.toHide({ [10] = 1, [11] = 2, [12] = 3 }, { ['1'] = 'A', ['2'] = 'B' }, 'A')
T.check(not hide[10] and hide[11] and hide[12], 'toHide: another group and no group hidden, ours not')
T.check(next(ms.toHide({ [10] = 1 }, {}, nil)) == nil, 'toHide: no anchor of ours yet: nothing')

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
T.check(handlers.bngeGroups and handlers.bngeRebase and handlers.bngeGoTo, 'the plugin\'s events handled')
T.check(#sent == 1 and sent[1].name == 'bngePose' and sent[1].data:find('"joined":true', 1, true)
  and sent[1].data:find('"aLat":36.5', 1, true), 'the first pose: anchor, place, joined')
T.check(other.active == 0 and stranger.active == 0 and mine.active == 1, 'no groups yet: the others\' cars off')
ms.update(1, anchor, { x = 100, y = 0, z = 0 })
T.check(#sent == 1, 'the next pose after POSE_S')
ms.update(1.1, anchor, { x = 100, y = 0, z = 0 })
T.check(#sent == 2 and sent[2].data:find('"joined":false', 1, true), 'the next pose, not joining any more')

-- the plugin's groups: player 2 shares our anchor, player 3 does not
decoded.g = { ['1'] = ms.key(36.5, -118.1), ['2'] = ms.key(36.5, -118.1), ['3'] = 'elsewhere' }
handlers.bngeGroups('g')
ms.update(ms.HIDE_S, anchor, { x = 100, y = 0, z = 0 })
T.check(other.active == 1 and stranger.active == 0, 'same anchor: on; another: still off')

-- rebase and go-to asked of core (go-to once a session, a little beside the other player)
decoded.r = { aLat = 36.6, aLon = -118.2 }
handlers.bngeRebase('r')
T.check(asked.rebase and asked.rebase[1] == 36.6, 'rebase asked')
decoded.t = { lat = 36.7, lon = -118.3 }
handlers.bngeGoTo('t')
T.check(asked.go and asked.go[1] == 36.7 and asked.go[2] > -118.3, 'go-to: beside the other player')
asked.go = nil
handlers.bngeGoTo('t')
T.check(asked.go == nil, 'go-to once a session')

-- damage: due when a player joins our group; after a change of DAMAGE_STEP that held DAMAGE_SETTLE_S
T.check(ms.damageDue(true, 0, 0, 0, 0.5) == true, 'damage: a newcomer: sent')
local due, settled = ms.damageDue(false, 10, 0, 0, 0.5)
T.check(due == false and settled == 0, 'damage: a small change: not sent')
due, settled = ms.damageDue(false, 500, 0, 0, 0.5)
T.check(due == false and settled == 0.5, 'damage: a big change: waits to settle')
due = ms.damageDue(false, 500, 0, ms.DAMAGE_SETTLE_S, 0.5)
T.check(due == true, 'damage: settled: sent')
-- sendDamage: our car's state asked of BeamMP's nodesVE when player 2 is (newly) in our group
ms.reset()
handlers.bngeGroups('g')
local cmds = {}
function mine:queueLuaCommand(c) cmds[#cmds + 1] = c end
map = { objects = { [20] = { damage = 0 } } }
ms.sendDamage({ [21] = 2 }, ms.key(36.5, -118.1), 0.5)
T.check(#cmds == 1 and cmds[1]:find('nodesVE.getNodes', 1, true), 'damage: sent to a newcomer of our group')
ms.sendDamage({ [21] = 2 }, ms.key(36.5, -118.1), 0.5)
T.check(#cmds == 1, 'damage: not again while nothing changes')
map.objects[20].damage = 900
for _ = 1, 6 do ms.sendDamage({ [21] = 2 }, ms.key(36.5, -118.1), 0.5) end
T.check(#cmds == 2, 'damage: sent once after it changed and settled: ' .. #cmds)
ms.sendDamage({ [22] = 3 }, ms.key(36.5, -118.1), 0.5)
T.check(#cmds == 2, 'damage: nobody else in our group: nothing sent')

-- reset: every car back on
ms.reset()
T.check(stranger.active == 1 and next(ms.state().deactivated) == nil, 'reset: the cars back on')
T.done('test_mpsession')
