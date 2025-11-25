# EVCS Connectivity & Maintenance Cookbook

This cookbook walks through the end-to-end workflow for onboarding an electric vehicle charging station (EVCS) to Arthexis Constellation and keeping it healthy afterwards. Use it as a runbook any time a new charger must connect to the central system or an installed unit needs corrective maintenance.

- [1. Know the onboarding sequence](#1-know-the-onboarding-sequence)
- [2. Pre-flight connectivity checklist](#2-pre-flight-connectivity-checklist)
- [3. Configure the CSMS](#3-configure-the-csms)
- [4. Point the EVCS at Arthexis](#4-point-the-evcs-at-arthexis)
- [5. Verify the first connection](#5-verify-the-first-connection)
- [6. Routine maintenance tasks](#6-routine-maintenance-tasks)
- [7. Emergency recovery playbook](#7-emergency-recovery-playbook)

---

## 1. Know the onboarding sequence

Every EVCS onboarding follows the same high-level milestones. Knowing the order prevents missed steps:

1. Collect charger metadata and network requirements from the site survey.
2. Prepare firewall, DNS, and TLS so the EVCS can resolve and reach the central system via `wss://`.
3. Create charger, connector, and authorization records inside Arthexis so the CSMS recognises the device.
4. Trigger the first connection and confirm the CSMS establishes a WebSocket session.
5. Run smoke tests—status poll, remote start/stop, and RFID sync—to verify the station accepts commands.
6. Schedule the recurring maintenance tasks described in this guide so the charger stays compliant.

## 2. Pre-flight connectivity checklist

Complete the following tasks before touching the CSMS configuration:

- **Inventory the hardware**
  - Manufacturer, model, and firmware build currently installed.
  - Connector count, numbering scheme, and power ratings per connector.
  - RFID or token ranges the site expects to accept on day one.
- **Network and security prerequisites**
  - Allocate an outbound firewall rule that allows TCP 443 (or the HTTPS listener configured for the node role) from the EVCS LAN to the CSMS hostname.
  - Validate that DNS or local host overrides resolve the CSMS hostname to the correct IP address.
  - Export the CSMS TLS certificate chain and confirm the EVCS trusts the issuing authority.
- **OCPP credentials**
  - Decide the `chargePointId` or serial the EVCS will present. The path portion of the WebSocket URL should match this ID.
  - If the charger needs HTTP basic authentication, record the username and password; store them as a Sigil so they can be rotated centrally.
- **Site readiness**
  - Ensure physical installation is complete and the EVCS is powered.
  - Confirm that any upstream metering or utility approvals are closed so test sessions can run uninterrupted.

## 3. Configure the CSMS

With prerequisites resolved, prepare Arthexis so it recognises the charger immediately when it calls home.

1. **Create or update the location** – In **Admin → Core → Locations**, create the site that will host the charger if it does not already exist. Attach address, timezone, and operator contact details.
2. **Register the charger** – Open **Admin → OCPP → Charge Points** and add the EVCS:
   - Set the `Charger ID` to the identifier collected in the pre-flight checklist.
   - Assign the location and, when available, provide a friendly display name.
   - Create connectors that mirror the physical numbering and power characteristics.
3. **Provision authorisation data** – In **Admin → Core → RFID cards** (or the relevant account model), preload the tokens that should authenticate on day one. Use **Admin → OCPP → IdToken Whitelist** when you rely on local lists.
4. **Capture configuration baselines** – If a template exists, link the charger to an **Admin → OCPP → Charger configurations** record. This lets you push approved settings immediately after the first connection.
5. **Prepare firmware packages** – Upload the vendor firmware image under **Admin → OCPP → Charger firmware** so it is ready if the EVCS needs an upgrade.
6. **Document credentials** – Store any HTTP basic auth or VPN parameters as Sigils via **Admin → Core → Sigils**. Reference them from deployment scripts to avoid hardcoding secrets.

## 4. Point the EVCS at Arthexis

Configure the charger using its local interface (vendor portal, front panel, or technician laptop):

1. Enter the WebSocket endpoint using HTTPS: `wss://<csms-hostname>/ws/ocpp/<CHARGER_ID>`.
2. Supply HTTP basic authentication credentials when required. Cross-check that the Sigil-backed values match the charger entry.
3. Set the OCPP protocol version to **1.6 JSON**. Disable mandatory subprotocol negotiation if the EVCS allows it; Arthexis accepts either mode.
4. Apply the trusted certificate chain by importing the PEM bundle you validated earlier.
5. Save and reboot the charger if the vendor UI requires a restart for new network settings.

## 5. Verify the first connection

Once the charger points to the CSMS, confirm the onboarding succeeded:

1. **Watch the charger log** – Tail `logs/charger.<CHARGER_ID>.log` and look for `Connected` and subsequent OCPP messages when the EVCS boots.
2. **Check admin status** – In **Admin → OCPP → Charge Points**, confirm the charger shows as `Online` and the last heartbeat timestamp is current.
3. **Validate authorisation** – Swipe a whitelisted RFID or trigger a remote start from the charger record. The session should move into `Charging` with meter values arriving.
4. **Send a configuration request** – From the charger admin action menu, run **Fetch configuration from EVCS** to ensure round-trip commands succeed.
5. **Record acceptance** – Update the location or charger notes with the commissioning date, firmware build, and any deviations from standard settings.

## 6. Routine maintenance tasks

After onboarding, schedule the following recurring activities to keep the EVCS fleet healthy:

### 6.1 Monitor connectivity and utilisation
- Review the charger change list weekly to catch offline units early.
- Subscribe to alerts produced by Celery tasks (for example, missed heartbeats) so incidents surface automatically.
- Export session summaries from **Admin → Core → Sessions** for billing and load analysis.

### 6.2 Manage firmware lifecycle
- Store vendor firmware revisions under **Admin → OCPP → Charger firmware**.
- Use the **Upload EVCS firmware** action to stage upgrades. Choose target chargers, set the retrieve window, and monitor **Admin → OCPP → Firmware deployments** for status.
- For diagnostics, use **Admin → Nodes → Node firmware** → **Download EVCS firmware** to capture the current image from field devices when supported.

### 6.3 Maintain configuration baselines
- Update **Charger configuration** records when vendors release new keys or defaults.
- Run **Push configuration to EVCS** after editing values to apply the template. Follow up with **Restart chargers** if the UI reports a restart requirement.
- Use **Fetch configuration from EVCS** periodically to detect drift between expected and actual device settings.

### 6.4 Keep RFID and reservation data fresh
- Synchronise whitelists through the **Send Local RFIDs to CP** action whenever new cards are approved.
- Pull local updates from the field with **Update RFIDs from EVCS** if the site allows local enrolment.
- Manage scheduled access via **Admin → OCPP → Reservations**. Cancel unused reservations and review the EVCS confirmation status to ensure the device acknowledged each booking.

### 6.5 Operate remote control workflows
- Issue **Remote start/stop**, **Unlock connector**, or **Change availability** commands directly from the charger detail page when responding to support calls.
- Trigger **Reset** actions (soft or hard) to clear faults without dispatching a technician.
- Use **Trigger message → StatusNotification** to force a status refresh after performing physical maintenance.

## 7. Emergency recovery playbook

When an EVCS stops communicating or reports persistent faults:

1. **Confirm network path** – Repeat the DNS, firewall, and TLS checks from the pre-flight section. Use packet captures if the TLS handshake fails.
2. **Inspect charger logs** – Review `logs/charger.<CHARGER_ID>.log` for errors, timeouts, or rejected commands around the incident window.
3. **Replay maintenance actions** – Re-push configurations and firmware to rule out corrupted state on the charger.
4. **Leverage DataTransfer** – Use **Admin → OCPP → Data transfers** to send vendor-specific diagnostics commands and collect the reply payload for engineering.
5. **Escalate** – If remote steps fail, record findings in the charger notes and open a ticket with the hardware vendor. Include the captured logs and timestamps to speed resolution.

Keeping this cookbook updated with vendor quirks and site-specific notes makes future onboarding cycles faster and reduces the mean time to repair when a field unit acts up.
