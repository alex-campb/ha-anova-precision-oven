## Anova Ovens

Home Assistant integration for the Anova Precision Oven (APO) using Anova's [official developer API](https://developer.anovaculinary.com/docs/devices/wifi/oven-commands). Real-time telemetry via WebSocket plus basic control (on/off, target temperature, wet/dry mode, lamp) and the v2 oven camera. Based on wrongdoug/ha-anova-precision-oven v1.3.0, under its own domain (`anova_ovens`).

> **Provided as-is. No warranty. No expectation of maintenance, support, or future updates.**

**Supported:** Anova Precision Oven v1 and v2.
**Not supported:** Anova Precision Cooker (APC sous vide) -- use the built-in `anova` integration in HA core.

### Entities

- 17 sensors: temperatures, humidity, timer, mode, setpoints, fan speed, heating elements, probe, cook session, firmware
- 4 binary sensors: connectivity, door, water tank, vent
- 1 climate: simple single-stage dry cook start/stop (see README for caveats)
- 1 switch: lamp
- 1 camera (v2 ovens, optional: needs the Anova app login and the Anova Camera Relay app)

### Requirements

Anova Personal Access Token (starts with `anova-`) from the Anova developer portal.
