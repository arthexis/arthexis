from __future__ import annotations

from enum import Enum


class RegistrationStatus(str, Enum):
    ACCEPTED = "Accepted"
    PENDING = "Pending"
    REJECTED = "Rejected"


class AuthorizationStatus(str, Enum):
    ACCEPTED = "Accepted"
    BLOCKED = "Blocked"
    EXPIRED = "Expired"
    INVALID = "Invalid"
    CONCURRENT_TX = "ConcurrentTx"


class GenericStatus(str, Enum):
    ACCEPTED = "Accepted"
    REJECTED = "Rejected"
    UNKNOWN = "Unknown"
    NOT_SUPPORTED = "NotSupported"
    SCHEDULED = "Scheduled"
    UNLOCKED = "Unlocked"
