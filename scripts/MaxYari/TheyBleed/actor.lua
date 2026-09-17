-- Hooks the engine's blood effect so decals appear exactly when vanilla blood does: successful hit, health damage
-- left after armor, a hit position, and no other onHit handler cancelling the attack.
-- Wounded actors below BLEED_HEALTH also drip once per BLEED_INTERVAL, until healed above it, BLEED_DURATION
-- after their last wound, or BLEED_AFTER_DEATH after dying. No per-frame work and no handlers besides the hit hook: health is
-- only read right after a wound and then once per drip while bleeding. Bleeding isn't saved; it ends on reload.
local async = require('openmw.async')
local core = require('openmw.core')
local types = require('openmw.types')
local I = require('openmw.interfaces')
local self = require('openmw.self')
local nearby = require('openmw.nearby')
local util = require('openmw.util')
local auxUtil = require('openmw_aux.util')

-- Blood effect texture (from openmw.cfg fallback Blood_Texture_N, lowercased base name) -> decal colour.
-- Textures not listed get no decals (dust, sparks, Diverse Blood's energy). Names match COLORS in generate_assets.py.
local DECAL_COLORS = {
    tx_blood = 'red',
    blood_blue = 'blue',      -- Diverse Blood
    blood_green = 'green',    -- Diverse Blood
    blood_dark = 'dark',      -- Diverse Blood
    blood_orange = 'orange',  -- Diverse Blood
}
local RED_BLOOD = 0

local BLEED_HEALTH = 0.5   -- fraction of max health below which the actor drips
local BLEED_INTERVAL = 1   -- seconds between drips
local BLEED_DURATION = 30  -- seconds of dripping after the latest wound
local BLEED_AFTER_DEATH = 5 -- corpses keep dripping this long
local BLEED_HEIGHT = 0.3   -- drip origin above the bounding box centre, as a fraction of its half height

local decalColor -- cached per actor; false = this actor's blood leaves no decals

-- Mirrors how the builtin spawnBloodEffect picks the effect texture, so the decal matches the visible spray.
local function getDecalColor()
    if decalColor ~= nil then return decalColor end
    local bloodType = self.object.type.record(self.object).bloodType
    local texture = core.getGMST('Blood_Texture_' .. bloodType)
    if not texture or texture == '' then
        texture = core.getGMST('Blood_Texture_0')
        bloodType = RED_BLOOD
    end
    local name = (texture or ''):lower():match('([^/\\]+)%.%w+$') or ''
    -- a texture we don't know on the default blood slot is still red blood, just retextured
    decalColor = DECAL_COLORS[name] or (bloodType == RED_BLOOD and 'red') or false
    return decalColor
end

local function sendToPlayers(eventName, data)
    for _, player in ipairs(nearby.players) do
        player:sendEvent(eventName, data)
    end
end

local function isBadlyWounded()
    local health = types.Actor.stats.dynamic.health(self)
    local max = health.base + health.modifier
    return max > 0 and health.current / max < BLEED_HEALTH
end

local bleeding = false
local bleedUntil = 0
local diedAt -- simulation time death was first noticed while bleeding
local bleedChain = 0 -- bumped on every (re)start so a timer left over from an older chain stops itself

local function drip(chain)
    if chain ~= bleedChain then return end
    local now = core.getSimulationTime()
    if types.Actor.isDead(self) then
        diedAt = diedAt or now
        bleedUntil = math.min(bleedUntil, diedAt + BLEED_AFTER_DEATH)
    end
    if not isBadlyWounded() or now > bleedUntil then
        bleeding = false -- healed or bled out; the next wound restarts it
        return
    end
    local box = self.object:getBoundingBox()
    sendToPlayers('TheyBleed_Drip', {
        color = decalColor,
        origin = box.center + util.vector3(0, 0, box.halfSize.z * BLEED_HEIGHT),
        victim = self.object,
    })
    async:newUnsavableSimulationTimer(BLEED_INTERVAL, function() drip(chain) end)
end

local function startBleedingIfWounded()
    if not getDecalColor() or not isBadlyWounded() then return end
    local now = core.getSimulationTime()
    if types.Actor.isDead(self) then
        -- killing blow: note the death now so the corpse bleeds BLEED_AFTER_DEATH from here, not from the first drip
        diedAt = diedAt or now
        bleedUntil = math.min(now + BLEED_DURATION, diedAt + BLEED_AFTER_DEATH)
    else
        bleedUntil = now + BLEED_DURATION -- every wound refreshes the window
    end
    if bleeding then return end
    bleeding = true
    bleedChain = bleedChain + 1
    local chain = bleedChain
    -- stagger actors wounded together
    async:newUnsavableSimulationTimer(BLEED_INTERVAL * math.random(), function() drip(chain) end)
end

local ATTACKER_TIMEOUT = 10 -- seconds; an attacker left over from a hit another handler cancelled is ignored after this

local baseCombat = I.Combat
local pendingAttacker
local pendingAttackerTime = 0

-- onHit handlers run newest-first, so this sees the attack before the builtin handler calls spawnBloodEffect
baseCombat.addOnHitHandler(function(attack)
    pendingAttacker = attack.attacker
    pendingAttackerTime = core.getSimulationTime()
end)

local combat = auxUtil.shallowCopy(baseCombat)
combat.spawnBloodEffect = function(position)
    baseCombat.spawnBloodEffect(position)
    local attacker = pendingAttacker
    if core.getSimulationTime() - pendingAttackerTime > ATTACKER_TIMEOUT then attacker = nil end
    pendingAttacker = nil
    local color = getDecalColor()
    if not color then return end

    sendToPlayers('TheyBleed_Hit', {
        color = color,
        isPlayer = types.Player.objectIsInstance(self.object),
        hitPos = position,
        victim = self.object,
        victimPos = self.position,
        attackerPos = attacker and attacker:isValid() and attacker.position or nil,
    })
    -- damage is applied after this call, so check the new health on the next tick
    async:newUnsavableSimulationTimer(0, startBleedingIfWounded)
end

return {
    interfaceName = 'Combat',
    interface = combat,
}
