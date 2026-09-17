-- Casts the rendering rays that place blood decals. castRenderingRay only works in player onFrame/input handlers,
-- so actors report hits here and the rays are worked off one decal per frame.
local camera = require('openmw.camera')
local nearby = require('openmw.nearby')
local core = require('openmw.core')
local self = require('openmw.self')
local types = require('openmw.types')
local util = require('openmw.util')
local I = require('openmw.interfaces')
local Settings = require('scripts.MaxYari.TheyBleed.settings')

Settings.registerPage()
local settings = Settings.new()

-- Quad edge lengths must match FAMILIES in tools/generate_assets.py
local DROPS = { quadSize = 16, scaleMin = 0.75, scaleMax = 1.8 }
local POOL = { quadSize = 40, scaleMin = 0.6, scaleMax = 1.0 }

-- Hits: blood leaves the wound mostly downward, fans out wide to the sides and carries a little along the attack.
-- Straight behind the victim is hidden from the attacker's view, so the backward reach is kept short.
local HIT_POOLS = 2                  -- big decals per hit at Blood Amount 1; a pool that can't be placed carries over
local HIT_DROPS = 4
local MAX_HIT_RAYS = 30              -- cap on rays per hit, whatever the Blood Amount setting
local HIT_REACH = 220                -- ray length from the wound
local HIT_SOUNDS = 2                 -- landing sounds per hit, played by the first decals that land
local HIT_SOUND_CHANCE = 0.33        -- chance a hit gets landing sounds at all (rolled once per hit)
local PLAYER_HIT_DROPS = 1           -- the player's own wounds emit a single small decal, no pools
local SIDE_SPREAD = 1.0              -- max sideways travel per unit of drop, stratified across one hit's decals
local ALONG_RANGE = { -0.25, 0.7 }   -- travel along the attack per unit of drop; negative is back toward the attacker
local DOWN_RANGE = { 0.8, 1.3 }
local MAX_PENDING = 90               -- queued decals beyond this are dropped (big brawls)

-- Droplet flight time, so the landing sound (and decal) trail the hit: launched at DROPLET_SPEED along the ray,
-- accelerated by the part of gravity pointing along it. Tuned for feel: 2x speed and 4x gravity of a real droplet
-- (Morrowind ~72 units per metre), which halves the flight time.
local DROPLET_SPEED = 300            -- units/s
local GRAVITY = 2800                 -- units/s^2
local MIN_LAND_DELAY = 0.1           -- seconds; always leave a gap after the hit sound

-- Drips from badly wounded actors: one small decal straight-ish down
local DRIP_REACH = 250
local DRIP_SPREAD = 0.35             -- max horizontal travel per unit of drop

-- Debug (setting): hold RMB to spray decals where the crosshair points
local SPRAY_INTERVAL = 0.05
local SPRAY_DISTANCE = 1500
local SPRAY_CONE = math.rad(4)
local SPRAY_POOL_CHANCE = 0.3
local SPRAY_COLORS = { 'red', 'blue', 'green', 'dark', 'orange' } -- picked at random per spray decal
local SPRAY_SOUND_INTERVAL = 0.3     -- the spray also plays landing sounds, throttled, for testing

-- Footprint probing for pools: two extra rays cast back onto the surface around the hit point
local PROBE_RADIUS = 0.7             -- fraction of the pool's half-size to sample at
local PROBE_HEIGHT = 12              -- rays start this far above the surface along the hit normal
local MAX_TILT = math.rad(35)        -- fitted plane may differ this much from the hit normal before we give up

local RAY_PASSES = 3                 -- how many unsuitable objects a ray may pass through before giving up

local UP = util.vector3(0, 0, 1)
local screenCenter = util.vector2(0.5, 0.5)

local pending = {} -- FIFO of { origin, dir, ignore, burst }; burst is shared by one hit's jobs
local sprayTimer = 0
local sprayHeld = false -- tracked from mouse events so idle frames don't poll input
local spraySoundTimer = 0

-- Blood must not stick to things that move or get picked up.
local function isUnsuitableSurface(obj)
    if not obj then return false end
    if types.Actor.objectIsInstance(obj) then return true end
    if types.Item.objectIsInstance(obj) and types.Item.isCarriable(obj) then return true end
    if types.Door.objectIsInstance(obj) and not types.Door.isTeleport(obj) then return true end
    return false
end

-- Rendering ray that passes through unsuitable objects. Decal meshes use the effect node mask,
-- so rays already ignore existing decals.
local function castSurfaceRay(from, dir, length, ignore)
    local to = from + dir * length
    for _ = 1, RAY_PASSES do
        local res = nearby.castRenderingRay(from, to, { ignore = ignore })
        if not res.hit then return nil end
        if not isUnsuitableSurface(res.hitObject) then return res end
        from = res.hitPos + dir * 0.5
        if (to - from):dot(dir) <= 0 then return nil end
    end
end

local function randomScale(family)
    return family.scaleMin + math.random() * (family.scaleMax - family.scaleMin)
end

local function randomRange(range)
    return range[1] + math.random() * (range[2] - range[1])
end

local function anyPerpendicular(v)
    local helper = math.abs(v.z) < 0.9 and UP or util.vector3(1, 0, 0)
    return v:cross(helper):normalize()
end

-- Where a ray dropped onto the surface at `center + offset` lands, or nil if it misses or lands on something unsuitable.
local function probeSurface(center, normal, offset)
    local origin = center + normal * PROBE_HEIGHT + offset
    local res = nearby.castRenderingRay(origin, origin - normal * (PROBE_HEIGHT * 2), { ignore = self })
    if not res.hit or isUnsuitableSurface(res.hitObject) then return nil end
    return res.hitPos
end

-- Normal of the plane through the hit point and two probed points around it, averaging the surface
-- under a large decal instead of trusting the single triangle the ray happened to hit.
-- Returns nil when the footprint isn't a usable surface (ledge edge, corner, probe missed).
local function fitFootprintNormal(center, normal, radius)
    local u = anyPerpendicular(normal)
    local v = normal:cross(u)
    local a = math.random() * 2 * math.pi
    -- two probes 90 degrees apart around the centre, random orientation so errors don't line up
    local dirA = u * math.cos(a) + v * math.sin(a)
    local dirB = normal:cross(dirA)
    local pA = probeSurface(center, normal, dirA * radius)
    local pB = probeSurface(center, normal, dirB * radius)
    if not pA or not pB then return nil end

    local fitted = (pA - center):cross(pB - center)
    if fitted:length() < 1e-3 then return nil end
    fitted = fitted:normalize()
    if fitted:dot(normal) < 0 then fitted = -fitted end
    if math.acos(math.min(1, fitted:dot(normal))) > MAX_TILT then return nil end
    return fitted
end

local function flightTime(distance, dir)
    local g = GRAVITY * math.max(0, -dir.z)
    if g < 1 then return distance / DROPLET_SPEED end
    -- distance = v*t + g*t^2/2
    return (-DROPLET_SPEED + math.sqrt(DROPLET_SPEED * DROPLET_SPEED + 2 * g * distance)) / g
end

-- Returns the placed family ('drops'|'pool') or nil if nothing was placed.
-- opts: { wantPool, color, sound, landTime } where landTime is the hit's simulation time for delaying the landing.
local function spawnFromRay(from, dir, length, ignore, opts)
    local res = castSurfaceRay(from, dir, length, ignore)
    if not res or not res.hitNormal then return nil end

    local normal = res.hitNormal:normalize()
    if normal:dot(dir) > 0 then normal = -normal end -- hit a backface

    local family, scale = 'drops', randomScale(DROPS)
    if opts.wantPool then
        local poolScale = randomScale(POOL)
        local fitted = fitFootprintNormal(res.hitPos, normal, POOL.quadSize * 0.5 * poolScale * PROBE_RADIUS)
        -- surface too uneven for a big quad: fall back to a small cluster on the original hit normal
        if fitted then
            family, scale, normal = 'pool', poolScale, fitted
        end
    end

    local delay = 0
    if opts.landTime then
        local landsAt = opts.landTime + flightTime((res.hitPos - from):length(), dir)
        delay = math.max(MIN_LAND_DELAY, landsAt - core.getSimulationTime())
    end

    core.sendGlobalEvent('TheyBleed_Spawn', {
        family = family,
        color = opts.color,
        delay = delay,
        sound = opts.sound,
        position = res.hitPos,
        normal = normal,
        scale = scale,
        actor = self.object,
    })
    return family
end

-- data: { color, isPlayer, hitPos, victim, victimPos, attackerPos (optional) }
local function onActorHit(data)
    local forward
    if data.attackerPos then
        local d = data.victimPos - data.attackerPos
        forward = util.vector3(d.x, d.y, 0)
    end
    if not forward or forward:length() < 1 then
        local a = math.random() * 2 * math.pi
        forward = util.vector3(math.cos(a), math.sin(a), 0)
    end
    forward = forward:normalize()
    local side = forward:cross(UP)

    local amount = settings.BloodAmount
    local count, pools
    if data.isPlayer then
        count, pools = math.min(MAX_HIT_RAYS, math.floor(PLAYER_HIT_DROPS * amount + 0.5)), 0
    else
        count = math.min(MAX_HIT_RAYS, math.floor((HIT_POOLS + HIT_DROPS) * amount + 0.5))
        pools = math.floor(count * HIT_POOLS / (HIT_POOLS + HIT_DROPS) + 0.5) -- keep the pool share
    end
    if count <= 0 then return end
    local burst = {
        color = data.color,
        poolsOwed = pools,
        soundsLeft = math.random() < HIT_SOUND_CHANCE and math.min(HIT_SOUNDS, count) or 0,
        hitTime = core.getSimulationTime(),
    }
    local jobs = {}
    for i = 1, count do
        -- stratify sideways so one hit's decals fan out instead of clumping
        local lateral = (((i - 1) + math.random()) / count * 2 - 1) * SIDE_SPREAD
        local dir = (forward * randomRange(ALONG_RANGE) + side * lateral - UP * randomRange(DOWN_RANGE)):normalize()
        jobs[i] = { origin = data.hitPos, dir = dir, ignore = data.victim, burst = burst }
    end
    -- shuffle so pools (taken by the earliest jobs) don't always land on the same side
    for i = count, 2, -1 do
        local j = math.random(i)
        jobs[i], jobs[j] = jobs[j], jobs[i]
    end
    for _, job in ipairs(jobs) do
        if #pending >= MAX_PENDING then return end
        pending[#pending + 1] = job
    end
end

local function processPending()
    local job = table.remove(pending, 1)
    local burst = job.burst
    local ignore = job.ignore:isValid() and job.ignore or self.object
    local placed = spawnFromRay(job.origin, job.dir, job.reach or HIT_REACH, ignore, {
        wantPool = burst.poolsOwed > 0,
        color = burst.color,
        sound = burst.soundsLeft > 0,
        landTime = burst.hitTime,
    })
    if placed then burst.soundsLeft = burst.soundsLeft - 1 end
    if placed == 'pool' then burst.poolsOwed = burst.poolsOwed - 1 end
end

-- data: { color, origin, victim }
local function onActorDrip(data)
    if #pending >= MAX_PENDING then return end
    local a = math.random() * 2 * math.pi
    local r = DRIP_SPREAD * math.sqrt(math.random())
    local dir = util.vector3(math.cos(a) * r, math.sin(a) * r, -1):normalize()
    pending[#pending + 1] = {
        origin = data.origin, dir = dir, ignore = data.victim, reach = DRIP_REACH,
        burst = { color = data.color, poolsOwed = 0, soundsLeft = 0, hitTime = core.getSimulationTime() },
    }
end

local function spray(dt)
    spraySoundTimer = spraySoundTimer + dt
    sprayTimer = sprayTimer + dt
    if sprayTimer < SPRAY_INTERVAL then return end
    sprayTimer = 0
    local dir = camera.viewportToWorldVector(screenCenter):normalize()
    -- random offset inside a cone around the crosshair
    local u = anyPerpendicular(dir)
    local v = dir:cross(u)
    local a = math.random() * 2 * math.pi
    local r = math.tan(SPRAY_CONE) * math.sqrt(math.random())
    dir = (dir + u * (math.cos(a) * r) + v * (math.sin(a) * r)):normalize()
    spawnFromRay(camera.getPosition(), dir, SPRAY_DISTANCE, self, {
        wantPool = math.random() < SPRAY_POOL_CHANCE,
        color = SPRAY_COLORS[math.random(#SPRAY_COLORS)],
        sound = spraySoundTimer >= SPRAY_SOUND_INTERVAL,
    })
    if spraySoundTimer >= SPRAY_SOUND_INTERVAL then spraySoundTimer = 0 end
end

return {
    engineHandlers = {
        -- idle cost is two cheap checks; castRenderingRay is only allowed here or in input handlers
        onFrame = function(dt)
            -- at most one decal per frame; hits take priority over the debug spray
            if #pending > 0 then
                processPending()
            elseif sprayHeld then
                if I.UI.getMode() then return end
                spray(dt)
            end
        end,
        onMouseButtonPress = function(button)
            if button == 3 and settings.DebugSpray then
                sprayHeld = true
                sprayTimer = SPRAY_INTERVAL -- first spray decal spawns immediately
            end
        end,
        onMouseButtonRelease = function(button)
            if button == 3 then sprayHeld = false end
        end,
    },
    eventHandlers = {
        TheyBleed_Hit = onActorHit,
        TheyBleed_Drip = onActorDrip,
    },
}
