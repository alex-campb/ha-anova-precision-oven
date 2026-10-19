"""Camera platform for Anova Precision Oven (v2, via the app login).

The oven only streams while cooking. Starting its live stream over the app
login returns a Cloudflare WebRTC (WHEP) playback URL. (The SRT URL it also
returns carries no video for the oven's stream, so it is not used.)

Snapshots: frames are read from the WHEP URL directly with aiortc (see
whep.py), in the background because the WebRTC setup can take longer than
the 10 seconds HA allows a snapshot; a request returns the latest frame.
aiortc is installed when the camera is set up, so only v2 ovens with the
app login need it, and if it cannot be installed only snapshots are lost.

Live view: the same URL is handed to HA's built-in go2rtc.

The stream is kept alive while requested and stopped when it is not.
"""
from __future__ import annotations

import asyncio
from datetime import timedelta
import importlib
import logging
import time
from typing import Any

from homeassistant.components.camera import Camera, CameraEntityFeature
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.requirements import async_process_requirements

from .const import DOMAIN, OVEN_TYPE_V2
from .coordinator import AnovaOvenConfigEntry, AnovaOvenCoordinator
from .entity import AnovaOvenEntity
from .whep import async_grab_jpeg

_LOGGER = logging.getLogger(__name__)

CMD_APO_START_LIVE_STREAM = "CMD_APO_START_LIVE_STREAM"
CMD_APO_STOP_LIVE_STREAM = "CMD_APO_STOP_LIVE_STREAM"

# The app re-sends the start command about once a minute to keep streaming
STREAM_KEEPALIVE = timedelta(seconds=50)
# Stop the oven's stream when Home Assistant has not asked for it this long
STREAM_IDLE_STOP_SECONDS = 600
# The oven's publisher needs a moment after a fresh start, or WHEP gets 409
STREAM_WARMUP_SECONDS = 3
# Returned while HA checks which provider can play this camera; only the
# webrtc: scheme matters there, so the oven's stream is not started for it
PROVIDER_PROBE_URL = "webrtc:https://anova.invalid/webRTC/play"
# Installed on demand by the camera platform (compatible with HA's av pin)
AIORTC_REQUIREMENT = "aiortc==1.15.0"
# How long one background WebRTC frame grab may take
FRAME_GRAB_TIMEOUT_SECONDS = 30
# How long a snapshot request waits for a grab before returning the last frame
SNAPSHOT_WAIT_SECONDS = 8


async def async_setup_entry(
    hass: HomeAssistant,
    entry: AnovaOvenConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the oven camera for v2 ovens when the app login is configured."""
    coordinator = entry.runtime_data.coordinator
    if coordinator.app_session is None:
        return

    aiortc = None
    try:
        await async_process_requirements(
            hass, f"{DOMAIN}.camera", [AIORTC_REQUIREMENT], is_built_in=False
        )
        aiortc = await hass.async_add_import_executor_job(
            importlib.import_module, "aiortc"
        )
    except Exception as err:
        _LOGGER.warning(
            "Oven camera snapshots are unavailable because %s could not be "
            "installed: %s",
            AIORTC_REQUIREMENT,
            err,
        )

    known_devices: set[str] = set()

    def create_entities_for_device(device_id: str) -> None:
        info = coordinator.get_device_info(device_id) or {}
        if device_id in known_devices or info.get("type") != OVEN_TYPE_V2:
            return

        known_devices.add(device_id)
        async_add_entities([AnovaOvenCamera(coordinator, device_id, aiortc)])
        _LOGGER.debug("Created camera entity for device %s", device_id)

    for device_id in coordinator.devices:
        create_entities_for_device(device_id)

    @callback
    def handle_coordinator_update() -> None:
        for device_id in coordinator.devices:
            create_entities_for_device(device_id)

    entry.async_on_unload(coordinator.async_add_listener(handle_coordinator_update))


class AnovaOvenCamera(AnovaOvenEntity, Camera):
    """Snapshots (aiortc) and live view (go2rtc) of the oven cavity."""

    _attr_supported_features = CameraEntityFeature.STREAM

    def __init__(
        self,
        coordinator: AnovaOvenCoordinator,
        device_id: str,
        aiortc: Any,
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
        self._probing_providers = False
        self._grab_task: asyncio.Task | None = None
        self._aiortc = aiortc
        self._last_image: bytes | None = None

    async def async_camera_image(
        self, width: int | None = None, height: int | None = None
    ) -> bytes | None:
        """Return a fresh frame if one arrives in time, else the last frame."""
        if not self._is_cooking:
            return self._last_image

        if self._grab_task is None or self._grab_task.done():
            self._grab_task = self.hass.async_create_background_task(
                self._async_grab_frame(), f"{self.entity_id} frame grab"
            )
        try:
            await asyncio.wait_for(asyncio.shield(self._grab_task), SNAPSHOT_WAIT_SECONDS)
        except TimeoutError:
            pass
        return self._last_image

    async def _async_grab_frame(self) -> None:
        """Read one frame from the oven's WHEP stream, logging failures."""
        if self._aiortc is None:
            return

        started = time.monotonic()
        try:
            whep_url = await self._async_ensure_stream()
            if whep_url is None:
                return
            image = await async_grab_jpeg(
                self._aiortc,
                async_get_clientsession(self.hass),
                whep_url,
                FRAME_GRAB_TIMEOUT_SECONDS,
            )
        except Exception as err:  # WhepError, network or stream start errors
            _LOGGER.warning(
                "No frame from the oven camera after %.0fs: %s",
                time.monotonic() - started,
                err,
            )
            return

        self._last_image = image
        _LOGGER.debug(
            "Oven camera frame took %.1fs (%d bytes)",
            time.monotonic() - started,
            len(image),
        )

    @property
    def _is_cooking(self) -> bool:
        state = self.coordinator.get_device_state(self._device_id) or {}
        return state.get("state", {}).get("mode") == "cook"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Explain why there is no picture when idle."""
        return {"streaming": self._whep_url is not None, "cooking": self._is_cooking}

    async def async_refresh_providers(self, *, write_state: bool = True) -> None:
        """Let HA link this camera to go2rtc without starting the oven stream.

        HA picks the WebRTC provider once, when the entity is added, by
        calling stream_source(). At that point the oven may not be cooking
        (or its state not loaded yet), which would leave the camera without
        go2rtc for good.
        """
        self._probing_providers = True
        try:
            await super().async_refresh_providers(write_state=write_state)
        finally:
            self._probing_providers = False

    async def stream_source(self) -> str | None:
        """Start (or keep) the oven's live stream and return it for go2rtc."""
        if self._probing_providers:
            return PROVIDER_PROBE_URL
        whep_url = await self._async_ensure_stream()
        return f"webrtc:{whep_url}" if whep_url else None

    async def _async_ensure_stream(self) -> str | None:
        """Start (or keep) the oven's live stream; return its WHEP URL."""
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
            return self._whep_url

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
        if self._grab_task and not self._grab_task.done():
            self._grab_task.cancel()
        await self._async_stop_stream()
        await super().async_will_remove_from_hass()
