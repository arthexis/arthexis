"""Signals for the RFID app."""

from django.dispatch import Signal

# Fired when an RFID tag is detected by the always-on watcher.
tag_scanned = Signal()
