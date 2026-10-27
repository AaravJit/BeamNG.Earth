-- Night lighting (the user, 2026-10-27: "work on night lighting"; GitHub #3): after dusk the buildings' windows light
-- up. Their materials carry night emissive maps (tools/build_night_windows.py: about half the plaster sheet's windows,
-- the curtain walls and shopfronts with some bays dimmed), dark by day (emissiveFactor 0 in buildings.materials.json);
-- this turns them on and off with the stock environment's own day / night (core_environment.getLightState, the switch
-- the stock maps' night lights follow), checked every CHECK_S: a material change twice a day, no per-frame cost.
local M = {}

M.CHECK_S = 1.0
-- the materials and their night glow (emissive factor, nits: West Coast USA's highrise windows glow at 200)
M.MATERIALS = {
  bne_bld_facade = { '1 1 1', 200 },
  bne_bld_office = { '0.95 0.97 1', 150 },
  -- (shopfronts: their whole band lights up; at 320 nits, then 90, a grey-white strip along every street)
  bne_bld_shop = { '1 0.80 0.58', 30 },
  -- the street lamps' lenses (furniture.py G_LENS; tools/build_lamp_lens.py): sodium-warm, bright
  bne_lamp_lens = { '1 0.66 0.33', 4000 },
}

-- The street lamps' light: a pool of LIGHTS_MAX spot lights (no shadows), every LIGHTS_S moved to the lamps nearest
-- the camera within NEAR_M (their lenses: the road tiles' lamplens rows, furniture.py), pointing down. A moved light
-- costs nothing new: the pool is made once (a few a frame) and only switched off by day. (After MihneaTeodorStoica's
-- PR #340, which kept 24 point lights within 90 m.)
M.LIGHTS_MAX = 40
M.LIGHTS_S = 0.25
M.NEAR_M = 180
M.MAKE_PER_STEP = 4
-- (sodium orange, as West Coast USA's night streets)
M.LIGHT = { range = 34, inner = 80, outer = 140, brightness = 8.0, color = '1 0.66 0.33 1', drop = 0.12 }

local lastCheck = -1e9
local lastLights = -1e9
M.night = nil            -- the state applied (nil: none yet)
M.errors = 0
local lenses = {}        -- road tile key -> { {x, y, z}, ... }
local pool = {}          -- { id, at (lens table or nil) }

-- Whether it is night by the stock environment (nil when there is no time of day, as in the main menu).
function M.isNight()
  local env = rawget(_G, 'core_environment')
  if type(env) ~= 'table' or type(env.getLightState) ~= 'function' then return nil end
  local ok, st = pcall(env.getLightState)
  if not ok or type(st) ~= 'table' then return nil end
  return st.isNight == true
end

local function apply(night)
  for name, glow in pairs(M.MATERIALS) do
    local m = scenetree.findObject(name)
    if m then
      local ok = pcall(function()
        m:setField('emissiveFactor', 0, night and glow[1] or '0 0 0')
        m:setField('emissiveIntensityNits', 0, tostring(night and glow[2] or -1))
        m:flush()
        m:reload()
      end)
      if not ok then M.errors = M.errors + 1 end
    end
  end
  M.night = night
end

-- A road tile's furniture rows (roads.lua: { group index, x, y, z, yaw, ... } with its groups): its lenses.
function M.onRows(key, items, groups)
  local gi = nil
  for i, g in ipairs(type(groups) == 'table' and groups or {}) do if g == 'lamplens' then gi = i - 1 end end
  if gi == nil or type(items) ~= 'table' then lenses[key] = nil; return end
  local out = {}
  for _, r in ipairs(items) do
    if type(r) == 'table' and r[1] == gi and r[2] and r[3] and r[4] then out[#out + 1] = { r[2], r[3], r[4] } end
  end
  lenses[key] = #out > 0 and out or nil
end

function M.dropKey(key) lenses[key] = nil end

-- Origin rebase (core.lua finishRebase): p_new = R*p + t.
function M.transform(xf)
  for _, list in pairs(lenses) do
    for _, l in ipairs(list) do l[1], l[2], l[3] = xf:apply(l[1], l[2], l[3]) end
  end
  for _, e in ipairs(pool) do e.at = nil end           -- (placed again at the next step)
end

-- Pure: the n nearest lenses to (x, y) within r (a list of lens tables, nearest first).
function M.nearest(x, y, r, n)
  local cand, r2 = {}, r * r
  for _, list in pairs(lenses) do
    for _, l in ipairs(list) do
      local d = (l[1] - x) ^ 2 + (l[2] - y) ^ 2
      if d <= r2 then cand[#cand + 1] = { l, d } end
    end
  end
  table.sort(cand, function(a, b) return a[2] < b[2] end)
  local out = {}
  for i = 1, math.min(n, #cand) do out[i] = cand[i][1] end
  return out
end

local function newLight()
  local o = createObject('SpotLight')
  if not o then return nil end
  local L = M.LIGHT
  o:setField('range', 0, tostring(L.range))
  o:setField('innerAngle', 0, tostring(L.inner))
  o:setField('outerAngle', 0, tostring(L.outer))
  o:setField('brightness', 0, tostring(L.brightness))
  o:setField('color', 0, L.color)
  o:setField('castShadows', 0, 'false')
  o:setField('isEnabled', 0, 'false')
  o.canSave = false
  o:registerObject('')
  if scenetree.MissionGroup then scenetree.MissionGroup:addObject(o) end
  return o:getID()
end

local function place(e, l)
  local o = scenetree.findObjectById(e.id)
  if not o then return end
  if l then
    local m = MatrixF(true)
    m:setFromEuler(vec3(math.pi / 2, 0, 0))           -- (its forward, local y, down: -pi/2 pointed it up)
    m:setPosition(vec3(l[1], l[2], l[3] - M.LIGHT.drop))
    o:setTransform(m)
  end
  if (l ~= nil) ~= (e.at ~= nil) then o:setField('isEnabled', 0, l and 'true' or 'false') end
  e.at = l
end

local function stepLights(night)
  if not night then
    for _, e in ipairs(pool) do if e.at then place(e, nil) end end
    return
  end
  local cx, cy = 0, 0
  local ok, p = pcall(core_camera.getPosition)
  if ok and p then cx, cy = p.x, p.y end
  local want = M.nearest(cx, cy, M.NEAR_M, M.LIGHTS_MAX)
  local made = 0
  while #pool < #want and made < M.MAKE_PER_STEP do
    local id = newLight()
    if not id then M.errors = M.errors + 1; break end
    pool[#pool + 1] = { id = id }
    made = made + 1
  end
  -- the lights already on a wanted lens stay; the others take the rest
  local keep, free = {}, {}
  local wanted = {}
  for _, l in ipairs(want) do wanted[l] = true end
  for _, e in ipairs(pool) do
    if e.at and wanted[e.at] then keep[e.at] = true else free[#free + 1] = e end
  end
  for _, l in ipairs(want) do
    if not keep[l] then
      local e = table.remove(free)
      if not e then break end
      place(e, l)
    end
  end
  for _, e in ipairs(free) do if e.at then place(e, nil) end end
end

-- Every frame (core.lua; cheap: the materials looked at every CHECK_S, the lights every LIGHTS_S).
function M.update()
  local now = os.clockhp()
  if now - lastCheck >= M.CHECK_S then
    lastCheck = now
    local night = M.isNight()
    if night ~= nil and night ~= M.night then apply(night) end
  end
  if now - lastLights >= M.LIGHTS_S and (M.night or #pool > 0) then
    lastLights = now
    local ok = pcall(stepLights, M.night == true)
    if not ok then M.errors = M.errors + 1 end
  end
end

-- A new level or session: the materials are new (applied again at the next check), the lights gone.
function M.reset()
  M.night, lastCheck, lastLights = nil, -1e9, -1e9
  for _, e in ipairs(pool) do
    local o = scenetree.findObjectById(e.id)
    if o then o:delete() end
  end
  pool, lenses = {}, {}
end

function M.status()
  local on, n = 0, 0
  for _, e in ipairs(pool) do if e.at then on = on + 1 end end
  for _, list in pairs(lenses) do n = n + #list end
  return { night = M.night, errors = M.errors, lights = #pool, lit = on, lenses = n }
end

return M
