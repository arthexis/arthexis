"""Composition layer for base OCPP consumers.

Public extension points:
    * ``SinkConsumer`` for sink/testing websocket endpoints.
    * ``CSMSConsumer`` for the primary OCPP 1.6/2.x CSMS websocket consumer.
"""

from .csms import CSMSConsumer, SinkConsumer

__all__ = ["SinkConsumer", "CSMSConsumer"]
