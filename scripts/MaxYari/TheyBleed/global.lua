-- Owns decal records and instances: spawns aligned quads on request, grows pools in, plays landing sounds,
-- and recycles decals past a cap.
local async = require('openmw.async')
local core = require('openmw.core')
local world = require('openmw.world')
local types = require('openmw.types')
local util = require('openmw.util')
local Settings = require('scripts.MaxYari.TheyBleed.settings')

local SAVE_VERSION = 3 -- 2: per-colour records, 3: renamed to TheyBleed (mesh paths changed)

-- Variant counts and colour names must match FAMILIES / COLORS in tools/generate_assets.py
local COLORS = { red = true, blue = true, green = true, dark = true, orange = true }
-- growChance: fraction of spawned decals that spread in from growStart to full size over GROW_TIME
local FAMILIES = {
    drops = { count = 12, surfaceOffset = 0.2, growChance = 0.5, growStart = 0.5 },
    -- bigger quads sit a bit higher so small surface bumps under them don't poke through
    pool = { count = 8, surfaceOffset = 0.5, growChance = 1, growStart = 0.3 },
}
local RECYCLE_OLDEST = 0.8  -- over the cap, a random decal from this oldest fraction is removed, so fresh blood stays

local GROW_TIME = 10        -- seconds for a growing decal to spread to full size
local GROW_MAX_SLOPE = math.rad(30) -- only decals on floors this flat grow; walls and ceilings start at full size
local GROW_EASE = 0.5       -- 0 = linear, 1 = quadratic ease-out; in between starts a bit faster and settles gently

local IMPACT_SOUND = 'sounds/TheyBleed/blood_droplet_impact.wav'
local PITCH_RANGE = { 0.7, 0.9 }
local SOUND_VOLUME = 0.12    -- OpenAL gain at 100% of the volume setting
local SOUND_WAIT_FRAMES = 10 -- give up on a queued sound if its decal never gets placed
local DEBUG_SOUND = false    -- log each impact sound with its distance to the player
local DEBUG_GROWTH = false   -- log growth start/finish with the scale the engine reports

local UP = util.vector3(0, 0, 1)

local trimToCap -- forward declaration: a lowered cap is applied as soon as the setting changes
Settings.registerGroup()
local settings = Settings.new(function() trimToCap() end)

local recordIds = {} -- recordIds[color][family] = { record ids }, created lazily per colour
local decals = {}    -- spawned GameObjects, oldest first
local growing = {}   -- { obj, target, start, elapsed, waited }
local pendingSounds = {} -- { obj, frames }: decals whose landing sound waits for the teleport to be applied

local function getRecordIds(color, family)
    local byFamily = recordIds[color] or {}
    recordIds[color] = byFamily
    local ids = byFamily[family]
    local count = FAMILIES[family].count
    if not ids or #ids ~= count then
        ids = {}
        for i = 1, count do
            local draft = types.Static.createRecordDraft({
                model = string.format('meshes/MaxYari/TheyBleed/%s_%s_%02d.nif', family, color, i),
            })
            ids[i] = world.createRecord(draft).id
        end
        byFamily[family] = ids
    end
    return ids
end

-- Rotation mapping the quad's local +Z onto `normal`, with a random spin around it.
local function alignToNormal(normal)
    local d = UP:dot(normal)
    local base
    if d > 0.9999 then
        base = util.transform.identity
    elseif d < -0.9999 then
        base = util.transform.rotate(math.pi, util.vector3(1, 0, 0))
    else
        local axis = UP:cross(normal):normalize()
        local angle = math.acos(d)
        base = util.transform.rotate(angle, axis)
        -- util.transform rotation handedness differs from the textbook one, so verify and flip if needed
        if (base:apply(UP) - normal):length() > 0.01 then
            base = util.transform.rotate(-angle, axis)
        end
    end
    return base * util.transform.rotateZ(math.random() * 2 * math.pi)
end

local function removeDecal(obj)
    if obj:isValid() and obj.count > 0 then obj:remove() end
end

function trimToCap()
    -- each visible decal is its own draw call (no batching for runtime-created refs)
    while #decals > settings.MaxDecals do
        local pickFrom = math.max(1, math.floor(#decals * RECYCLE_OLDEST))
        removeDecal(table.remove(decals, math.random(pickFrom)))
    end
end

-- data: { family = 'drops'|'pool', color, position, normal, scale, sound (bool) }, cell resolved at hit time
local function place(data, cell)
    local family = FAMILIES[data.family] and data.family or 'drops'
    local cfg = FAMILIES[family]
    local color = COLORS[data.color] and data.color or 'red'
    local ids = getRecordIds(color, family)
    local normal = data.normal
    local scale = data.scale or 1

    local obj = world.createObject(ids[math.random(#ids)], 1)
    local grows = normal.z >= math.cos(GROW_MAX_SLOPE) and math.random() < cfg.growChance
    obj:setScale(grows and scale * cfg.growStart or scale)
    obj:teleport(cell, data.position + normal * cfg.surfaceOffset, { rotation = alignToNormal(normal) })
    decals[#decals + 1] = obj
    if grows then
        growing[#growing + 1] = { obj = obj, target = scale, start = cfg.growStart, elapsed = 0 }
    end
    trimToCap()

    if data.sound then
        -- teleport is applied later in the frame; a sound attached before that plays at the world origin
        pendingSounds[#pendingSounds + 1] = { obj = obj, frames = 0 }
    end
end

local function updatePendingSounds()
    local i = 1
    while i <= #pendingSounds do
        local p = pendingSounds[i]
        local obj = p.obj
        p.frames = p.frames + 1
        local done = not obj:isValid() or p.frames > SOUND_WAIT_FRAMES
        if not done and obj.cell then
            core.sound.playSoundFile3d(IMPACT_SOUND, obj, {
                volume = SOUND_VOLUME * settings.SoundVolume / 100,
                pitch = PITCH_RANGE[1] + math.random() * (PITCH_RANGE[2] - PITCH_RANGE[1]),
            })
            if DEBUG_SOUND and world.players[1] then
                print(string.format('[TheyBleed] impact sound at %s, %.0f units from player', tostring(obj.position),
                    (obj.position - world.players[1].position):length()))
            end
            done = true
        end
        if done then
            pendingSounds[i] = pendingSounds[#pendingSounds]
            pendingSounds[#pendingSounds] = nil
        else
            i = i + 1
        end
    end
end

-- data additionally carries `actor` (whose cell the decal goes into) and `delay`: seconds until the droplet lands
local function spawn(data)
    -- resolve the cell now: if the player walks through a door before the droplet lands, the decal still
    -- belongs to the cell where the blood was spilled
    local cell = data.actor.cell
    if not cell then return end
    if data.delay and data.delay > 0 then
        async:newUnsavableSimulationTimer(data.delay, function() place(data, cell) end)
    else
        place(data, cell)
    end
end

local function updateGrowth(dt)
    local i = 1
    while i <= #growing do
        local g = growing[i]
        local obj = g.obj
        local done
        if not obj:isValid() then
            done = true
        elseif obj.count == 0 or not obj.cell then
            -- teleport zeroes count until it's applied later in the frame; only a removed decal stays like this
            g.waited = (g.waited or 0) + 1
            done = g.waited > 10
        else
            if DEBUG_GROWTH and g.elapsed == 0 then
                print(string.format('[TheyBleed] grow start %s: reported scale %.2f, target %.2f, waited %d frames',
                    obj.recordId, obj.scale, g.target, g.waited or 0))
            end
            g.elapsed = g.elapsed + dt
            local t = math.min(1, g.elapsed / GROW_TIME)
            -- blend of linear and quadratic ease-out: speed goes from (1 + GROW_EASE) down to (1 - GROW_EASE)
            local eased = t + GROW_EASE * (t - t * t)
            local start = g.start or 0.3
            obj:setScale(g.target * (start + (1 - start) * eased))
            done = t >= 1
        end
        if done and DEBUG_GROWTH then
            print(string.format('[TheyBleed] grow end %s: valid %s, count %s, reported scale %s, elapsed %.2f',
                obj:isValid() and obj.recordId or '?', tostring(obj:isValid()), obj:isValid() and obj.count or '-',
                obj:isValid() and string.format('%.2f', obj.scale) or '-', g.elapsed))
        end
        if done then
            growing[i] = growing[#growing]
            growing[#growing] = nil
        else
            i = i + 1
        end
    end
end

return {
    engineHandlers = {
        onUpdate = function(dt)
            if #pendingSounds > 0 then updatePendingSounds() end
            if #growing > 0 then updateGrowth(dt) end
        end,
        onSave = function()
            return { version = SAVE_VERSION, recordIds = recordIds, decals = decals, growing = growing }
        end,
        onLoad = function(data)
            decals = data and data.decals or {}
            growing = {}
            if not data or data.version ~= SAVE_VERSION then
                -- older decals point at meshes that no longer exist; drop the ones we can still reach
                for _, obj in ipairs(decals) do removeDecal(obj) end
                decals, recordIds = {}, {}
                return
            end
            recordIds = data.recordIds or {}
            growing = data.growing or {}
            trimToCap() -- honour a lowered cap
        end,
    },
    eventHandlers = {
        TheyBleed_Spawn = spawn,
    },
}
