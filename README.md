# Anova Precision Oven for Home Assistant

Home Assistant integration for the Anova Precision Oven (APO) using Anova's [official developer API](https://developer.anovaculinary.com/docs/devices/wifi/oven-commands). Real-time telemetry via WebSocket (`wss://devices.anovaculinary.io`), plus basic control.

> **Provided as-is. No warranty. No expectation of maintenance, support, or future updates.** This is a personal project shared in case it's useful to someone else. You are responsible for verifying correct behavior -- particularly when automating against a physical cooking appliance.

## Scope

Full read-only telemetry for the oven (22 sensors and binary sensors). Limited control: on/off, target temperature, and dry (oven) or wet (sous vide) mode. The lamp is read-only because Anova's API has no lamp command. Complex cooking (multi-stage recipes, steam, custom heating element combinations, timer, fan/vent) should be driven from the Anova app. See [Climate Entity Behavior](#climate-entity-behavior) before automating against the climate entity.

### Supported

- Anova Precision Oven v1 (tested)
- Anova Precision Oven v2 (untested; reads should work, the cook-start path sends extra fields the API docs suggest v2 requires)

### Not Supported

Anova Precision Cooker (APC sous vide circulator). That's a different product with a different API -- use the built-in [Anova integration](https://www.home-assistant.io/integrations/anova) in HA core.

## Entities

### Sensors (17, read-only)

| Entity | Description |
|---|---|
| Dry temperature | Current dry bulb temperature |
| Wet temperature | Current wet bulb temperature |
| Probe temperature | Meat probe temperature (when connected) |
| Humidity | Current relative humidity |
| Timer | Cook timer remaining (seconds) |
| Mode | `idle` / `preheat` / `cook` |
| Dry / Wet / Probe temperature setpoint | Target temperatures from whatever cook is running |
| Humidity setpoint | Target humidity percentage |
| Fan speed | Current fan speed percentage |
| Rack position | Current rack position |
| Timer start type | `Immediately` / `When preheated` / etc. |
| Heating elements | `Top` / `Bottom` / `Rear` / combinations / `All` / `Off` |
| Firmware / Hardware version | Diagnostic |
| Cook session | Current cook session ID or `idle` |

### Binary Sensors (5, read-only)

| Entity | Description |
|---|---|
| Connectivity | Online / offline |
| Door | Open / closed |
| Water tank | Empty (problem) + `descale_required` attribute |
| Vent | Open / closed |
| Lamp | On / off (cavity lamp) |

### Climate (1)

`OFF` and `HEAT` HVAC modes, target temperature 25-250 C (25-100 C in wet mode), and `dry` / `wet` presets. See [Climate Entity Behavior](#climate-entity-behavior).

## Climate Entity Behavior

The climate entity is intentionally simple.

**Oven idle:** `turn_on` starts a new single-stage cook with no timer, using the selected preset:

| Parameter | `dry` preset | `wet` preset (sous vide) |
|---|---|---|
| Bulb | Dry | Wet |
| Steam (relative humidity) | 0% | 100% |
| Heating elements | Top + Rear | Top + Rear |
| Fan speed | 100% | 100% |
| Vent | Closed | Closed |

**Oven cooking (v2 ovens):** changing the target temperature or preset resends the running cook from its active stage with only the bulb mode and setpoint changed. Steam, fan, heating elements, rack position and later stages are kept, and a running stage timer continues with the time it had left. Anova's personal-token API has no "adjust current cook" command, so the cook gets a new cook ID.

**Oven cooking (v1 ovens):** a temperature change replaces the running cook with the simple cook above.

`turn_off` sends `CMD_APO_STOP`.

## Installation

### HACS

1. In HACS, open the three-dot menu and select **Custom repositories**
2. Add `https://github.com/wrongdoug/ha-anova-precision-oven` as an **Integration**
3. Install "Anova Precision Oven" and restart HA

### Manual

Copy `custom_components/anova_precision_oven/` into your HA config's `custom_components/` directory and restart.

## Configuration

**Settings > Devices & Services > Add Integration > Anova Precision Oven**, then paste your Personal Access Token (starts with `anova-`).

Tokens are documented in Anova's [authentication guide](https://developer.anovaculinary.com/docs/devices/wifi/authentication). Max 10 PATs per account. WebSocket connections time out after 30 minutes of inactivity; the integration auto-reconnects.

## License

MIT. See `LICENSE`.
