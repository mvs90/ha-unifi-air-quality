# UniFi Protect Air Quality for Home Assistant

An experimental, local-push Home Assistant custom integration for the UniFi
Protect **UP-AirQuality** sensor. It communicates directly with the console;
MQTT, a cloud account, and an additional bridge are not required.

> [!WARNING]
> This alpha uses Protect's undocumented private bootstrap and WebSocket APIs
> because the Public Integration API does not expose continuous air-quality
> readings yet. A dedicated API boundary keeps a later migration isolated.

## Current milestone: safe discovery

Version `0.1.0` can:

- configure a local Protect console through the Home Assistant UI;
- authenticate with a dedicated local Protect user;
- find adopted UP-AirQuality devices in the private bootstrap response;
- follow private WebSocket updates with a 15-minute bootstrap safety refresh;
- register the sensors as Home Assistant devices; and
- export field-preserving, anonymized raw sensor data through Home Assistant
  diagnostics.

It intentionally does **not** create measurement entities yet. Real,
anonymized diagnostics will be used to verify the wire field names, units, and
update shapes before CO2, AQI, VOC/TVOC, particulate matter, temperature,
humidity, and vape-index entities are added.

## Installation

### HACS custom repository

1. In HACS, open **Integrations** and add
   `https://github.com/mvs90/ha-unifi-air-quality` as a custom repository of
   type **Integration**.
2. Install **UniFi Protect Air Quality** and restart Home Assistant.
3. Go to **Settings → Devices & services → Add integration** and search for
   **UniFi Protect Air Quality**.

For manual installation, copy `custom_components/unifi_air_quality` into the
same directory under your Home Assistant configuration directory and restart.

## Protect account

Create a dedicated **local** UniFi OS user with Protect read access. Do not use
an owner account, Ubiquiti cloud SSO, or expose Protect to the internet. The
password is stored by Home Assistant in the config entry and is never included
in integration logs or diagnostics.

Most consoles use a self-signed certificate. Leave certificate verification
off for those systems; enable it when the console has a certificate trusted by
the Home Assistant host.

## Collecting the first diagnostic fixture

After setup, open the integration's menu and select **Download diagnostics**.
The export removes credentials, network locations, names, MAC addresses,
serials, and stable identifiers. String values that are not explicitly known
to be harmless are also redacted, while field names, numbers, booleans, and
nested structure remain available for protocol development.

Review the JSON yourself before sharing it. Open a GitHub issue and attach only
the reviewed diagnostic file.

## Development

Requirements: Docker and Python 3.13 or newer.

```bash
python3 -m venv .venv
.venv/bin/pip install -e '.[test]'
.venv/bin/pytest
docker compose -f dev/compose.yaml up -d
```

The Docker configuration bind-mounts this checkout's `custom_components`
directory, so a container restart picks up code changes:

```bash
docker compose -f dev/compose.yaml restart homeassistant
docker compose -f dev/compose.yaml logs -f homeassistant
```

Home Assistant is available at <http://localhost:8123>. The image is pinned to
`2026.8.1`; update both `dev/compose.yaml` and the test dependency together.

## Removal

Remove the config entry under **Settings → Devices & services**, then uninstall
the repository in HACS and restart Home Assistant.

## Known limitations

- The private API can change with any Protect update.
- Username/password authentication is required until the public API exposes
  continuous readings and device-change notifications.
- Only diagnostics and device discovery are present in the first milestone.
- Automatic console discovery is not implemented.
- The alpha is installable as a HACS custom repository. Submission to HACS's
  default catalog additionally requires a brand entry in the central Home
  Assistant brands repository and a stable GitHub release.

This project is not affiliated with Ubiquiti Inc. UniFi and UniFi Protect are
trademarks of their respective owner.
