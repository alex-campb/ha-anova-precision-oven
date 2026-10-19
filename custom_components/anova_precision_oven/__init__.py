"""The Anova Precision Oven integration."""
from __future__ import annotations

import re

import voluptuous as vol

from homeassistant.const import Platform
from homeassistant.core import (
    HomeAssistant,
    ServiceCall,
    ServiceResponse,
    SupportsResponse,
)
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.service import async_register_admin_service

from .const import CONF_TOKEN, DOMAIN
from .coordinator import (
    AnovaOvenConfigEntry,
    AnovaOvenCoordinator,
    AnovaOvenRuntimeData,
)

PLATFORMS: list[Platform] = [
    Platform.SENSOR,
    Platform.BINARY_SENSOR,
    Platform.CLIMATE,
    Platform.SWITCH,
]


SERVICE_SEND_COMMAND = "send_command"
ATTR_COMMAND = "command"
ATTR_PAYLOAD = "payload"
ATTR_COOKER_ID = "cooker_id"

SEND_COMMAND_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_COMMAND): vol.All(
            cv.string, vol.Match(re.compile(r"^CMD_APO_[A-Z0-9_]+$"))
        ),
        vol.Optional(ATTR_PAYLOAD): dict,
        vol.Optional(ATTR_COOKER_ID): cv.string,
    }
)


async def _async_send_command(call: ServiceCall) -> ServiceResponse:
    """Send a raw oven command and return Anova's reply (advanced/testing)."""
    hass = call.hass
    entries = hass.config_entries.async_loaded_entries(DOMAIN)
    coordinators = [entry.runtime_data.coordinator for entry in entries]
    cooker_id = call.data.get(ATTR_COOKER_ID)

    matches = [
        (coordinator, device_id)
        for coordinator in coordinators
        for device_id in coordinator.devices
        if cooker_id in (None, device_id)
    ]
    if len(matches) != 1:
        raise HomeAssistantError(
            "Set cooker_id to choose an oven" if matches else "No matching oven found"
        )

    coordinator, device_id = matches[0]
    try:
        response = await coordinator.send_command_and_wait(
            device_id, call.data[ATTR_COMMAND], call.data.get(ATTR_PAYLOAD)
        )
    except TimeoutError as err:
        raise HomeAssistantError("Anova did not reply within 15 seconds") from err
    return {"response": response}


async def async_setup_entry(hass: HomeAssistant, entry: AnovaOvenConfigEntry) -> bool:
    """Set up Anova Precision Oven from a config entry."""
    if not hass.services.has_service(DOMAIN, SERVICE_SEND_COMMAND):
        async_register_admin_service(
            hass,
            DOMAIN,
            SERVICE_SEND_COMMAND,
            _async_send_command,
            SEND_COMMAND_SCHEMA,
            supports_response=SupportsResponse.ONLY,
        )

    coordinator = AnovaOvenCoordinator(hass, entry.data[CONF_TOKEN])
    entry.runtime_data = AnovaOvenRuntimeData(coordinator=coordinator)

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    if hass.is_running:
        hass.async_create_background_task(
            coordinator.start_websocket(),
            "anova_precision_oven_websocket",
        )
    else:
        async def _start_websocket(_event):
            hass.async_create_background_task(
                coordinator.start_websocket(),
                "anova_precision_oven_websocket",
            )

        entry.async_on_unload(
            hass.bus.async_listen_once("homeassistant_started", _start_websocket)
        )

    return True


async def async_unload_entry(
    hass: HomeAssistant, entry: AnovaOvenConfigEntry
) -> bool:
    """Unload a config entry."""
    if unload_ok := await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        await entry.runtime_data.coordinator.stop_websocket()

    return unload_ok
