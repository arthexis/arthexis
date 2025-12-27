# OCPP 1.6 Compliance Review

## Summary
The current Open Charge Point Protocol (OCPP) 1.6 feature set is fully implemented across the project’s defined scope.

## Coverage snapshot
- **Overall coverage:** 28 of 28 operations supported (**100%**).
- **CSMS to CP:** 19 of 19 operations supported (**100%**).
- **CP to CSMS:** 10 of 10 operations supported (**100%**).

Source: `apps/ocpp/coverage.json`.

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

The project also implements optional security and monitoring extensions such as `LogStatusNotification` and `SecurityEventNotification`, along with additional monitoring and reporting messages listed in `apps/ocpp/coverage.json`.

### CSMS to charge point (CSMS → CP)
Supported operations:
- CancelReservation
- ChangeAvailability
- ChangeConfiguration
- ClearCache
- ClearChargingProfile
- DataTransfer
- GetCompositeSchedule
- GetConfiguration
- GetDiagnostics
- GetLocalListVersion
- RemoteStartTransaction
- RemoteStopTransaction
- ReserveNow
- Reset
- SendLocalList
- SetChargingProfile
- TriggerMessage
- UnlockConnector
- UpdateFirmware

Charging profile management (`SetChargingProfile`, `ClearChargingProfile`) and schedule visibility (`GetCompositeSchedule`) are now fully supported, closing the previous compliance gap for smart charging.

## Notes
The repository includes many optional OCPP 1.6 security and monitoring messages beyond the baseline specification. Refer to `apps/ocpp/coverage.json` for the full inventory of implemented operations.
