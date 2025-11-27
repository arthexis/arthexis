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
        self.waiting_for_instance = False
        self._has_loaded_snapshot = False
        self._last_mtime: float | None = None
        self._last_reload_frame = 0
        self._loading_progress = 0
        self._menu_open = False
        self._pending_action_token: str | None = None
        self._action_feedback = ""
        self._suite_host = "127.0.0.1"
        self._suite_port: int | None = None
        self._page_start = 0
        self._selected_connector_key: str | None = None
        self._last_click_key: str | None = None
        self._last_click_frame = 0
        self._info_overlay_visible = False
        self._top_bar_height = 18
        self._bottom_bar_height = 18
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
            self.waiting_for_instance = False
            return

        self._last_mtime = stat.st_mtime
        self._has_loaded_snapshot = True
        self.instance_running = bool(payload.get("instance_running"))
        self.waiting_for_instance = bool(payload.get("waiting_for_instance"))
        self.connectors = payload.get("connectors", []) or []
        self._suite_host = str(payload.get("suite_host") or "127.0.0.1").strip() or "127.0.0.1"
        suite_port = payload.get("suite_port")
        try:
            parsed_port = int(suite_port)
            self._suite_port = parsed_port if 1 <= parsed_port <= 65535 else None
        except (TypeError, ValueError):
            self._suite_port = None

        self._clamp_page_start()

    def _clamp_page_start(self) -> None:
        total = len(self._local_connectors())
        slots = self._visible_slots(total)
        max_start = max(0, total - slots)
        self._page_start = min(self._page_start, max_start)

    def _local_connectors(self) -> list[dict]:
        return [connector for connector in self.connectors if not bool(connector.get("is_evcs"))]

    def _visible_slots(self, total_connectors: int) -> int:
        return 1 if total_connectors <= 1 else 2

    def _connector_key(self, connector: dict) -> str:
        serial = connector.get("serial") or ""
        connector_id = connector.get("connector_id") or ""
        return f"{serial}:{connector_id}"

    def update(self) -> None:
        if pyxel.btnp(pyxel.KEY_R):
            self._load_connectors(force=True)

        self._auto_reload()
        self._check_action_response()
        self._handle_mouse()
        self._advance_loading()

    def draw(self) -> None:
        pyxel.cls(1)

        if self.waiting_for_instance and not self.instance_running:
            self._draw_loading()
            self._draw_top_bar()
            self._draw_bottom_bar()
            if self._menu_open:
                self._draw_command_menu()
            if self._info_overlay_visible:
                self._draw_info_overlay()
            return

        if not self._has_loaded_snapshot or (not self.instance_running and not self.connectors):
            self._draw_loading()
            self._draw_top_bar()
            self._draw_bottom_bar()
            if self._menu_open:
                self._draw_command_menu()
            if self._info_overlay_visible:
                self._draw_info_overlay()
            return

        if not self.instance_running:
            pyxel.text(10, self._top_bar_height + 2, "Instance is starting...", 6)

        self._draw_connector_area()
        self._draw_navigation_arrows()
        self._draw_top_bar()
        self._draw_bottom_bar()
        if self._menu_open:
            self._draw_command_menu()
        if self._info_overlay_visible:
            self._draw_info_overlay()

    def _layout_connectors(self) -> list[tuple[dict, int, int, int, int]]:
        local_connectors = self._local_connectors()
        total = len(local_connectors)
        slots = self._visible_slots(total)
        start = min(self._page_start, max(0, total - slots))
        visible = local_connectors[start : start + slots]

        area_y = self._top_bar_height
        area_height = pyxel.height - self._top_bar_height - self._bottom_bar_height
        padding = 10
        card_height = max(82, area_height - padding * 2)

        layouts: list[tuple[dict, int, int, int, int]] = []
        if slots == 1:
            card_width = pyxel.width - padding * 2
            x = padding
            y = area_y + (area_height - card_height) // 2
            for connector in visible:
                layouts.append((connector, x, y, card_width, card_height))
            return layouts

        card_width = (pyxel.width - padding * 3) // 2
        y = area_y + (area_height - card_height) // 2
        for index, connector in enumerate(visible):
            x = padding + index * (card_width + padding)
            layouts.append((connector, x, y, card_width, card_height))
        return layouts

    def _draw_connector_area(self) -> None:
        layouts = self._layout_connectors()
        if not layouts:
            message = "No connectors available. Press R to reload."
            pyxel.text(10, self._top_bar_height + 10, message, 7)
            return

        for connector, x, y, width, height in layouts:
            self._draw_card(connector, x, y, width, height)
            if self._connector_key(connector) == self._selected_connector_key:
                pyxel.rectb(x - 2, y - 2, width + 4, height + 4, 11)

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

    def _draw_navigation_arrows(self) -> None:
        if len(self._local_connectors()) <= 2:
            return

        area_y = self._top_bar_height
        area_height = pyxel.height - self._top_bar_height - self._bottom_bar_height
        center_y = area_y + area_height // 2
        size = 12

        pyxel.rect(0, center_y - size, size + 4, size * 2, 0)
        pyxel.rectb(0, center_y - size, size + 4, size * 2, 7)
        pyxel.tri(size + 2, center_y, 2, center_y - size, 2, center_y + size, 7)

        pyxel.rect(pyxel.width - size - 4, center_y - size, size + 4, size * 2, 0)
        pyxel.rectb(pyxel.width - size - 4, center_y - size, size + 4, size * 2, 7)
        pyxel.tri(pyxel.width - size - 2, center_y, pyxel.width - 2, center_y - size, pyxel.width - 2, center_y + size, 7)

    def _advance_page(self, delta: int) -> None:
        total = len(self._local_connectors())
        slots = self._visible_slots(total)
        if total <= slots:
            return

        self._page_start = max(0, min(self._page_start + delta * slots, total - slots))

    def _draw_loading(self) -> None:
        width = pyxel.width - 20
        bar_width = int(width * 0.7)
        bar_height = 12
        x = (pyxel.width - bar_width) // 2
        y = (pyxel.height // 2) - (bar_height // 2)

        pyxel.text(10, self._top_bar_height + 4, "Loading...", 7)
        pyxel.rect(x, y, bar_width, bar_height, 0)
        pyxel.rectb(x, y, bar_width, bar_height, 7)
        fill = int(bar_width * self._loading_progress)
        pyxel.rect(x + 1, y + 1, max(fill - 2, 0), bar_height - 2, 11)
        pyxel.text(x, y + bar_height + 6, "Waiting for services to come online...", 6)

    def _top_bar_hitbox(self) -> tuple[int, int, int, int]:
        return 0, 0, pyxel.width, self._top_bar_height

    def _bottom_bar_hitbox(self) -> tuple[int, int, int, int]:
        return 0, pyxel.height - self._bottom_bar_height, pyxel.width, self._bottom_bar_height

    def _draw_top_bar(self) -> None:
        x, y, width, height = self._top_bar_hitbox()
        pyxel.rect(x, y, width, height, 0)
        pyxel.text(x + 6, y + 5, self._action_feedback or "Tap for commands", 7)

    def _server_info(self) -> str:
        connector_count = len(self._local_connectors())
        label = self._suite_label() or "Server info unavailable"
        status = "Running" if self.instance_running else "Offline"
        return f"{label} | {status} | {connector_count} local connector(s)"

    def _draw_bottom_bar(self) -> None:
        x, y, width, height = self._bottom_bar_hitbox()
        pyxel.rect(x, y, width, height, 0)
        pyxel.text(x + 6, y + 5, self._server_info()[:60], 7)

    def _draw_info_overlay(self) -> None:
        padding = 8
        overlay_width = pyxel.width - 60
        overlay_height = 120
        x = (pyxel.width - overlay_width) // 2
        y = (pyxel.height - overlay_height) // 2

        pyxel.rect(x, y, overlay_width, overlay_height, 0)
        pyxel.rectb(x, y, overlay_width, overlay_height, 7)

        local_connectors = self._local_connectors()
        slots = self._visible_slots(len(local_connectors))
        start = min(self._page_start, max(0, len(local_connectors) - slots))
        end = min(len(local_connectors), start + slots)
        host_label = f"{self._suite_host}:{self._suite_port}" if self._suite_port else self._suite_host

        info_lines = [
            self._server_info(),
            f"Host: {host_label or 'Unknown'}",
            f"Instance running: {'Yes' if self.instance_running else 'No'}",
            f"Waiting for instance: {'Yes' if self.waiting_for_instance else 'No'}",
            f"Showing: {start + 1 if local_connectors else 0}-{end} of {len(local_connectors)} local connector(s)",
            f"Selected connector: {self._selected_connector_key or 'None'}",
            f"Last operation: {self._action_feedback or 'None'}",
        ]

        for index, line in enumerate(info_lines):
            pyxel.text(x + padding, y + padding + index * 12, line[:overlay_width // 4], 7)

    def _command_menu_bounds(self) -> tuple[int, int, int, int]:
        menu_width = 170
        menu_height = 18 + 14 * len(self._quick_actions)
        x = (pyxel.width - menu_width) // 2
        y = (pyxel.height - menu_height) // 2
        return x, y, menu_width, menu_height

    def _draw_command_menu(self) -> None:
        x, y, menu_width, menu_height = self._command_menu_bounds()
        pyxel.rect(x, y, menu_width, menu_height, 0)
        pyxel.rectb(x, y, menu_width, menu_height, 7)
        pyxel.text(x + 6, y + 4, "Commands", 7)

        for index, action in enumerate(self._quick_actions):
            item_y = y + 10 + index * 14
            pyxel.rect(x + 4, item_y + 4, menu_width - 8, 10, 1)
            pyxel.text(x + 8, item_y + 6, action["label"][:28], 7)

    def _handle_mouse(self) -> None:
        mx, my = pyxel.mouse_x, pyxel.mouse_y

        if pyxel.btnp(pyxel.MOUSE_BUTTON_LEFT):
            if self._info_overlay_visible:
                self._info_overlay_visible = False
                return

            if self._handle_top_bar_click(mx, my):
                return
            if self._handle_bottom_bar_click(mx, my):
                return
            if self._handle_command_menu_click(mx, my):
                return
            if self._handle_arrow_click(mx, my):
                return
            self._handle_connector_click(mx, my)

    def _handle_top_bar_click(self, mx: int, my: int) -> bool:
        x, y, width, height = self._top_bar_hitbox()
        if x <= mx <= x + width and y <= my <= y + height:
            self._menu_open = not self._menu_open
            self._info_overlay_visible = False
            return True
        return False

    def _handle_bottom_bar_click(self, mx: int, my: int) -> bool:
        x, y, width, height = self._bottom_bar_hitbox()
        if x <= mx <= x + width and y <= my <= y + height:
            self._info_overlay_visible = True
            self._menu_open = False
            return True
        return False

    def _handle_command_menu_click(self, mx: int, my: int) -> bool:
        if not self._menu_open:
            return False

        x, y, width, height = self._command_menu_bounds()
        if not (x <= mx <= x + width and y <= my <= y + height):
            self._menu_open = False
            return True

        for index, action in enumerate(self._quick_actions):
            item_y = y + 10 + index * 14
            if x + 4 <= mx <= x + width - 4 and item_y + 4 <= my <= item_y + 14:
                self._queue_action(action)
                self._menu_open = False
                return True

        return True

    def _handle_arrow_click(self, mx: int, my: int) -> bool:
        if len(self._local_connectors()) <= 2:
            return False

        area_y = self._top_bar_height
        area_height = pyxel.height - self._top_bar_height - self._bottom_bar_height
        center_y = area_y + area_height // 2
        size = 12

        if 0 <= mx <= size + 4 and center_y - size <= my <= center_y + size:
            self._advance_page(-1)
            return True

        if pyxel.width - size - 4 <= mx <= pyxel.width and center_y - size <= my <= center_y + size:
            self._advance_page(1)
            return True

        return False

    def _handle_connector_click(self, mx: int, my: int) -> None:
        for connector, x, y, width, height in self._layout_connectors():
            if x <= mx <= x + width and y <= my <= y + height:
                key = self._connector_key(connector)
                double_click = self._last_click_key == key and pyxel.frame_count - self._last_click_frame < 12

                if self._selected_connector_key == key and not double_click:
                    self._selected_connector_key = None
                else:
                    self._selected_connector_key = key

                self._last_click_key = key
                self._last_click_frame = pyxel.frame_count

                if double_click:
                    self._menu_open = True
                    self._info_overlay_visible = False
                return

    def _auto_reload(self) -> None:
        if pyxel.frame_count - self._last_reload_frame < 30:
            return
        self._last_reload_frame = pyxel.frame_count
        self._load_connectors()

    def _advance_loading(self) -> None:
        self._loading_progress = (self._loading_progress + 0.02) % 1.0

    def _queue_action(self, action: dict) -> None:
        token = uuid.uuid4().hex
        payload = {"action": action["action"], "token": token}
        try:
            ACTION_REQUEST_PATH.parent.mkdir(parents=True, exist_ok=True)
            ACTION_REQUEST_PATH.write_text(json.dumps(payload, indent=2))
        except OSError:
            self._action_feedback = "Failed to queue action."
            self._pending_action_token = None
            return

        self._pending_action_token = token
        self._action_feedback = f"{action['label']}..."

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
        self._pending_action_token = None

    def _suite_label(self) -> str:
        if self._suite_port:
            return f"Suite: {self._suite_host}:{self._suite_port}"
        if self._suite_host:
            return f"Suite: {self._suite_host}"
        return ""


def main() -> None:
    ConnectorViewport()


if __name__ == "__main__":
    main()
