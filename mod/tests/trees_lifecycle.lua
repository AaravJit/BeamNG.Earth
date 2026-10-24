-- Regression coverage for replacing Forest rows without losing references to old items.
-- Run from the repository root with: luajit mod/tests/trees_lifecycle.lua
local dir = (arg[0]:match('^(.*)[/\\\\]') or '.')
package.path = dir .. '/../beamng_earth/lua/ge/extensions/?.lua;' .. package.path
local trees = dofile(dir .. '/../beamng_earth/lua/ge/extensions/beamngEarth/trees.lua')

local function rows(x)
  return { { 0, x, 0, 0, 0, 0, 0.9, 0 } }
end
local function tick3(anchor)
  for _ = 1, 3 do trees.update(anchor, nil, nil) end
end
local function tileCount()
  return trees.status().tiles
end

-- Equivalent decoded rows should reuse the current tile, not retire it unnecessarily.
trees.onRows(7, 'g1_2', rows(0), { 'guardrail' })
trees.onRows(7, 'g1_2', rows(0), { 'guardrail' })
assert(tileCount() == 1, 'identical rows must reuse the existing tile')

-- A changed tile must retain the old record until the paced cleanup removes it.
trees.onRows(7, 'g1_2', rows(10), { 'guardrail' })
assert(tileCount() == 2, 'changed rows must keep a retired record until cleanup')
tick3(7)
assert(tileCount() == 1, 'retired record must be removed after its items are processed')

-- The production roads.lua path marks the previous tile for deletion before re-sending its rows.
trees.dropKey('7:g1_2')
trees.onRows(7, 'g1_2', rows(20), { 'guardrail' })
assert(tileCount() == 2, 'a dropped tile and its replacement must coexist until cleanup')
tick3(7)
assert(tileCount() == 1, 'dropped tile cleanup must not remove its replacement')

trees.dropKey('7:g1_2')
tick3(7)
assert(tileCount() == 0, 'a final drop must remove the active tile')

print('trees_lifecycle: OK')
