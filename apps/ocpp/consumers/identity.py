from .. import store


def _extract_vehicle_identifier(payload: dict) -> tuple[str, str]:
    """Return normalized VID and VIN values from an OCPP message payload."""

    raw_vid = payload.get("vid")
    vid_value = str(raw_vid).strip() if raw_vid is not None else ""
    raw_vin = payload.get("vin")
    vin_value = str(raw_vin).strip() if raw_vin is not None else ""
    if not vid_value and vin_value:
        vid_value = vin_value
    return vid_value, vin_value


def _register_log_names_for_identity(
    charger_id: str, connector_id: int | str | None, display_name: str
) -> None:
    """Register friendly log names for a charger identity and its pending key."""

    if not charger_id:
        return
    friendly_name = display_name or charger_id
    store.register_log_name(
        store.identity_key(charger_id, connector_id),
        friendly_name,
        log_type="charger",
    )
    if connector_id is None:
        store.register_log_name(
            store.pending_key(charger_id), friendly_name, log_type="charger"
        )
        store.register_log_name(charger_id, friendly_name, log_type="charger")
