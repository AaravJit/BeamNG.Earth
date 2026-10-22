-- luajit mod/tests/test_mpanchor.lua  (BeamMP: other players' positions into our frame, mpanchor.lua from Thai-v4's
-- PR #88): a position packet of a player whose anchor is known comes out in our frame (the same real-world point);
-- one of an unknown player, or another kind of packet, passes untouched; nothing before install.
local dir = (arg[0]:match('^(.*)[/\\]') or '.')
local T = dofile(dir .. '/testlib.lua')
dofile(dir .. '/stubs.lua')
local geodesy = require('beamngEarth/geodesy')
local ma = require('beamngEarth/mpanchor')

-- BeamMP's position handler, stubbed: what reaches it
local got = {}
positionGE = { handle = function(raw) got[#got + 1] = raw end }
local enc = {}
function jsonEncode(t)
  enc[#enc + 1] = t
  return 'ENC' .. #enc
end
function jsonDecode(s)
  local x, y, z = s:match('"pos":%[([^,]+),([^,]+),([^%]]+)%]')
  return { pos = { tonumber(x), tonumber(y), tonumber(z) }, vel = { 1, 0, 0 }, rot = { 0, 0, 0, 1 } }
end
MPCoreNetwork = { isMPSession = function() return true end }

ma.install()
T.check(positionGE.handle ~= nil and ma.status():find('installed=true', 1, true), 'installed: BeamMP\'s handler wrapped')

-- our anchor and player 4's, 3 km apart
ma.announceOwn(36.5, -118.1)
T.check(ma.ownChanged == true, 'our anchor changed: mpsession sends a pose at once')
ma.setRemote(4, 36.52, -118.08)
T.check(ma.knows(4) and not ma.knows(5), 'knows the anchors it was told')

positionGE.handle('p:4-0:{"pos":[100,200,30]}')
local out = enc[#enc]
local theirs, ours = geodesy.newAnchor(36.52, -118.08), geodesy.newAnchor(36.5, -118.1)
local ex, ey, ez = theirs:transformTo(ours):apply(100, 200, 30)
T.check(got[#got] == 'p:4-0:ENC' .. #enc and math.abs(out.pos[1] - ex) < 1e-6 and math.abs(out.pos[2] - ey) < 1e-6
  and math.abs(out.pos[3] - ez) < 1e-6, 'a known player\'s position: in our frame')
-- the same point on Earth: their frame -> geodetic == our frame -> geodetic
local la1, lo1 = theirs:enuToGeodetic(100, 200, 30)
local la2, lo2 = ours:enuToGeodetic(out.pos[1], out.pos[2], out.pos[3])
T.check(math.abs(la1 - la2) < 1e-9 and math.abs(lo1 - lo2) < 1e-9, 'the same real-world point')

positionGE.handle('p:5-0:{"pos":[1,2,3]}')
T.check(got[#got] == 'p:5-0:{"pos":[1,2,3]}', 'an unknown player\'s position: untouched')
positionGE.handle('Xn:4-0:{}')
T.check(got[#got] == 'Xn:4-0:{}', 'another kind of packet: untouched')

ma.reset()
T.check(not ma.knows(4), 'reset: the anchors forgotten')
T.done('test_mpanchor')
