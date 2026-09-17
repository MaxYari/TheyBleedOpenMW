local storage = require("openmw.storage")
local async = require("openmw.async")

--- A little helper that for live settings update without constantly hitting the storage
-- (from SneakIsGoodNow; `section` is optional so global scripts can pass storage.globalSection,
-- `onChange` is called after the cached values were refreshed)
local SettingsHelper = {}
SettingsHelper.__index = SettingsHelper
function SettingsHelper:new(sectionName, section, onChange)
    local inst = {
        sectionName = sectionName,
        store = section or storage.playerSection(sectionName),
        settings = {},
        trackedSettings = {}
    }

    inst.store:subscribe(async:callback(function(val)
        for key, _ in pairs(inst.trackedSettings) do
            inst.settings[key] = inst.store:get(key)
        end
        if onChange then onChange() end
    end))

    -- Im not sure how this whole metatable nonesense works - but it does
    setmetatable(inst, self)

    return inst
end

function SettingsHelper:__index(key)
    if rawget(self, key) then return rawget(self, key) end
    if not self.trackedSettings[key] then
        self.trackedSettings[key] = true
        self.settings[key] = self.store:get(key)
    end
    return self.settings[key]
end

return SettingsHelper
