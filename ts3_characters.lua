-- Toy Story 3 Characters Mod
-- Registers CrabeMenu quick-swap entries for T3_Woody, T3_Jessie, T3_Buzz, and T3_Zurg.

Crabe = Crabe or {}

local CHARS = {
    { name = "T3_Woody",  label = "TS3 Woody" },
    { name = "T3_Jessie", label = "TS3 Jessie" },
    { name = "T3_Buzz",   label = "TS3 Buzz Lightyear" },
    { name = "T3_Zurg",   label = "TS3 Emperor Zurg" },
}

local function registerEntries()
    if not (Crabe.Menu and Crabe.Menu.registerInCategory) then return end
    for _, c in ipairs(CHARS) do
        local actor = c.name
        local label = c.label
        Crabe.Menu.registerInCategory("Modded Characters", {
            label = label,
            action = function()
                local sku = Crabe.VirtualReader and Crabe.VirtualReader.skuForName
                    and tonumber(Crabe.VirtualReader.skuForName(actor))
                if sku and type(Game) == "table" and type(Game.SetCharacter) == "function" then
                    Game.SetCharacter(sku, "loadout")
                    return string.format("Swapped to %s (SKU %d)", actor, sku)
                elseif Crabe.VirtualReader and Crabe.VirtualReader.placeCharacter then
                    Crabe.VirtualReader.placeCharacter(actor)
                    return string.format("Placed %s via VirtualReader", actor)
                end
                return "Game API not ready."
            end,
        })
    end
end

registerEntries()
print("[TS3] Toy Story 3 Characters mod loaded (T3_Woody, T3_Jessie, T3_Buzz, T3_Zurg).")
