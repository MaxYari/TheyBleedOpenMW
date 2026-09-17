-- Settings group (global storage, so the global script can read it too) and a shared accessor.
-- Registered from the global script; the page itself is registered by the player script.
local storage = require('openmw.storage')
local I = require('openmw.interfaces')
local SettingsHelper = require('scripts.MaxYari.TheyBleed.settings_helper')

local M = {}

M.PAGE = 'TheyBleedPage'
M.GROUP = 'SettingsTheyBleed'
M.LOGO_GROUP = 'SettingsTheyBleedLogo'

M.DEFAULTS = {
    MaxDecals = 200,
    BloodAmount = 1,
    SoundVolume = 100,
    DebugSpray = false,
}

function M.registerPage()
    I.Settings.registerPage {
        key = M.PAGE,
        l10n = 'TheyBleed',
        name = 'THEY BLEED',
        description = 'Blood splatters, pools and drips left by wounds. Changes apply immediately.',
    }
end

function M.registerGroup()
    -- logo on top of the page; the TheyBleedLogo renderer is registered by menu.lua
    I.Settings.registerGroup {
        key = M.LOGO_GROUP,
        page = M.PAGE,
        l10n = 'TheyBleed',
        name = '',
        order = 0,
        permanentStorage = false,
        settings = {
            { key = 'Logo', renderer = 'TheyBleedLogo', default = '', name = '' },
        },
    }
    I.Settings.registerGroup {
        key = M.GROUP,
        page = M.PAGE,
        l10n = 'TheyBleed',
        name = 'Blood',
        order = 1,
        permanentStorage = true,
        settings = {
            {
                key = 'MaxDecals',
                renderer = 'number',
                default = M.DEFAULTS.MaxDecals,
                argument = { min = 0, max = 1000, integer = true },
                name = 'Max Decals',
                description = 'Blood decals kept in the world at once. Past this, random older ones are removed. '
                    .. 'Every visible decal is a draw call, so lower this if big fights cost frames.',
            },
            {
                key = 'BloodAmount',
                renderer = 'number',
                default = M.DEFAULTS.BloodAmount,
                argument = { min = 0, max = 5 },
                name = 'Blood Amount',
                description = 'Multiplies the decals a hit sprays (6 on NPCs and creatures, 1 on the player), '
                    .. 'up to 30 per hit. 0 turns hit blood off; wounded actors still drip.',
            },
            {
                key = 'SoundVolume',
                renderer = 'number',
                default = M.DEFAULTS.SoundVolume,
                argument = { min = 0, max = 400 },
                name = 'Impact Sound Volume',
                description = 'Volume of blood landing, in percent.',
            },
            {
                key = 'DebugSpray',
                renderer = 'checkbox',
                default = M.DEFAULTS.DebugSpray,
                name = 'Debug: Spray With Right Mouse',
                description = 'Hold the right mouse button to spray decals of every colour where you aim.',
            },
        },
    }
end

-- Cached accessor with live updates; missing values fall back to DEFAULTS
-- (e.g. when a script reads before the group registration reached storage).
function M.new(onChange)
    local helper = SettingsHelper:new(M.GROUP, storage.globalSection(M.GROUP), onChange)
    return setmetatable({}, {
        __index = function(_, key)
            local value = helper[key]
            if value == nil then return M.DEFAULTS[key] end
            return value
        end,
    })
end

return M
