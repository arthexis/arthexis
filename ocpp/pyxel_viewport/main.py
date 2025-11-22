from __future__ import annotations

import json
import math
import uuid
from pathlib import Path

import pyxel

DATA_PATH = Path(__file__).resolve().parent / "data" / "connectors.json"
ACTION_REQUEST_PATH = DATA_PATH.parent / "pyxel_action_request.json"
ACTION_RESPONSE_PATH = DATA_PATH.parent / "pyxel_action_response.json"

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
        self.instance_running = False
        self._has_loaded_snapshot = False
        self._last_mtime: float | None = None
        self._last_reload_frame = 0
        self._loading_progress = 0
        self._menu_open = False
        self._pending_action_token: str | None = None
        self._action_feedback = ""
        self._action_feedback_frame = 0
        self._quick_actions = (
            {"label": "Start Default Simulator", "action": "start_default_simulator"},
        )

        pyxel.init(480, 320, title="Connector Viewport", fps=30)
        pyxel.mouse(True)
        self._load_connectors(force=True)
        pyxel.run(self.update, self.draw)

    def _load_connectors(self, *, force: bool = False) -> None:
        try:
            stat = DATA_PATH.stat()
        except FileNotFoundError:
            if force:
                self._has_loaded_snapshot = False
            return

        if not force and self._last_mtime and stat.st_mtime <= self._last_mtime:
            return

        try:
            payload = json.loads(DATA_PATH.read_text())
        except (FileNotFoundError, json.JSONDecodeError):
            self.connectors = []
            self._has_loaded_snapshot = False
            self.instance_running = False
            return

        self._last_mtime = stat.st_mtime
        self._has_loaded_snapshot = True
        self.instance_running = bool(payload.get("instance_running"))
        self.connectors = payload.get("connectors", []) or []

    def update(self) -> None:
        if pyxel.btnp(pyxel.KEY_R):
            self._load_connectors(force=True)

        self._auto_reload()
        self._check_action_response()
        self._handle_mouse()
        self._advance_loading()
        self._expire_feedback()

    def draw(self) -> None:
        pyxel.cls(1)

        if not self._has_loaded_snapshot or (not self.instance_running and not self.connectors):
            self._draw_loading()
            self._draw_menu()
            self._draw_feedback()
            return

        padding = 10
        card_width = 150
        card_height = 90
        columns = max(1, (pyxel.width - padding) // (card_width + padding))

        if not self.connectors:
            pyxel.text(padding, padding, "No connectors available. Press R to reload.", 7)
            self._draw_menu()
            self._draw_feedback()
            return

        if not self.instance_running:
            pyxel.text(padding, padding - 6, "Instance is starting...", 6)

        for idx, connector in enumerate(self.connectors):
            col = idx % columns
            row = idx // columns
            x = padding + col * (card_width + padding)
            y = padding + row * (card_height + padding)
            self._draw_card(connector, x, y, card_width, card_height)

        self._draw_menu()
        self._draw_feedback()

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

    def _auto_reload(self) -> None:
        if pyxel.frame_count - self._last_reload_frame < 30:
            return
        self._last_reload_frame = pyxel.frame_count
        self._load_connectors()

    def _advance_loading(self) -> None:
        self._loading_progress = (self._loading_progress + 0.02) % 1.0

    def _draw_loading(self) -> None:
        width = pyxel.width - 20
        bar_width = int(width * 0.7)
        bar_height = 12
        x = (pyxel.width - bar_width) // 2
        y = (pyxel.height // 2) - (bar_height // 2)

        pyxel.text(10, 10, "Starting instance...", 7)
        pyxel.rect(x, y, bar_width, bar_height, 0)
        pyxel.rectb(x, y, bar_width, bar_height, 7)
        fill = int(bar_width * self._loading_progress)
        pyxel.rect(x + 1, y + 1, max(fill - 2, 0), bar_height - 2, 11)
        pyxel.text(x, y + bar_height + 6, "Waiting for services to come online...", 6)

    def _menu_hitbox(self) -> tuple[int, int, int, int]:
        size = 14
        margin = 6
        x = pyxel.width - size - margin
        y = margin
        return x, y, size, size

    def _draw_menu(self) -> None:
        x, y, size, size_value = self._menu_hitbox()
        pyxel.rect(x, y, size_value, size_value, 0)
        pyxel.rectb(x, y, size_value, size_value, 7)
        for idx in range(3):
            pyxel.rect(x + 3, y + 3 + idx * 4, size_value - 6, 1, 7)

        if not self._menu_open:
            return

        menu_width = 150
        menu_x = x - menu_width + size_value
        menu_y = y + size_value + 4
        menu_height = 14 + 14 * len(self._quick_actions)

        pyxel.rect(menu_x, menu_y, menu_width, menu_height, 0)
        pyxel.rectb(menu_x, menu_y, menu_width, menu_height, 7)
        pyxel.text(menu_x + 6, menu_y + 2, "Quick actions", 7)

        for index, action in enumerate(self._quick_actions):
            item_y = menu_y + 8 + index * 14
            pyxel.rect(menu_x + 4, item_y + 4, menu_width - 8, 10, 1)
            pyxel.text(menu_x + 8, item_y + 6, action["label"][:28], 7)

    def _handle_mouse(self) -> None:
        if not pyxel.btnp(pyxel.MOUSE_BUTTON_LEFT):
            return

        mx, my = pyxel.mouse_x, pyxel.mouse_y
        btn_x, btn_y, btn_w, btn_h = self._menu_hitbox()
        if btn_x <= mx <= btn_x + btn_w and btn_y <= my <= btn_y + btn_h:
            self._menu_open = not self._menu_open
            return

        if not self._menu_open:
            return

        menu_width = 150
        menu_x = btn_x - menu_width + btn_w
        menu_y = btn_y + btn_h + 4
        for index, action in enumerate(self._quick_actions):
            item_y = menu_y + 8 + index * 14
            item_height = 10
            if menu_x + 4 <= mx <= menu_x + menu_width - 4 and item_y + 4 <= my <= item_y + 4 + item_height:
                self._queue_action(action)
                self._menu_open = False
                return

    def _queue_action(self, action: dict) -> None:
        token = uuid.uuid4().hex
        payload = {"action": action["action"], "token": token}
        try:
            ACTION_REQUEST_PATH.parent.mkdir(parents=True, exist_ok=True)
            ACTION_REQUEST_PATH.write_text(json.dumps(payload, indent=2))
        except OSError:
            self._action_feedback = "Failed to queue action."
            self._action_feedback_frame = pyxel.frame_count
            self._pending_action_token = None
            return

        self._pending_action_token = token
        self._action_feedback = f"{action['label']}..."
        self._action_feedback_frame = pyxel.frame_count

    def _check_action_response(self) -> None:
        if not self._pending_action_token:
            return

        try:
            payload = json.loads(ACTION_RESPONSE_PATH.read_text())
        except (FileNotFoundError, json.JSONDecodeError):
            return

        if payload.get("token") != self._pending_action_token:
            return

        success = bool(payload.get("success"))
        message = str(payload.get("message") or ("Action completed" if success else "Action failed"))
        self._action_feedback = message
        self._action_feedback_frame = pyxel.frame_count
        self._pending_action_token = None

    def _expire_feedback(self) -> None:
        if not self._action_feedback:
            return
        if pyxel.frame_count - self._action_feedback_frame > 180:
            self._action_feedback = ""

    def _draw_feedback(self) -> None:
        if not self._action_feedback:
            return
        text = self._action_feedback[:32]
        text_width = len(text) * 4
        x = pyxel.width - text_width - 6
        y = pyxel.height - 10
        pyxel.rect(x - 2, y - 2, text_width + 4, 10, 0)
        pyxel.text(x, y, text, 10 if self._pending_action_token else 7)


def main() -> None:
    ConnectorViewport()


if __name__ == "__main__":
    main()
