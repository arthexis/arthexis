from __future__ import annotations

from celery import shared_task


@shared_task(name="apps.cards.tasks.process_rfid_watchlist_event")
def process_rfid_watchlist_event(event_id: int) -> str:
    from apps.cards.watchlists import process_watchlist_event

    return process_watchlist_event(int(event_id))
