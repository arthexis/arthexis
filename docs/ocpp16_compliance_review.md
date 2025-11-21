# OCPP 1.6 Compliance Review

## Summary
This document reviews the current Open Charge Point Protocol (OCPP) 1.6 feature coverage based on the project's declared specification set and tracked implementation coverage.

## Coverage snapshot
- **Overall coverage:** 24 of 28 operations supported (**85.71%**).
- **CSMS to CP:** 15 of 19 operations supported (**78.95%**).
- **CP to CSMS:** 10 of 10 operations supported (**100%**).

Source: `ocpp/coverage.json`.

## Implemented operations
### Charge point to CSMS (CP → CSMS)
Supported operations:
- Authorize
- BootNotification
- DataTransfer
- DiagnosticsStatusNotification
- FirmwareStatusNotification
- Heartbeat
- MeterValues
- StartTransaction
- StatusNotification
- StopTransaction

The implementation list also includes `LogStatusNotification` and `SecurityEventNotification`, which are optional security extensions beyond the core 1.6 profile.

### CSMS to charge point (CSMS → CP)
Supported operations:
- CancelReservation
- ChangeAvailability
- ChangeConfiguration
- ClearCache
- DataTransfer
- GetConfiguration
- GetLocalListVersion
- RemoteStartTransaction
- RemoteStopTransaction
- ReserveNow
- Reset
- SendLocalList
- TriggerMessage
- UnlockConnector
- UpdateFirmware

## Gaps against OCPP 1.6 spec
The OCPP 1.6 call matrix defines additional CSMS → CP operations that are not yet implemented:
- ClearChargingProfile
- GetCompositeSchedule
- GetDiagnostics
- SetChargingProfile

Adding these messages would close the remaining compliance delta for remote configuration and scheduling controls.

## Recommendations
1. **Prioritize remote charging profile support:** Implement `SetChargingProfile` and `ClearChargingProfile` handlers and associated database/application plumbing to support Smart Charging features.
2. **Add scheduling visibility:** Support `GetCompositeSchedule` so CSMS can query station-side aggregate schedules.
3. **Enable diagnostics retrieval:** Implement `GetDiagnostics` to allow CSMS-initiated log/diagnostic package collection.
4. **Update coverage reporting:** Refresh `ocpp/coverage.json` after new handlers and tests are added to confirm the compliance gap closure.
