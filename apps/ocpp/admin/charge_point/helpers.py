from apps.ocpp import store
from apps.ocpp.models import Charger
from apps.ocpp.views import _charger_state, _live_sessions


def charger_status_state(charger: Charger) -> str:
    tx_obj = store.get_transaction(charger.charger_id, charger.connector_id)
    state, _ = _charger_state(
        charger,
        tx_obj if charger.connector_id is not None else (_live_sessions(charger) or None),
    )
    return state
