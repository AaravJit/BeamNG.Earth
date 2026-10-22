-- luajit mod/tests/test_beammp_plugin.lua  (BeamNG.Earth's BeamMP server plugin, beammp/Server/BeamNGEarth/main.lua,
-- its pure part; the server runs Lua 5.3, this file keeps to what both have): players grouped by anchor; players of
-- different groups who meet move to the group with the most players, then the oldest, if its anchor is in reach of
-- all of them; a joining player sent to the biggest group.
local dir = (arg[0]:match('^(.*)[/\\]') or '.')
local T = dofile(dir .. '/testlib.lua')
local G = dofile(dir .. '/../../beammp/Server/BeamNGEarth/main.lua')

local M_PER_DEG = 111194.9     -- (G.dist's sphere)
local function north(lat, m) return lat + m / M_PER_DEG end

-- distance
T.check(math.abs(G.dist(0, 0, north(0, 1000), 0) - 1000) < 1, 'dist: 1 km north')

-- two players far apart, each its own anchor: two groups, no move
local players = {
  [1] = { aLat = 36.0, aLon = -118.0, lat = 36.0, lon = -118.0 },
  [2] = { aLat = 48.0, aLon = 11.0, lat = 48.0, lon = 11.0 },
}
local groups, moves = G.plan(players, {})
T.check(groups[1] ~= groups[2] and next(moves) == nil, 'far apart: two groups, nothing moves')

-- player 2 comes within MERGE_M of player 1 (its own anchor at its teleport place): 1's older group wins
players[2] = { aLat = north(36.0, 2000), aLon = -118.0, lat = north(36.0, 2000), lon = -118.0 }
local seen = { [G.key(36.0, -118.0)] = 1, [G.key(players[2].aLat, -118.0)] = 5 }
groups, moves = G.plan(players, seen)
T.check(moves[2] and moves[2].aLat == 36.0 and moves[1] == nil, 'met: the newer one moves to the older anchor')

-- a group of two meets one player: the bigger group wins even if newer
players[3] = { aLat = north(36.0, 2000), aLon = -118.0, lat = north(36.0, 2100), lon = -118.0 }
groups, moves = G.plan(players, seen)
T.check(moves[1] and moves[1].aLat == players[2].aLat and not moves[2] and not moves[3],
  'the bigger group wins: the lone player moves')

-- the winning anchor out of reach of a member (> MAX_ANCHOR_M): the next one
players = {
  [1] = { aLat = 36.0, aLon = -118.0, lat = north(36.0, 14000), lon = -118.0 },   -- 14 km from its own anchor
  [2] = { aLat = north(36.0, 15000), aLon = -118.0, lat = north(36.0, 15000), lon = -118.0 },
}
seen = { [G.key(36.0, -118.0)] = 1, [G.key(players[2].aLat, -118.0)] = 9 }
groups, moves = G.plan(players, seen)
T.check(moves[1] and moves[1].aLat == players[2].aLat, 'the older anchor too far from player 2: the newer one')

-- chained: A - B - C each within MERGE_M of the next (A and C 5 km apart): one cluster, one anchor
players = {
  [1] = { aLat = 36.0, aLon = -118.0, lat = 36.0, lon = -118.0 },
  [2] = { aLat = north(36.0, 2500), aLon = -118.0, lat = north(36.0, 2500), lon = -118.0 },
  [3] = { aLat = north(36.0, 5000), aLon = -118.0, lat = north(36.0, 5000), lon = -118.0 },
}
seen = { [G.key(36.0, -118.0)] = 1 }
groups, moves = G.plan(players, seen)
T.check(moves[2] and moves[3] and not moves[1], 'chained: all to the oldest anchor')

-- the session: poses, the meeting place on the first one, stale poses left out, leaving
G.players, G.anchorSeen, G.clock = {}, {}, 0
T.check(G.onPose(1, { v = G.PROTO, aLat = 36, aLon = -118, lat = 36, lon = -118, joined = true }, 'a') == nil,
  'the first player: alone, nowhere to go')
G.tick(2)
local place = G.onPose(2, { v = G.PROTO, aLat = 48, aLon = 11, lat = 48, lon = 11, joined = true }, 'b')
T.check(place and place.lat == 36 and place.lon == -118, 'a joining player: sent to the first player')
T.check(G.onPose(3, { v = 99, aLat = 1, aLon = 1, lat = 1, lon = 1 }, 'c') == nil and G.players[3] == nil,
  'another protocol: ignored')
T.check(G.onPose(4, { v = G.PROTO, aLat = 1, aLon = 1, lat = 95, lon = 1 }, 'd') == nil and G.players[4] == nil,
  'a bad latitude: ignored')
G.tick(G.STALE_S + 1)
local g = G.tick(0)
T.check(next(g) == nil, 'no pose for STALE_S: left out')
G.onLeave(1)
T.check(G.players[1] == nil, 'left: forgotten')

T.done('test_beammp_plugin')
