-- =======================================================
-- KINO — Pont d'intégration fenêtré MPV & KINO (Win32)
-- Synchronise le plein écran, l'agrandissement, le déplacement
-- et le redimensionnement de la fenêtre parente KINO.
-- =======================================================

local ffi = require("ffi")
ffi.cdef[[
    void* GetParent(void* hWnd);
    bool ReleaseCapture();
    int SendMessageW(void* hWnd, unsigned int Msg, unsigned int wParam, long lParam);
    void* LoadCursorW(void* hInstance, int lpCursorName);
    void* SetCursor(void* hCursor);
]]
local u32 = ffi.load("user32")

local IDC_ARROW = 32512
local IDC_SIZENS = 32645
local IDC_SIZEWE = 32644
local IDC_SIZENWSE = 32642
local IDC_SIZENESW = 32643

local cursor_arrow = u32.LoadCursorW(nil, IDC_ARROW)
local cursor_ns = u32.LoadCursorW(nil, IDC_SIZENS)
local cursor_we = u32.LoadCursorW(nil, IDC_SIZEWE)
local cursor_nwse = u32.LoadCursorW(nil, IDC_SIZENWSE)
local cursor_nesw = u32.LoadCursorW(nil, IDC_SIZENESW)

local function get_parent()
    local wid = mp.get_property_number("window-id")
    if wid and wid > 0 then
        return u32.GetParent(ffi.cast("void*", wid))
    end
    return nil
end

local function get_border_hit(x, y, w, h)
    local b = 8
    local at_l = x <= b
    local at_r = x >= (w - b)
    local at_t = y <= b
    local at_b = y >= (h - b)

    if at_t and at_l then return 13, cursor_nwse
    elseif at_t and at_r then return 14, cursor_nesw
    elseif at_b and at_l then return 16, cursor_nesw
    elseif at_b and at_r then return 17, cursor_nwse
    elseif at_l then return 10, cursor_we
    elseif at_r then return 11, cursor_we
    elseif at_t then return 12, cursor_ns
    elseif at_b then return 15, cursor_ns
    end
    return nil, nil
end

-- Curseur adaptatif sur les bordures pour le redimensionnement
mp.observe_property("mouse-pos", "native", function(_, pos)
    if not pos then return end
    local is_fs = mp.get_property_bool("fullscreen", false)
    local is_max = mp.get_property_bool("window-maximized", false)
    if is_fs or is_max then return end

    local w = mp.get_property_number("osd-width", 1280)
    local h = mp.get_property_number("osd-height", 720)
    local hit, cur = get_border_hit(pos.x, pos.y, w, h)
    if cur then
        u32.SetCursor(cur)
    end
end)

-- Clic gauche : redimensionnement sur les bords, glisser sur la barre du haut, pause sinon
mp.add_key_binding("MBTN_LEFT", "kino_click", function()
    local is_fs = mp.get_property_bool("fullscreen", false)
    local is_max = mp.get_property_bool("window-maximized", false)
    local parent = get_parent()

    if parent ~= nil and not is_fs then
        local x, y = mp.get_mouse_pos()
        local w = mp.get_property_number("osd-width", 1280)
        local h = mp.get_property_number("osd-height", 720)

        if not is_max then
            local hit_code, _ = get_border_hit(x, y, w, h)
            if hit_code then
                u32.ReleaseCapture()
                u32.SendMessageW(parent, 0x00A1, hit_code, 0)
                return
            end
        end

        -- Déplacement de la fenêtre KINO par la barre supérieure (top 42px)
        -- On laisse les marges gauche/droite pour les boutons de contrôle uosc
        if y <= 42 and x >= 130 and x <= (w - 140) then
            u32.ReleaseCapture()
            u32.SendMessageW(parent, 0x00A1, 2, 0) -- HTCAPTION
            return
        end
    end

    mp.command("cycle pause")
end)

-- Double clic : maximiser si barre du haut, plein écran sinon
mp.add_key_binding("MBTN_LEFT_DBL", "kino_dblclick", function()
    local is_fs = mp.get_property_bool("fullscreen", false)
    local x, y = mp.get_mouse_pos()
    if y <= 42 and not is_fs then
        mp.command("cycle window-maximized")
    else
        mp.command("cycle fullscreen")
    end
end)

-- Touche f pour basculer le plein écran
mp.add_key_binding("f", "kino_fullscreen_key", function()
    mp.command("cycle fullscreen")
end)

-- Touche Échap intelligente : quitter le plein écran si actif, fermer le lecteur sinon
mp.add_key_binding("ESC", "kino_esc", function()
    local is_fs = mp.get_property_bool("fullscreen", false)
    if is_fs then
        mp.set_property_bool("fullscreen", false)
    else
        mp.command("quit")
    end
end)
