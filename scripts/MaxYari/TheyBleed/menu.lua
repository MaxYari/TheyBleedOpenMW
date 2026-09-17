-- Menu context: setting renderers can only be registered here. Draws the title logo at the top of the settings page.
local I = require('openmw.interfaces')
local ui = require('openmw.ui')
local util = require('openmw.util')

-- Content rectangle of the logo inside its 1024x512 texture, as printed by tools/make_logo.py
local LOGO_TEXTURE = ui.texture {
    path = 'textures/MaxYari/TheyBleed/logo.dds',
    offset = util.vector2(4, 109),
    size = util.vector2(1016, 294),
}
local LOGO_WIDTH = 480

I.Settings.registerRenderer('TheyBleedLogo', function()
    return {
        type = ui.TYPE.Flex,
        -- the settings row puts renderers on the right; out-growing the row's spacer centres the logo instead
        external = { grow = 1000 },
        props = { horizontal = true, align = ui.ALIGNMENT.Center, arrange = ui.ALIGNMENT.Center },
        content = ui.content {
            {
                type = ui.TYPE.Image,
                props = {
                    resource = LOGO_TEXTURE,
                    size = util.vector2(LOGO_WIDTH, LOGO_WIDTH * 294 / 1016),
                },
            },
        },
    }
end)

return {}
