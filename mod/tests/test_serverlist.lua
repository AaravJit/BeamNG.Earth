-- luajit mod/tests/test_serverlist.lua  (serverlist.lua: the BeamNG.Earth servers for the tab in BeamMP's browser):
-- the list address, the HTTP answer and the list read safely.
local dir = (arg[0]:match('^(.*)[/\\]') or '.')
local T = dofile(dir .. '/testlib.lua')
dofile(dir .. '/stubs.lua')
local sl = require('beamngEarth/serverlist')

local h, p, path = sl.parseUrl('http://10.0.0.5:47640/servers')
T.check(h == '10.0.0.5' and p == 47640 and path == '/servers', 'parseUrl: host, port, path')
h, p, path = sl.parseUrl('http://list.example.org')
T.check(h == 'list.example.org' and p == 80 and path == '/', 'parseUrl: default port and path')
T.check(sl.parseUrl('https://x/y') == nil, 'parseUrl: only plain http')

T.check(sl.parseResponse('HTTP/1.0 200 OK\r\nContent-Type: application/json\r\n\r\n[1]') == '[1]', 'parseResponse: the body')
local body, why = sl.parseResponse('HTTP/1.1 404 Not Found\r\n\r\n{}')
T.check(body == nil and why == 'HTTP 404', 'parseResponse: an error status')
T.check(sl.parseResponse('garbage') == nil, 'parseResponse: no response')

local list = sl.toServers('x', function()
  return { { ip = '1.2.3.4', port = 30814, name = 'A', players = 2, maxPlayers = 8, version = '1.6.2' },
    { ip = 5, port = 1 }, { port = 2 }, 'junk', { ip = '9.9.9.9', port = '30815' } }
end)
T.check(#list == 2 and list[1].name == 'A' and list[2].port == 30815 and list[2].players == 0, 'toServers: valid entries only, fields made safe')
T.check(sl.toServers('x', function() error('bad') end) == nil, 'toServers: undecodable')

T.done('test_serverlist')
