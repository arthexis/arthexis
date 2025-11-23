from __future__ import annotations

import json
import math
import uuid
from pathlib import Path

import pyxel


class ContainerWindow:
    def __init__(self, title: str, x: int, y: int, width: int, height: int, connectors: list[dict]) -> None:
        self.title = title
        self.x = x
        self.y = y
        self.width = width
        self.height = height
        self.connectors = connectors
        self._drag_offset_x = 0
        self._drag_offset_y = 0

    @property
    def header_height(self) -> int:
        return 16

    def hit_header(self, mx: int, my: int) -> bool:
        return self.x <= mx <= self.x + self.width and self.y <= my <= self.y + self.header_height

    def set_drag_offset(self, mx: int, my: int) -> None:
        self._drag_offset_x = mx - self.x
        self._drag_offset_y = my - self.y

    def snapped_position(self, mx: int, my: int, grid_size: int, viewport_width: int, viewport_height: int) -> tuple[int, int]:
        snapped_x = max(0, min(viewport_width - self.width, round((mx - self._drag_offset_x) / grid_size) * grid_size))
        snapped_y = max(0, min(viewport_height - self.height, round((my - self._drag_offset_y) / grid_size) * grid_size))
        return snapped_x, snapped_y

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

ACTIVE_DRAG_ACCENT = 11
LAST_ACTIVE_ACCENT = 3


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
        self._containers: list[ContainerWindow] = []
        self.instance_running = False
        self.waiting_for_instance = False
        self._has_loaded_snapshot = False
        self._last_mtime: float | None = None
        self._last_reload_frame = 0
        self._loading_progress = 0
        self._menu_open = False
        self._pending_action_token: str | None = None
        self._action_feedback = ""
        self._action_feedback_frame = 0
        self._suite_host = "127.0.0.1"
        self._suite_port: int | None = None
        self._grid_size = 8
        self._dragging_container: ContainerWindow | None = None
        self._last_active_container: ContainerWindow | None = None
        self._suite_overlay_dragging = False
        self._suite_overlay_x = 6
        self._suite_overlay_y = 0
        self._suite_overlay_offset_x = 0
        self._suite_overlay_offset_y = 0
        self._quick_actions = (
            {"label": "Start Default Simulator", "action": "start_default_simulator"},
        )

        pyxel.init(480, 320, title="Connector Viewport", fps=30)
        pyxel.mouse(True)
        self._suite_overlay_y = pyxel.height - 16
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

        self._rebuild_containers()

    def _rebuild_containers(self) -> None:
        existing_positions = {container.title: (container.x, container.y) for container in self._containers}

        grouped: dict[str, list[dict]] = {}
        for connector in self.connectors:
            key = str(connector.get("location") or "Connectors")
            grouped.setdefault(key, []).append(connector)

        self._containers = []
        default_width = 220
        for index, (title, items) in enumerate(sorted(grouped.items())):
            columns = max(1, (default_width - 16) // 130)
            rows = max(1, math.ceil(len(items) / columns))
            body_height = rows * (82 + 6) + 12
            height = 20 + body_height

            existing_position = existing_positions.get(title)
            if existing_position:
                x, y = existing_position
            else:
                x = 8 + (index % 2) * (default_width + 12)
                y = 8 + (index // 2) * (height + 12)

            x = max(0, min(pyxel.width - default_width, x))
            y = max(0, min(pyxel.height - height, y))

            self._containers.append(ContainerWindow(title, x, y, default_width, height, items))

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

        if self.waiting_for_instance and not self.instance_running:
            self._draw_loading()
            self._draw_menu()
            self._draw_suite_hint()
            self._draw_feedback()
            return

        if not self._has_loaded_snapshot or (not self.instance_running and not self.connectors):
            self._draw_loading()
            self._draw_menu()
            self._draw_suite_hint()
            self._draw_feedback()
            return

        padding = 10

        if not self._containers:
            pyxel.text(padding, padding, "No connectors available. Press R to reload.", 7)
            self._draw_menu()
            self._draw_feedback()
            return

        if not self.instance_running:
            pyxel.text(padding, padding - 6, "Instance is starting...", 6)

        for container in self._containers:
            self._draw_container(container)

        self._draw_menu()
        self._draw_suite_hint()
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

    def _draw_container(self, container: ContainerWindow) -> None:
        accent = 5
        if self._dragging_container is container:
            accent = ACTIVE_DRAG_ACCENT
        elif self._last_active_container is container:
            accent = LAST_ACTIVE_ACCENT
        elif container.connectors:
            accent = _nearest_palette_index(container.connectors[0].get("status_color"))

        pyxel.rect(container.x, container.y, container.width, container.height, 0)
        pyxel.rectb(container.x, container.y, container.width, container.height, accent)

        header_color = 1
        pyxel.rect(container.x, container.y, container.width, container.header_height, header_color)
        pyxel.rectb(container.x, container.y, container.width, container.header_height, accent)
        pyxel.text(container.x + 6, container.y + 5, container.title[:26], 7)

        content_x = container.x + 4
        content_y = container.y + container.header_height + 4
        content_width = container.width - 8
        content_height = container.height - container.header_height - 8

        for grid_y in range(content_y, content_y + content_height, self._grid_size * 2):
            pyxel.rect(content_x, grid_y, content_width, 1, 1)

        for grid_x in range(content_x, content_x + content_width, self._grid_size * 2):
            pyxel.rect(grid_x, content_y, 1, content_height, 1)

        self._draw_container_connectors(container, content_x, content_y, content_width, content_height)

    def _draw_container_connectors(
        self, container: ContainerWindow, content_x: int, content_y: int, content_width: int, content_height: int
    ) -> None:
        padding = 6
        card_width = 120
        card_height = 82
        columns = max(1, (content_width - padding) // (card_width + padding))
        rows = max(1, math.ceil(max(1, len(container.connectors)) / columns))
        total_height = rows * (card_height + padding)

        start_y = content_y + max(0, (content_height - total_height) // 2)

        if not container.connectors:
            empty_text = "Drop connectors here" if self.instance_running else "No connectors"
            pyxel.text(content_x + padding, start_y + padding, empty_text[:28], 7)
            return

        for idx, connector in enumerate(container.connectors):
            col = idx % columns
            row = idx // columns
            x = content_x + padding + col * (card_width + padding)
            y = start_y + row * (card_height + padding)
            self._draw_card(connector, x, y, card_width, card_height)

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

        pyxel.text(10, 10, "Loading...", 7)
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
        mx, my = pyxel.mouse_x, pyxel.mouse_y

        if pyxel.btnp(pyxel.MOUSE_BUTTON_LEFT):
            if self._handle_menu_click(mx, my):
                return
            if self._start_suite_overlay_drag(mx, my):
                return
            self._start_container_drag(mx, my)

        if self._suite_overlay_dragging:
            if pyxel.btn(pyxel.MOUSE_BUTTON_LEFT):
                self._update_suite_overlay_drag(mx, my)
            else:
                self._suite_overlay_dragging = False
            return

        if self._dragging_container and pyxel.btn(pyxel.MOUSE_BUTTON_LEFT):
            self._update_container_drag(mx, my)
        elif self._dragging_container and not pyxel.btn(pyxel.MOUSE_BUTTON_LEFT):
            self._last_active_container = self._dragging_container
            self._dragging_container = None

    def _handle_menu_click(self, mx: int, my: int) -> bool:
        btn_x, btn_y, btn_w, btn_h = self._menu_hitbox()
        if btn_x <= mx <= btn_x + btn_w and btn_y <= my <= btn_y + btn_h:
            self._menu_open = not self._menu_open
            return True

        if not self._menu_open:
            return False

        menu_width = 150
        menu_x = btn_x - menu_width + btn_w
        menu_y = btn_y + btn_h + 4
        menu_height = 14 + 14 * len(self._quick_actions)
        for index, action in enumerate(self._quick_actions):
            item_y = menu_y + 8 + index * 14
            item_height = 10
            if menu_x + 4 <= mx <= menu_x + menu_width - 4 and item_y + 4 <= my <= item_y + 4 + item_height:
                self._queue_action(action)
                self._menu_open = False
                return True

        return menu_x <= mx <= menu_x + menu_width and menu_y <= my <= menu_y + menu_height

    def _start_container_drag(self, mx: int, my: int) -> None:
        for container in reversed(self._containers):
            if container.hit_header(mx, my):
                container.set_drag_offset(mx, my)
                self._dragging_container = container
                self._containers.remove(container)
                self._containers.append(container)
                self._update_container_drag(mx, my)
                return

    def _update_container_drag(self, mx: int, my: int) -> None:
        if not self._dragging_container:
            return

        new_x, new_y = self._dragging_container.snapped_position(mx, my, self._grid_size, pyxel.width, pyxel.height)
        self._dragging_container.x = new_x
        self._dragging_container.y = new_y

    def _start_suite_overlay_drag(self, mx: int, my: int) -> bool:
        label = self._suite_label()
        if not label:
            return False

        overlay_x, overlay_y, overlay_w, overlay_h = self._suite_overlay_bounds(label)
        if overlay_x <= mx <= overlay_x + overlay_w and overlay_y <= my <= overlay_y + overlay_h:
            self._suite_overlay_dragging = True
            self._suite_overlay_offset_x = mx - overlay_x
            self._suite_overlay_offset_y = my - overlay_y
            return True

        return False

    def _update_suite_overlay_drag(self, mx: int, my: int) -> None:
        label = self._suite_label()
        overlay_x, overlay_y, overlay_w, overlay_h = self._suite_overlay_bounds(label)
        self._suite_overlay_x = max(0, min(pyxel.width - overlay_w, mx - self._suite_overlay_offset_x))
        self._suite_overlay_y = max(0, min(pyxel.height - overlay_h, my - self._suite_overlay_offset_y))

    def _suite_overlay_bounds(self, label: str) -> tuple[int, int, int, int]:
        padding = 4
        text_width = len(label) * 4
        box_width = text_width + padding * 2
        box_height = 12
        x = max(0, min(pyxel.width - box_width, self._suite_overlay_x))
        y = max(0, min(pyxel.height - box_height, self._suite_overlay_y))
        self._suite_overlay_x = x
        self._suite_overlay_y = y
        return x, y, box_width, box_height

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

    def _suite_label(self) -> str:
        if self._suite_port:
            return f"Suite: {self._suite_host}:{self._suite_port}"
        if self._suite_host:
            return f"Suite: {self._suite_host}"
        return ""

    def _draw_suite_hint(self) -> None:
        label = self._suite_label()
        if not label:
            return

        x, y, box_width, box_height = self._suite_overlay_bounds(label)

        padding = 4
        pyxel.rect(x - 1, y - 1, box_width + 2, box_height + 2, 0)
        pyxel.rect(x, y, box_width, box_height, 1)
        pyxel.rectb(x, y, box_width, box_height, 5)
        pyxel.text(x + padding - 1, y + 3, label, 7)


def main() -> None:
    ConnectorViewport()


if __name__ == "__main__":
    main()
