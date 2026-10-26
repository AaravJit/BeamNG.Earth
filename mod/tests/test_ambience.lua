-- luajit mod/tests/test_ambience.lua  (forest wind and room tone, ambience.lua): a vegetation tile's sound nodes go to
-- the stock core_audioForest as one cloud round the listener (its main event the tiles' set), the room tone plays
-- and follows the listener, the set chosen by the tiles' sound items (kept until another has M.SWITCH times them),
-- a rebase moves the nodes, a teleport's old tiles go.
local dir = (arg[0]:match('^(.*)[/\\]') or '.')
local T = dofile(dir .. '/testlib.lua')
local S = dofile(dir .. '/stubs.lua')

-- the engine pieces ambience.lua uses
package.preload['kdtreepoint3d'] = function()
  return { new = function() return { n = 0, preLoad = function(self) self.n = self.n + 1 end, build = function() end } end }
end
local clock = 0
os.clockhp = function() return clock end
local lx, ly = 0, 0
SFXSystem = { getPositionXYZ = function() return lx, ly, 0 end }
MatrixF = function() return {} end
Sim = { upcast = function(o) return o end }
Engine = { generateUUID = function() return 'u' end }
function simObjectExists(o) return S.objects[o.id] ~= nil end
local af = { calls = {} }
function af.setNodeCloud(c) af.cloud = c end
function af.setMainEvent(e) af.main = e end
function af.setEnabled(v) af.enabled = v end
extensions = { core_audioForest = af }
local function patch(o)
  function o:setTransform() end
  function o:play() self.playing = true end
  return o
end
local newObject = S.newObject
S.newObject = function(class) return patch(newObject(class)) end
createObject = function(class) return S.newObject(class) end

local amb = require('beamngEarth/ambience')

-- pure: events and vote
T.check(amb.events({ 0, 0 })[1]:match('ConiferousDeciduous_01$') ~= nil, 'temperate _01')
T.check(amb.events({ 2, 1 })[2]:match('Roomtone_Desert_02$') ~= nil, 'desert _02 room tone')
T.check(amb.events({ 3, 0 })[1]:match('Wind_Tropical$') ~= nil, 'tropical')
T.check(amb.events({ 9, 0 }) == nil, 'unknown set: nothing')
local function tile(cx, cy, b, w) return { cx = cx, cy = cy, biome = b, weight = w } end
local v = amb.vote({ tile(0, 0, { 0, 0 }, 10), tile(256, 0, { 2, 0 }, 30) }, 0, 0, nil)
T.check(v[1] == 2, 'the most sound items win')
v = amb.vote({ tile(0, 0, { 0, 0 }, 25), tile(256, 0, { 2, 0 }, 30) }, 0, 0, { 0, 0 })
T.check(v[1] == 0, 'the current set stays while the other has less than SWITCH times its items')
v = amb.vote({ tile(0, 0, { 1, 0 }, 0), tile(5000, 0, { 2, 0 }, 99) }, 0, 0, nil)
T.check(v[1] == 1, 'a tile without trees still gives its set; far tiles do not vote')

-- a tile's nodes as the cloud round the listener
amb.onTile(1, '0_0', { biome = { 0, 1 }, audio = { 10, 20, 5, 6, 0.5, 30, 200, 200, 5, 3, 0, 4, 2000, 0, 0, 1, 1, 1 } })
amb.update(1)
T.check(af.cloud and af.cloud.nodeCount == 2, 'the two nodes within the radius: ' .. tostring(af.cloud and af.cloud.nodeCount))
T.check(af.cloud.nodeDepth[1] == 6 and af.cloud.nodeSourceCount[1] == 30, 'depth and count kept')
T.check(af.main:match('ConiferousDeciduous_02$') ~= nil and af.enabled == true, 'main event set, enabled')
local rt = S.liveOf('SFXEmitter')
T.check(#rt == 1 and rt[1].fields.track:match('Roomtone_ConiferousDeciduous_02$') ~= nil and rt[1].playing, 'the room tone plays')

-- not built again until moved or changed (and not more often than GAP_S)
local builds = amb.builds
clock = 5
amb.update(1)
T.check(amb.builds == builds, 'nothing changed: not built again')
lx = 400
amb.update(1)
T.check(amb.builds == builds + 1, 'moved past MOVE_M: built again')
T.check(rt[1].pos.x == 400, 'the room tone follows the listener')

-- rebase: the nodes move (a ghost), and a teleport's tiles go
local xf = { apply = function(_, x, y, z) return x + 1000, y, z end }
amb.transform(xf)
lx = 1400
clock = 10
amb.update(2)
T.check(af.cloud.nodeCount == 2 and af.cloud.nodeX[1] == 1010, 'the ghost tile\'s nodes moved with the rebase')
amb.onTile(3, '0_0', { biome = { 0, 0 }, audio = {} })
clock = 15
amb.update(3)
T.check(af.cloud.nodeCount == 2, 'the ghost stays under another anchor\'s tile elsewhere')
clock = 200
amb.update(3)
T.check(af.cloud.nodeCount == 0 and af.enabled == false, 'the ghost goes after GHOST_MAX_S: no nodes, disabled')
lx = 0
clock = 202
amb.update(3)
T.check(af.main:match('_01$') ~= nil and S.liveOf('SFXEmitter')[1].fields.track:match('_01$') ~= nil,
  'back by the new tile: the wind and room tone of its set')
-- the room tone deleted by someone else: made again (no error every frame)
local old = S.liveOf('SFXEmitter')[1]
old:delete()
clock = 210
amb.update(3)
local now = S.liveOf('SFXEmitter')
T.check(#now == 1 and now[1].id ~= old.id, 'a deleted room tone: made again')

amb.clear()
T.check(#S.liveOf('SFXEmitter') == 0 and af.enabled == false, 'clear: the room tone goes')
T.done('test_ambience')
