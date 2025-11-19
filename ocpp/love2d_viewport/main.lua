local DATA_PATH = "data/connectors.json"

local connectors = {}
local time_alive = 0

local function parse_hex_color(hex)
    if not hex or hex == "" then
        return 0.06, 0.08, 0.12
    end

    local normalized = hex:gsub("#", "")
    if #normalized == 3 then
        normalized = normalized:gsub(".", "%1%1")
    end

    if #normalized ~= 6 then
        return 0.06, 0.08, 0.12
    end

    local r = tonumber(normalized:sub(1, 2), 16) or 12
    local g = tonumber(normalized:sub(3, 4), 16) or 20
    local b = tonumber(normalized:sub(5, 6), 16) or 28
    return r / 255, g / 255, b / 255
end

local function load_data()
    if not love.filesystem.getInfo(DATA_PATH) then
        return
    end

    local contents = love.filesystem.read(DATA_PATH)
    if not contents then
        return
    end

    local ok, decoded = pcall(love.data.decode, "string", "json", contents)
    if ok and decoded and decoded.connectors then
        connectors = decoded.connectors
    end
end

local function draw_car_widget(connector, x, y, width, height)
    local padding = 12
    local body_height = height * 0.45
    local body_y = y + height * 0.25
    local accent_r, accent_g, accent_b = parse_hex_color(connector.status_color)

    love.graphics.setColor(0.12, 0.15, 0.2)
    love.graphics.rectangle("fill", x, y, width, height, 10, 10)

    love.graphics.setColor(1, 1, 1)
    local title = connector.display_name ~= "" and connector.display_name or connector.serial
    love.graphics.print(title, x + padding, y + padding)

    love.graphics.setColor(accent_r, accent_g, accent_b)
    love.graphics.print(connector.connector_label, x + padding, y + padding + 18)

    love.graphics.setColor(0.15, 0.2, 0.28)
    love.graphics.rectangle("fill", x + padding, body_y, width - padding * 2, body_height, 12, 12)

    love.graphics.setColor(accent_r, accent_g, accent_b)
    love.graphics.rectangle("fill", x + padding + 8, body_y + 8, width - padding * 2 - 16, body_height - 16, 8, 8)

    love.graphics.setColor(0.05, 0.05, 0.05)
    local wheel_radius = 10
    love.graphics.circle("fill", x + width * 0.25, body_y + body_height + wheel_radius, wheel_radius)
    love.graphics.circle("fill", x + width * 0.75, body_y + body_height + wheel_radius, wheel_radius)

    local battery_width = width - padding * 2
    local battery_height = 16
    local battery_x = x + padding
    local battery_y = y + height - padding - battery_height

    love.graphics.setColor(0.2, 0.24, 0.3)
    love.graphics.rectangle("fill", battery_x, battery_y, battery_width, battery_height, 4, 4)

    local progress = 0.2
    if connector.is_charging then
        progress = 0.35 + 0.6 * ((math.sin(time_alive * 3) + 1) / 2)
    end
    local fill_width = battery_width * progress

    love.graphics.setColor(accent_r, accent_g, accent_b)
    love.graphics.rectangle("fill", battery_x + 2, battery_y + 2, fill_width - 4, battery_height - 4, 3, 3)

    love.graphics.setColor(1, 1, 1)
    love.graphics.print(connector.status_label, battery_x + 4, battery_y - 18)
end

function love.load()
    love.window.setTitle("Connector Viewport")
    love.window.setMode(960, 640, { resizable = true, minwidth = 640, minheight = 400 })
    love.graphics.setBackgroundColor(0.04, 0.05, 0.07)
    load_data()
end

function love.update(dt)
    time_alive = time_alive + dt
end

function love.draw()
    local padding = 18
    local card_width = 240
    local card_height = 160
    local available_width = love.graphics.getWidth() - padding
    local columns = math.max(1, math.floor(available_width / (card_width + padding)))

    if #connectors == 0 then
        love.graphics.setColor(1, 1, 1)
        love.graphics.print("No connectors available. Generate a snapshot and reload (press R).", padding, padding)
        return
    end

    for i, connector in ipairs(connectors) do
        local col = (i - 1) % columns
        local row = math.floor((i - 1) / columns)
        local x = padding + col * (card_width + padding)
        local y = padding + row * (card_height + padding)
        draw_car_widget(connector, x, y, card_width, card_height)
    end
end

function love.keypressed(key)
    if key == "r" then
        load_data()
    end
end
