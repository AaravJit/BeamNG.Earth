-- BeamNG.Earth servers for BeamMP's server browser (the user, 2026-10-24: "show a list of all the .earth servers ...
-- make it so players without .earth cant see .earth server in the server list"). BeamNG.Earth servers are Private in
-- BeamMP (off BeamMP's public list); their plugin announces them to our list service (native/serverlist), and this
-- fetches that list for the "BeamNG.Earth" tab the UI adds to BeamMP's page (ui/ui-vue/mods/beamngEarth/index.js).
-- Plain HTTP with luasocket at settimeout(0), stepped in onUpdate: nothing here blocks a frame. The UI polls state().
local M = {}

M.LIST_URL = 'http://10.0.0.5:47640/servers'   -- (the list service; set to its public address before a release)
M.TIMEOUT_S = 6

local req = nil             -- { sock, phase = 'connect' | 'send' | 'recv', buf, t, host, port, path }
local result = { state = 'idle', servers = {}, error = nil, at = nil }

-- Pure: host, port, path of an http:// URL (nil: not one).
function M.parseUrl(url)
  local host, port, path = tostring(url or ''):match('^http://([^/:]+):?(%d*)(/?.*)$')
  if not host then return nil end
  return host, tonumber(port) or 80, path ~= '' and path or '/'
end

-- Pure: the body of an HTTP/1.0 response if its status is 200 (nil, why otherwise).
function M.parseResponse(raw)
  local head, body = tostring(raw or ''):match('^(.-)\r\n\r\n(.*)$')
  if not head then return nil, 'no response' end
  local code = tonumber(head:match('^HTTP/%d%.%d (%d+)'))
  if code ~= 200 then return nil, 'HTTP ' .. tostring(code) end
  return body
end

-- Pure: the list from the service's JSON (decode: a JSON decoder), each entry checked and its fields made safe.
function M.toServers(body, decode)
  local ok, list = pcall(decode, body)
  if not ok or type(list) ~= 'table' then return nil, 'bad list' end
  local out = {}
  for _, s in ipairs(list) do
    if type(s) == 'table' and type(s.ip) == 'string' and tonumber(s.port) then
      out[#out + 1] = { ip = s.ip, port = math.floor(tonumber(s.port)), name = tostring(s.name or ''),
        players = tonumber(s.players) or 0, maxPlayers = tonumber(s.maxPlayers) or 0,
        version = tostring(s.version or ''), place = tostring(s.place or '') }
    end
  end
  return out
end

local function finish(servers, err)
  if req and req.sock then pcall(req.sock.close, req.sock) end
  req = nil
  if servers then result.servers = servers end
  result.state, result.error, result.at = err and 'error' or 'done', err, os.time()
end

-- Start fetching the list (a fetch already running carries on).
function M.refresh()
  if req then return true end
  local host, port, path = M.parseUrl(M.LIST_URL)
  if not host then finish(nil, 'no list address') return false end
  local ok, socket = pcall(require, 'socket')
  if not ok then finish(nil, 'no luasocket') return false end
  local sock = socket.tcp()
  sock:settimeout(0)
  req = { sock = sock, phase = 'connect', buf = {}, t = 0, host = host, port = port, path = path }
  result.state, result.error = 'loading', nil
  return true
end

function M.onUpdate(dtReal)
  if not req then return end
  req.t = req.t + (dtReal or 0)
  if req.t > M.TIMEOUT_S then finish(nil, 'the server list did not answer') return end
  local s = req.sock
  if req.phase == 'connect' then
    local ok, err = s:connect(req.host, req.port)
    if ok or err == 'already connected' then
      req.phase = 'send'
    elseif err ~= 'timeout' and err ~= 'Operation already in progress' then
      finish(nil, 'cannot reach the server list (' .. tostring(err) .. ')')
      return
    end
  end
  if req.phase == 'send' then
    local ok = s:send(string.format('GET %s HTTP/1.0\r\nHost: %s\r\nUser-Agent: BeamNG.Earth\r\n\r\n', req.path, req.host))
    if ok then req.phase = 'recv' end
  end
  if req.phase == 'recv' then
    local data, err, part = s:receive('*a')
    local chunk = data or part
    if chunk and #chunk > 0 then req.buf[#req.buf + 1] = chunk end
    if data or err == 'closed' then
      local body, why = M.parseResponse(table.concat(req.buf))
      if not body then finish(nil, why) return end
      local servers, bad = M.toServers(body, jsonDecode)
      finish(servers, bad)
    elseif err ~= 'timeout' then
      finish(nil, tostring(err))
    end
  end
end

-- For the UI: { state = 'idle' | 'loading' | 'done' | 'error', servers, error, at }.
function M.state() return result end

M.onExtensionLoaded = function() end

return M
