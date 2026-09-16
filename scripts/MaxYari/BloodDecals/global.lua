-- Owns decal records and instances: spawns aligned quads on request, recycles the oldest past a cap.
local world = require('openmw.world')
local types = require('openmw.types')
local util = require('openmw.util')

local VARIANT_COUNT = 12
local MAX_DECALS = 400
local SURFACE_OFFSET = 0.2       -- units along the normal, avoids z-fighting with the surface
local SCALE_MIN, SCALE_MAX = 0.5, 1.2

local UP = util.vector3(0, 0, 1)

local recordIds = {}
local decals = {} -- FIFO of spawned GameObjects
local decalsHead = 1

local function ensureRecords()
    if #recordIds == VARIANT_COUNT then return end
    recordIds = {}
    for i = 1, VARIANT_COUNT do
        local draft = types.Static.createRecordDraft({
            model = string.format('meshes/MaxYari/BloodDecals/drops_%02d.nif', i),
        })
        recordIds[i] = world.createRecord(draft).id
    end
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

local function pushDecal(obj)
    decals[#decals + 1] = obj
    while #decals - decalsHead + 1 > MAX_DECALS do
        local old = decals[decalsHead]
        decals[decalsHead] = nil
        decalsHead = decalsHead + 1
        if old:isValid() and old.count > 0 then old:remove() end
    end
    -- compact the queue once the dead prefix gets large
    if decalsHead > MAX_DECALS then
        local compact = {}
        for i = decalsHead, #decals do compact[#compact + 1] = decals[i] end
        decals, decalsHead = compact, 1
    end
end

local function spawn(data)
    ensureRecords()
    local normal = data.normal
    local obj = world.createObject(recordIds[math.random(#recordIds)], 1)
    obj:setScale(SCALE_MIN + math.random() * (SCALE_MAX - SCALE_MIN))
    obj:teleport(data.actor.cell, data.position + normal * SURFACE_OFFSET, { rotation = alignToNormal(normal) })
    pushDecal(obj)
end

return {
    engineHandlers = {
        onInit = ensureRecords,
        onSave = function()
            local list = {}
            for i = decalsHead, #decals do list[#list + 1] = decals[i] end
            return { recordIds = recordIds, decals = list }
        end,
        onLoad = function(data)
            recordIds = data and data.recordIds or {}
            decals = data and data.decals or {}
            decalsHead = 1
            ensureRecords()
        end,
    },
    eventHandlers = {
        BloodDecals_Spawn = spawn,
    },
}
