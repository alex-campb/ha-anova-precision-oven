"""Camera platform for Anova Precision Oven (v2, via the app login).

The oven only streams while cooking. Starting its live stream over the app
login returns a Cloudflare WebRTC (WHEP) playback URL; Home Assistant's
built-in go2rtc plays that URL for both snapshots and live view. The
SRT URL it also returns carries no video for the oven's stream, so it is
not used. The stream is kept alive while it is being watched and stopped
when it is no longer requested.
"""
from __future__ import annotations

import asyncio
from datetime import timedelta
import logging
import time
from typing import Any

from homeassistant.components.camera import Camera, CameraEntityFeature
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import async_track_time_interval

from .const import OVEN_TYPE_V2
from .coordinator import AnovaOvenConfigEntry, AnovaOvenCoordinator
from .entity import AnovaOvenEntity

_LOGGER = logging.getLogger(__name__)

CMD_APO_START_LIVE_STREAM = "CMD_APO_START_LIVE_STREAM"
CMD_APO_STOP_LIVE_STREAM = "CMD_APO_STOP_LIVE_STREAM"

# The app re-sends the start command about once a minute to keep streaming
STREAM_KEEPALIVE = timedelta(seconds=50)
# Stop the oven's stream when Home Assistant has not asked for it this long
STREAM_IDLE_STOP_SECONDS = 600
# The oven's publisher needs a moment after a fresh start, or WHEP gets 409
STREAM_WARMUP_SECONDS = 3


async def async_setup_entry(
    hass: HomeAssistant,
    entry: AnovaOvenConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the oven camera for v2 ovens when the app login is configured."""
    coordinator = entry.runtime_data.coordinator
    if coordinator.app_session is None:
        return

    known_devices: set[str] = set()

    def create_entities_for_device(device_id: str) -> None:
        info = coordinator.get_device_info(device_id) or {}
        if device_id in known_devices or info.get("type") != OVEN_TYPE_V2:
            return

        known_devices.add(device_id)
        async_add_entities([AnovaOvenCamera(coordinator, device_id)])
        _LOGGER.debug("Created camera entity for device %s", device_id)

    for device_id in coordinator.devices:
        create_entities_for_device(device_id)

    @callback
    def handle_coordinator_update() -> None:
        for device_id in coordinator.devices:
            create_entities_for_device(device_id)

    entry.async_on_unload(coordinator.async_add_listener(handle_coordinator_update))


class AnovaOvenCamera(AnovaOvenEntity, Camera):
    """Live view and snapshots of the oven cavity, played by go2rtc."""

    _attr_supported_features = CameraEntityFeature.STREAM

    def __init__(
        self,
        coordinator: AnovaOvenCoordinator,
        device_id: str,
    ) -> None:
        """Initialize the camera."""
        AnovaOvenEntity.__init__(self, coordinator, device_id)
        Camera.__init__(self)
        self._attr_unique_id = f"{device_id}_camera"
        self._attr_name = "Camera"

        self._lock = asyncio.Lock()
        self._whep_url: str | None = None
        self._last_requested = 0.0
        self._cancel_keepalive: Any = None

    @property
    def use_stream_for_stills(self) -> bool:
        """Take snapshots from the stream (go2rtc); there is no still URL."""
        return True

    @property
    def _is_cooking(self) -> bool:
        state = self.coordinator.get_device_state(self._device_id) or {}
        return state.get("state", {}).get("mode") == "cook"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Explain why there is no picture when idle."""
        return {"streaming": self._whep_url is not None, "cooking": self._is_cooking}

    async def stream_source(self) -> str | None:
        """Start (or keep) the oven's live stream and return it for go2rtc."""
        if not self._is_cooking:
            await self._async_stop_stream()
            return None

        async with self._lock:
            self._last_requested = time.monotonic()
            if self._whep_url is None:
                try:
                    self._whep_url = await self._async_start_stream()
                except Exception as err:
                    _LOGGER.warning("Could not start the oven camera stream: %s", err)
                    return None
                self._start_keepalive()
                await asyncio.sleep(STREAM_WARMUP_SECONDS)
            return f"webrtc:{self._whep_url}"

    async def _async_start_stream(self) -> str:
        """Send the live-stream start command and return the WHEP URL."""
        response = await self.coordinator.send_app_command(
            self._device_id, CMD_APO_START_LIVE_STREAM
        )
        result = response.get("payload", {})
        url = result.get("data", {}).get("webRTCPlayback", {}).get("url")
        if result.get("status") != "ok" or not url:
            raise RuntimeError(f"Anova refused live video: {result.get('error')}")
        return url

    @callback
    def _start_keepalive(self) -> None:
        if self._cancel_keepalive is None:
            self._cancel_keepalive = async_track_time_interval(
                self.hass, self._async_keepalive, STREAM_KEEPALIVE
            )

    async def _async_keepalive(self, _now: Any = None) -> None:
        """Keep streaming while cooking and recently requested, else stop."""
        idle = time.monotonic() - self._last_requested > STREAM_IDLE_STOP_SECONDS
        if idle or not self._is_cooking:
            await self._async_stop_stream()
            return
        try:
            await self._async_start_stream()
        except Exception as err:
            _LOGGER.debug("Oven camera keepalive failed: %s", err)

    async def _async_stop_stream(self) -> None:
        """Stop the oven's live stream if this camera started it."""
        if self._cancel_keepalive:
            self._cancel_keepalive()
            self._cancel_keepalive = None
        if self._whep_url is None:
            return
        self._whep_url = None
        try:
            await self.coordinator.send_app_command(
                self._device_id, CMD_APO_STOP_LIVE_STREAM
            )
        except Exception as err:
            _LOGGER.debug("Stopping the oven camera stream failed: %s", err)

    async def async_will_remove_from_hass(self) -> None:
        """Stop streaming when the entity is removed."""
        await self._async_stop_stream()
        await super().async_will_remove_from_hass()
