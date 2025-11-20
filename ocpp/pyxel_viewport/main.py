from __future__ import annotations

import json
import math
from pathlib import Path

import pyxel

DATA_PATH = Path(__file__).resolve().parent / "data" / "connectors.json"

DEFAULT_PALETTE = [
    0x000000,
    0x1D2B53,
    0x7E2553,
    0x008751,
    0xAB5236,
    0x5F574F,
    0xC2C3C7,
    0xFFF1E8,
    0xFF004D,
    0xFFA300,
    0xFFEC27,
    0x00E436,
    0x29ADFF,
    0x83769C,
    0xFF77A8,
    0xFFCCAA,
]


def _rgb_from_hex(hex_color: str | None) -> tuple[int, int, int]:
    if not hex_color:
        return 12, 20, 28

    normalized = hex_color.lstrip("#")
    if len(normalized) == 3:
        normalized = "".join(ch * 2 for ch in normalized)
    if len(normalized) != 6:
        return 12, 20, 28

    try:
        return tuple(int(normalized[i : i + 2], 16) for i in (0, 2, 4))  # type: ignore[misc]
    except ValueError:
        return 12, 20, 28


def _nearest_palette_index(hex_color: str | None) -> int:
    target_r, target_g, target_b = _rgb_from_hex(hex_color)
    best_index = 1
    best_distance = float("inf")

    for index, value in enumerate(DEFAULT_PALETTE):
        palette_r = (value >> 16) & 0xFF
        palette_g = (value >> 8) & 0xFF
        palette_b = value & 0xFF
        distance = (palette_r - target_r) ** 2 + (palette_g - target_g) ** 2 + (palette_b - target_b) ** 2
        if distance < best_distance:
            best_distance = distance
            best_index = index

    return best_index


class ConnectorViewport:
    def __init__(self) -> None:
        self.connectors: list[dict] = []
        self._load_connectors()

        pyxel.init(480, 320, title="Connector Viewport", fps=30)
        pyxel.run(self.update, self.draw)

    def _load_connectors(self) -> None:
        try:
            payload = json.loads(DATA_PATH.read_text())
        except (FileNotFoundError, json.JSONDecodeError):
            self.connectors = []
            return

        self.connectors = payload.get("connectors", []) or []

    def update(self) -> None:
        if pyxel.btnp(pyxel.KEY_R):
            self._load_connectors()

    def draw(self) -> None:
        pyxel.cls(1)

        padding = 10
        card_width = 150
        card_height = 90
        columns = max(1, (pyxel.width - padding) // (card_width + padding))

        if not self.connectors:
            pyxel.text(padding, padding, "No connectors available. Press R to reload.", 7)
            return

        for idx, connector in enumerate(self.connectors):
            col = idx % columns
            row = idx // columns
            x = padding + col * (card_width + padding)
            y = padding + row * (card_height + padding)
            self._draw_card(connector, x, y, card_width, card_height)

    def _draw_card(self, connector: dict, x: int, y: int, width: int, height: int) -> None:
        accent = _nearest_palette_index(connector.get("status_color"))
        text_color = 7
        detail_color = 6

        pyxel.rect(x, y, width, height, 0)
        pyxel.rectb(x, y, width, height, accent)

        title = connector.get("display_name") or connector.get("serial") or "Connector"
        pyxel.text(x + 6, y + 6, str(title), text_color)
        connector_label = str(connector.get("connector_label", "")).strip()
        if connector_label:
            pyxel.text(x + 6, y + 16, connector_label, accent)

        location = connector.get("location") or ""
        if location:
            pyxel.text(x + 6, y + height - 32, str(location)[:26], detail_color)

        body_top = y + 26
        body_height = height - 48
        pyxel.rect(x + 6, body_top, width - 12, body_height, 1)
        pyxel.rectb(x + 6, body_top, width - 12, body_height, accent)

        wheel_y = body_top + body_height - 6
        pyxel.circ(x + 20, wheel_y, 5, detail_color)
        pyxel.circ(x + width - 20, wheel_y, 5, detail_color)

        battery_width = width - 24
        battery_height = 10
        battery_x = x + 12
        battery_y = y + height - 18

        pyxel.rect(battery_x, battery_y, battery_width, battery_height, 1)
        pyxel.rectb(battery_x, battery_y, battery_width, battery_height, accent)

        progress = 0.25
        if connector.get("is_charging"):
            progress = 0.35 + 0.6 * (0.5 + 0.5 * math.sin(pyxel.frame_count / 12))

        fill_width = max(0, int((battery_width - 4) * progress))
        pyxel.rect(battery_x + 2, battery_y + 2, fill_width, battery_height - 4, accent)

        status_label = str(connector.get("status_label", ""))
        pyxel.text(battery_x, battery_y - 10, status_label[:26], text_color)


def main() -> None:
    ConnectorViewport()


if __name__ == "__main__":
    main()
