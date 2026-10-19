"""Camera platform for Anova Precision Oven (v2, snapshots via the app login).

The oven only streams while cooking. A snapshot starts the oven's live
stream over the app login, grabs one frame from its SRT feed with ffmpeg,
and the stream is stopped again once snapshots stop being requested.
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any
from urllib.parse import urlencode

from homeassistant.components.camera import Camera
from homeassistant.components.ffmpeg import async_get_image
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import async_call_later

from .const import OVEN_TYPE_V2
from .coordinator import AnovaOvenConfigEntry, AnovaOvenCoordinator
from .entity import AnovaOvenEntity

_LOGGER = logging.getLogger(__name__)

CMD_APO_START_LIVE_STREAM = "CMD_APO_START_LIVE_STREAM"
CMD_APO_STOP_LIVE_STREAM = "CMD_APO_STOP_LIVE_STREAM"

# The app re-sends the start command about once a minute to keep streaming
STREAM_KEEPALIVE_SECONDS = 50
# Stop the oven's stream when no snapshot has been requested for this long
STREAM_IDLE_STOP_SECONDS = 60
# The oven's publisher needs a moment after a fresh start
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
    """Snapshot camera of the oven cavity."""

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
        self._srt_url: str | None = None
        self._stream_requested_at = 0.0
        self._last_image: bytes | None = None
        self._cancel_idle_stop: Any = None

    @property
    def _is_cooking(self) -> bool:
        state = self.coordinator.get_device_state(self._device_id) or {}
        return state.get("state", {}).get("mode") == "cook"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Explain why there is no live picture when idle."""
        return {"streaming": self._srt_url is not None, "cooking": self._is_cooking}

    async def async_camera_image(
        self, width: int | None = None, height: int | None = None
    ) -> bytes | None:
        """Return a frame from the oven camera, or the last one when idle."""
        if not self._is_cooking:
            return self._last_image

        async with self._lock:
            try:
                srt_url = await self._async_ensure_stream()
            except Exception as err:
                _LOGGER.warning("Could not start the oven camera stream: %s", err)
                return self._last_image

            image = await async_get_image(self.hass, srt_url, width=width, height=height)
            if image is None:
                # The publisher may still be coming up; try once more
                await asyncio.sleep(STREAM_WARMUP_SECONDS)
                image = await async_get_image(
                    self.hass, srt_url, width=width, height=height
                )

            self._schedule_idle_stop()
            if image:
                self._last_image = image
            else:
                _LOGGER.warning("No frame received from the oven camera stream")
            return self._last_image

    async def _async_ensure_stream(self) -> str:
        """Start or keep alive the oven's live stream and return its SRT URL."""
        now = time.monotonic()
        if self._srt_url and now - self._stream_requested_at < STREAM_KEEPALIVE_SECONDS:
            return self._srt_url

        response = await self.coordinator.send_app_command(
            self._device_id, CMD_APO_START_LIVE_STREAM
        )
        result = response.get("payload", {})
        srt = result.get("data", {}).get("srtPlayback", {})
        if result.get("status") != "ok" or not srt.get("url"):
            raise RuntimeError(f"Anova refused live video: {result.get('error')}")

        fresh = self._srt_url is None
        self._stream_requested_at = now
        query = urlencode(
            {
                "streamid": srt["streamId"],
                "passphrase": srt["passphrase"],
                "mode": "caller",
            }
        )
        self._srt_url = f"{srt['url']}?{query}"
        if fresh:
            await asyncio.sleep(STREAM_WARMUP_SECONDS)
        return self._srt_url

    @callback
    def _schedule_idle_stop(self) -> None:
        if self._cancel_idle_stop:
            self._cancel_idle_stop()
        self._cancel_idle_stop = async_call_later(
            self.hass, STREAM_IDLE_STOP_SECONDS, self._async_idle_stop
        )

    async def _async_idle_stop(self, _now: Any = None) -> None:
        self._cancel_idle_stop = None
        await self._async_stop_stream()

    async def _async_stop_stream(self) -> None:
        """Stop the oven's live stream if this camera started it."""
        if self._srt_url is None:
            return
        self._srt_url = None
        try:
            await self.coordinator.send_app_command(
                self._device_id, CMD_APO_STOP_LIVE_STREAM
            )
        except Exception as err:
            _LOGGER.debug("Stopping the oven camera stream failed: %s", err)

    async def async_will_remove_from_hass(self) -> None:
        """Stop streaming when the entity is removed."""
        if self._cancel_idle_stop:
            self._cancel_idle_stop()
            self._cancel_idle_stop = None
        await self._async_stop_stream()
        await super().async_will_remove_from_hass()
