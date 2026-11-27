# Device lifecycle and explicit cleanup

The integration preserves devices and entities when a sensor is temporarily
unavailable. This keeps existing entity IDs, names, disabled states and automation
references through an outage. Re-adoption can reuse that metadata when Protect
returns the same device ID and the Home Assistant device has not been deleted.
If Protect assigns a new ID, Home Assistant sees a new device.

| Protect state | Home Assistant behavior |
| --- | --- |
| Sensor is paired and online | Entities remain available while their individual data is fresh. Explicit device removal is refused. |
| Sensor is paired but offline | Registry metadata is retained and entities become unavailable. Explicit device removal is refused. |
| Protect cannot provide a valid inventory | Registry metadata is retained. A timeout, rejected login or incompatible response cannot authorize removal. |
| Sensor is no longer paired, confirmed by a new valid inventory | The user can explicitly remove its device from the Home Assistant device page. |
| An older version imported a known unsupported sensor | The user can explicitly delete the device from this integration without unpairing it in Protect. A fresh inventory must identify it as a recognized non-target model without conflicting model identifiers. |

To remove a sensor permanently:

1. Unpair it from the intended Protect console.
2. In Home Assistant, open **Settings → Devices & services → UniFi Protect Air
   Quality**, then the sensor's device page and its **Delete** action. Leave the
   integration entry enabled and loaded so it can perform the verification.
3. The integration checks a fresh HTTP inventory without applying WebSocket
   changes and verifies the console identity
   before allowing removal. If Protect cannot be reached, restore connectivity and
   retry. Disabling certificate verification is not a cleanup workaround.

Deleting a device is an explicit request to remove its registry entries. Back up
Home Assistant if you need to preserve custom names and other user settings before
this action. Ordinary offline periods do not require deletion.

If the sensor is adopted again, the integration discovers it and creates its
entities on all supported platforms. Its public unique-ID scheme stays unchanged;
Home Assistant manages any restoration of deleted registry metadata. Other
console entries are unaffected. Deleting a device is not a way to refresh a
temporarily unavailable sensor or to preserve its customizations.

## Availability during an outage

An entity requires a healthy coordinator, a device that is still in the current
inventory and reported connected, and verified device data less than 120 seconds
old. Measurement and alarm entities additionally check their own data ages.
Each alarm needs a recently confirmed status or event; numeric readings alone
do not confirm an alarm's end. Availability is re-evaluated every five seconds,
including when no stream messages arrive. A REST safety refresh is attempted
about every 60 seconds.

A brief WebSocket interruption does not immediately make fresh data unavailable.
Explicit device disconnection, removal, terminal authentication rejection or an
unsupported response can make entities unavailable before the freshness deadline.
A fresh but unresolved alarm may instead be **unknown**. Neither unknown nor
unavailable means that the alarm is cleared. Valid new data restores availability
automatically. See [diagnostic health](diagnostic-health.md) for the corresponding
freshness fields.

## Compatibility cleanup

During number-platform setup, compatibility cleanup removes only the exact known
`reading_interval` and `vape_low_threshold` number IDs belonging to devices of that
entry. An entity with a similar suffix, another platform, or another config entry
is preserved, including its entity ID and user settings.

The cleanup is performed when the number platform loads. It does not delete
ordinary disabled entities or change the identity of retained controls.

The removal hook follows Home Assistant's
[device-removal interface](https://contributors.home-assistant.io/core/integrations/device-registry/#let-users-remove-devices).
