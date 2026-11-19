# Documentation

Start with the [installation and configuration guide](../README.md) to use
UniFi Protect Air Quality in Home Assistant. It covers Marketplace, HACS and
manual installation, the local Protect account, sensor settings and common
problems.

## Using the integration

| Guide | What it explains |
| --- | --- |
| [Install, migrate and roll back](install-upgrade-rollback.md) | Identify an exact installation package, preserve existing entries and entity IDs during updates, and recover from a backup. |
| [Device lifecycle](device-lifecycle.md) | Discover newly adopted sensors, handle offline or removed devices, and remove or re-adopt a device deliberately. |
| [Diagnostic health](diagnostic-health.md) | Read transport, device, measurement and alarm freshness without mistaking a connected stream for current data. |
| [Alarm pictures and dashboard cards](../README.md#alarm-pictures-on-dashboards) | Show the integration's green OK and red alarm pictures on editable Tile cards. |
| [Changelog](../CHANGELOG.md) | Review changes between integration versions. |
| [Security policy](../SECURITY.md) | Report a security issue and understand credential, diagnostic and dependency handling. |

## Contributing and maintenance

| Guide | What it explains |
| --- | --- |
| [Contributing](../CONTRIBUTING.md) | Navigate the source, prepare a development environment, run the existing checks and document a contribution. |
| [Main-branch ruleset](maintainer-ruleset.md) | Apply and check the review and required-check rules used for changes to this repository. |
| [Integration artwork](../assets/branding/README.md) | Locate the source artwork and the local Home Assistant brand images. |
| [Historical HACS readiness report](hacs-readiness.md) | Read the earlier upstream submission and compatibility record; it is not the current installation guide. |

Each Python module, class and function carries a description next to its
implementation. The [source map](../CONTRIBUTING.md) identifies the modules
responsible for setup, transport, device state, entities and diagnostics.
