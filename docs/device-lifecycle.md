# Device lifecycle and explicit cleanup

The integration preserves devices and entities when a sensor is temporarily
unavailable. This keeps existing entity IDs, names, disabled states and automation
references through an outage or ordinary re-adoption.

| Protect state | Home Assistant behavior |
| --- | --- |
| Sensor is paired and online | Entities remain available while their individual data is fresh. Explicit device removal is refused. |
| Sensor is paired but offline | Registry metadata is retained and entities become unavailable. Explicit device removal is refused. |
| Protect cannot provide a valid inventory | Registry metadata is retained. A timeout, rejected login or incompatible response cannot authorize removal. |
| Sensor is no longer paired, confirmed by a new valid inventory | The user can explicitly remove its device from the Home Assistant device page. |

To remove a sensor permanently:

1. Unpair it from the intended Protect console.
2. In Home Assistant, open **Settings → Devices & services → UniFi Protect Air
   Quality**, then the sensor's device page and its **Delete** action.
3. The integration fetches a new inventory and verifies the console identity
   before allowing removal. If Protect cannot be reached, restore connectivity and
   retry. Disabling certificate verification is not a cleanup workaround.

Deleting a device is an explicit request to remove its registry entries. Back up
Home Assistant if you need to preserve custom names and other user settings before
this action. Ordinary offline periods do not require deletion.

If the sensor is adopted again, the integration discovers it and creates its
entities on all supported platforms. Its public unique-ID scheme stays unchanged;
Home Assistant manages any restoration of deleted registry metadata. Other
console entries are unaffected.

During number-platform setup, compatibility cleanup removes only the exact known
`reading_interval` and `vape_low_threshold` number IDs belonging to devices of that
entry. An entity with a similar suffix, another platform, or another config entry
is preserved, including its entity ID and user settings.

The removal hook follows Home Assistant's
[device-removal interface](https://contributors.home-assistant.io/core/integrations/device-registry/#let-users-remove-devices).
