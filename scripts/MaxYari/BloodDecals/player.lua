-- Test harness: hold RMB to spray blood decals where the crosshair points.
local camera = require('openmw.camera')
local input = require('openmw.input')
local nearby = require('openmw.nearby')
local core = require('openmw.core')
local self = require('openmw.self')
local types = require('openmw.types')
local util = require('openmw.util')
local I = require('openmw.interfaces')

local SPAWN_INTERVAL = 0.05 -- seconds between decals while RMB is held
local MAX_DISTANCE = 1500
local SPREAD = math.rad(4)   -- random cone half-angle, makes holding RMB spray a patch
local MAX_PASSTHROUGH = 3    -- how many existing decals a ray may skip through

local screenCenter = util.vector2(0.5, 0.5)
local timer = 0

local function isDecal(obj)
    return types.Static.objectIsInstance(obj)
        and types.Static.record(obj).model:lower():find('blooddecals', 1, true) ~= nil
end

local function isActor(obj)
    return types.Actor.objectIsInstance(obj)
end

local function jitter(dir, angle)
    -- pick any vector perpendicular to dir, then rotate it randomly around dir
    local helper = math.abs(dir.z) < 0.9 and util.vector3(0, 0, 1) or util.vector3(1, 0, 0)
    local u = dir:cross(helper):normalize()
    local v = dir:cross(u)
    local a = math.random() * 2 * math.pi
    local r = math.tan(angle) * math.sqrt(math.random())
    return (dir + u * (math.cos(a) * r) + v * (math.sin(a) * r)):normalize()
end

-- Rendering ray that passes through previously spawned decals.
local function castDecalRay(from, dir)
    for _ = 0, MAX_PASSTHROUGH do
        local res = nearby.castRenderingRay(from, from + dir * MAX_DISTANCE, { ignore = self })
        if not res.hit then return nil end
        if not res.hitObject or not isDecal(res.hitObject) then return res end
        from = res.hitPos + dir * 0.5
    end
end

local function trySpawn()
    local dir = jitter(camera.viewportToWorldVector(screenCenter):normalize(), SPREAD)
    local res = castDecalRay(camera.getPosition(), dir)
    if not res or not res.hitNormal then return end
    if res.hitObject and isActor(res.hitObject) then return end

    local normal = res.hitNormal:normalize()
    if normal:dot(dir) > 0 then normal = -normal end -- hit a backface
    core.sendGlobalEvent('BloodDecals_Spawn', {
        position = res.hitPos,
        normal = normal,
        actor = self.object,
    })
end

return {
    engineHandlers = {
        onFrame = function(dt)
            if not input.isMouseButtonPressed(3) or I.UI.getMode() then
                timer = SPAWN_INTERVAL -- first decal spawns immediately on the next press
                return
            end
            timer = timer + dt
            if timer < SPAWN_INTERVAL then return end
            timer = 0
            trySpawn()
        end,
    },
}
