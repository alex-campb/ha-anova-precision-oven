"""Climate platform for Anova Precision Oven."""
from __future__ import annotations

import logging
from typing import Any
import uuid

from homeassistant.components.climate import (
    ClimateEntity,
    ClimateEntityFeature,
    HVACAction,
    HVACMode,
)
from homeassistant.const import ATTR_TEMPERATURE, UnitOfTemperature
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    CMD_APO_START,
    CMD_APO_STOP,
    DEFAULT_FAN_SPEED,
    DEFAULT_HUMIDITY,
    DEFAULT_TEMPERATURE_CELSIUS,
    OVEN_TYPE_V1,
    OVEN_TYPE_V2,
    TEMP_MAX_CELSIUS,
    TEMP_MIN_CELSIUS,
)
from .coordinator import AnovaOvenConfigEntry, AnovaOvenCoordinator
from .entity import AnovaOvenEntity

_LOGGER = logging.getLogger(__name__)

HVAC_MODE_MAPPING = {
    "idle": HVACMode.OFF,
    "cook": HVACMode.HEAT,
    "preheat": HVACMode.HEAT,
}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: AnovaOvenConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Anova Oven climate based on a config entry."""
    coordinator = entry.runtime_data.coordinator

    known_devices: set[str] = set()

    def create_entities_for_device(device_id: str) -> None:
        if device_id in known_devices:
            return

        known_devices.add(device_id)
        async_add_entities([AnovaOvenClimate(coordinator, device_id)])
        _LOGGER.debug("Created climate entity for device %s", device_id)

    for device_id in coordinator.devices:
        create_entities_for_device(device_id)

    @callback
    def handle_coordinator_update() -> None:
        for device_id in coordinator.devices:
            create_entities_for_device(device_id)

    entry.async_on_unload(coordinator.async_add_listener(handle_coordinator_update))


class AnovaOvenClimate(AnovaOvenEntity, ClimateEntity):
    """Anova Oven climate control."""

    _attr_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_supported_features = (
        ClimateEntityFeature.TARGET_TEMPERATURE
        | ClimateEntityFeature.TURN_ON
        | ClimateEntityFeature.TURN_OFF
    )
    _attr_hvac_modes = [HVACMode.OFF, HVACMode.HEAT]
    _attr_min_temp = TEMP_MIN_CELSIUS
    _attr_max_temp = TEMP_MAX_CELSIUS
    _attr_target_temperature_step = 1

    def __init__(
        self,
        coordinator: AnovaOvenCoordinator,
        device_id: str,
    ) -> None:
        """Initialize the climate entity."""
        super().__init__(coordinator, device_id)
        self._attr_unique_id = f"{device_id}_climate"
        self._attr_name = None

    @property
    def hvac_mode(self) -> HVACMode:
        """Return current HVAC mode."""
        state = self.coordinator.get_device_state(self._device_id)
        if not state:
            return HVACMode.OFF

        mode = state.get("state", {}).get("mode", "idle")
        return HVAC_MODE_MAPPING.get(mode, HVACMode.OFF)

    @property
    def hvac_action(self) -> HVACAction:
        """Return current HVAC action."""
        state = self.coordinator.get_device_state(self._device_id)
        if not state:
            return HVACAction.OFF

        mode = state.get("state", {}).get("mode", "idle")
        if mode == "preheat":
            return HVACAction.PREHEATING
        if mode == "cook":
            return HVACAction.HEATING
        return HVACAction.OFF

    def _active_bulb(self) -> dict[str, Any]:
        """Return the temperature bulb (dry or wet) the oven is controlling."""
        state = self.coordinator.get_device_state(self._device_id)
        if not state:
            return {}

        bulbs = state.get("nodes", {}).get("temperatureBulbs", {})
        mode = "wet" if bulbs.get("mode") == "wet" else "dry"
        return bulbs.get(mode, {})

    @property
    def current_temperature(self) -> float | None:
        """Return the current temperature."""
        return self._active_bulb().get("current", {}).get("celsius")

    @property
    def target_temperature(self) -> float | None:
        """Return the target temperature."""
        return self._active_bulb().get("setpoint", {}).get("celsius")

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return additional state attributes."""
        state = self.coordinator.get_device_state(self._device_id)
        if not state:
            return {}

        nodes = state.get("nodes", {})
        temp_bulbs = nodes.get("temperatureBulbs", {})
        timer = nodes.get("timer", {})
        steam = nodes.get("steamGenerators", {})

        attrs = {
            "temperature_mode": temp_bulbs.get("mode"),
            "timer_mode": timer.get("mode"),
            "timer_current": timer.get("current"),
            "timer_initial": timer.get("initial"),
            "humidity": steam.get("relativeHumidity", {}).get("current"),
        }

        if temp_bulbs.get("mode") == "wet":
            wet = temp_bulbs.get("wet", {})
            attrs["wet_temperature_celsius"] = wet.get("current", {}).get("celsius")

        return attrs

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        """Set new target HVAC mode."""
        if hvac_mode == HVACMode.OFF:
            await self._async_turn_off()
        elif hvac_mode == HVACMode.HEAT:
            await self._async_turn_on()

    async def async_set_temperature(self, **kwargs: Any) -> None:
        """Set new target temperature."""
        temperature = kwargs.get(ATTR_TEMPERATURE)
        if temperature is None:
            return

        if not isinstance(temperature, (int, float)):
            _LOGGER.error("Invalid temperature type: %s", type(temperature).__name__)
            return

        if temperature < TEMP_MIN_CELSIUS or temperature > TEMP_MAX_CELSIUS:
            _LOGGER.error(
                "Temperature %s°C out of range (%s-%s°C)",
                temperature,
                TEMP_MIN_CELSIUS,
                TEMP_MAX_CELSIUS,
            )
            return

        if self.hvac_mode == HVACMode.HEAT:
            await self._async_start_cook(temperature)
        else:
            _LOGGER.warning("Cannot set temperature when oven is off")

    async def async_turn_on(self) -> None:
        """Turn on the oven."""
        await self._async_turn_on()

    async def async_turn_off(self) -> None:
        """Turn off the oven."""
        await self._async_turn_off()

    async def _async_turn_on(self) -> None:
        """Start cooking with current or default temperature."""
        target_temp = self.target_temperature
        if target_temp is None:
            target_temp = DEFAULT_TEMPERATURE_CELSIUS

        await self._async_start_cook(target_temp)

    async def _async_turn_off(self) -> None:
        """Stop cooking."""
        try:
            await self.coordinator.send_command(self._device_id, CMD_APO_STOP)
            await self.coordinator.async_request_refresh()
        except Exception as err:
            _LOGGER.error("Failed to stop oven: %s", err)

    async def _async_start_cook(self, temperature: float) -> None:
        """Start cooking with specified temperature.

        Note: this always starts a fresh single-stage dry-mode cook with
        default fan speed, humidity, and top+rear heating. It will
        overwrite any in-progress multi-stage or steam cook started from
        the Anova app.
        """
        device_info = self.coordinator.get_device_info(self._device_id)
        device_type = (
            device_info.get("type", OVEN_TYPE_V1) if device_info else OVEN_TYPE_V1
        )

        payload = self._create_cook_payload(temperature, device_type)

        try:
            await self.coordinator.send_command(
                self._device_id, CMD_APO_START, payload
            )
            await self.coordinator.async_request_refresh()
        except Exception as err:
            _LOGGER.error("Failed to start oven: %s", err)

    def _create_cook_payload(
        self, temperature: float, device_type: str
    ) -> dict[str, Any]:
        """Create a cook command payload."""
        cook_id = str(uuid.uuid4())
        stage_id = str(uuid.uuid4())

        temperature = max(TEMP_MIN_CELSIUS, min(TEMP_MAX_CELSIUS, temperature))
        fahrenheit = round(temperature * 9 / 5 + 32)

        if device_type == OVEN_TYPE_V2:
            # v2 stages wrap the node settings in "do"; no "exit" means the
            # stage runs until stopped.
            return {
                "stages": [
                    {
                        "id": stage_id,
                        "do": {
                            "type": "cook",
                            "fan": {"speed": DEFAULT_FAN_SPEED},
                            "heatingElements": {
                                "top": {"on": True},
                                "bottom": {"on": False},
                                "rear": {"on": True},
                            },
                            "exhaustVent": {"state": "closed"},
                            "temperatureBulbs": {
                                "mode": "dry",
                                "dry": {"setpoint": {"celsius": temperature}},
                            },
                            "steamGenerators": {
                                "mode": "relative-humidity",
                                "relativeHumidity": {"setpoint": DEFAULT_HUMIDITY},
                            },
                        },
                        "title": "Cook",
                        "description": "Home Assistant Cook",
                        "rackPosition": 3,
                    }
                ],
                "cookId": cook_id,
                "cookerId": self._device_id,
                "cookableId": "",
                "title": "",
                "type": device_type,
                "originSource": "api",
                "cookableType": "manual",
            }

        payload: dict[str, Any] = {
            "stages": [
                {
                    "id": stage_id,
                    "type": "cook",
                    "title": "Cook",
                    "description": "Home Assistant Cook",
                    "userActionRequired": False,
                    "temperatureBulbs": {
                        "mode": "dry",
                        "dry": {
                            "setpoint": {
                                "celsius": temperature,
                                "fahrenheit": fahrenheit,
                            }
                        },
                    },
                    "heatingElements": {
                        "top": {"on": True},
                        "bottom": {"on": False},
                        "rear": {"on": True},
                    },
                    "fan": {"speed": DEFAULT_FAN_SPEED},
                    "vent": {"open": False},
                    "steamGenerators": {
                        "mode": "relative-humidity",
                        "relativeHumidity": {"setpoint": DEFAULT_HUMIDITY},
                    },
                }
            ],
            "cookId": cook_id,
            "originSource": "api",
        }

        return payload
