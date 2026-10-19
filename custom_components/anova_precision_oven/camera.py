"""Camera platform for Anova Precision Oven (v2, snapshots via the app login).

The oven only streams while cooking. A snapshot starts the oven's live
stream over the app login and grabs one frame from its SRT feed with
ffmpeg in the background; the stream is stopped again once snapshots stop
being requested. Home Assistant gives a snapshot about 10 seconds, so a
request waits a bounded time and otherwise returns the latest frame.
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any
from urllib.parse import urlencode

from homeassistant.components.camera import Camera
from homeassistant.components.ffmpeg import get_ffmpeg_manager
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
# How long one ffmpeg frame grab may take
FRAME_GRAB_TIMEOUT_SECONDS = 25
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

        self._grab_task: asyncio.Task | None = None
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
        """Return a fresh frame if one arrives in time, else the last frame."""
        if not self._is_cooking:
            return self._last_image

        self._schedule_idle_stop()
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
        """Start the stream if needed and read one JPEG frame with ffmpeg."""
        try:
            srt_url = await self._async_ensure_stream()
        except Exception as err:
            _LOGGER.warning("Could not start the oven camera stream: %s", err)
            return

        binary = get_ffmpeg_manager(self.hass).binary
        proc = await asyncio.create_subprocess_exec(
            binary,
            "-hide_banner",
            "-loglevel",
            "warning",
            "-i",
            srt_url,
            "-frames:v",
            "1",
            "-c:v",
            "mjpeg",
            "-f",
            "image2pipe",
            "pipe:1",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), FRAME_GRAB_TIMEOUT_SECONDS
            )
        except TimeoutError:
            proc.kill()
            stdout, stderr = await proc.communicate()
            stderr += b"\n(timed out waiting for a frame)"

        if stdout:
            self._last_image = stdout
            return

        # Passphrase must not reach the log
        detail = stderr.decode(errors="replace").strip()
        if self._srt_url:
            detail = detail.replace(self._srt_url, "<stream url>")
        _LOGGER.warning(
            "No frame from the oven camera stream (ffmpeg exit %s): %s",
            proc.returncode,
            detail[-1500:] or "no output",
        )

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
        if self._grab_task and not self._grab_task.done():
            self._grab_task.cancel()
        if self._cancel_idle_stop:
            self._cancel_idle_stop()
            self._cancel_idle_stop = None
        await self._async_stop_stream()
        await super().async_will_remove_from_hass()
