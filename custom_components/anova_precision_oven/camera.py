"""Camera platform for Anova Precision Oven (v2, via the app login).

The oven only streams while cooking. Starting its live stream over the app
login returns a Cloudflare WebRTC (WHEP) playback URL, which is handed to
Home Assistant's built-in go2rtc for live view and snapshots. (The SRT URL
it also returns carries no video for the oven's stream, so it is not used.)

Snapshots: HA allows a snapshot 10 seconds, and opening the WebRTC
connection and waiting for a key frame can take longer. Frames are
therefore grabbed in the background (go2rtc's snapshot API, then ffmpeg
reading go2rtc's local RTSP restream with a longer timeout) and a request
returns the latest frame. Failures are logged.

The stream is kept alive while requested and stopped when it is not.
"""
from __future__ import annotations

import asyncio
from datetime import timedelta
import logging
import re
import secrets
import time
from urllib.parse import urljoin
from typing import Any

from homeassistant.components.camera import Camera, CameraEntityFeature
from homeassistant.components.ffmpeg import get_ffmpeg_manager

try:
    # HA 2026.x names go2rtc streams "<platform>_<unique_id>"
    from homeassistant.components.go2rtc.util import get_camera_identifier
except ImportError:
    # Older HA named them after the entity_id
    get_camera_identifier = None
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import async_track_time_interval

from .const import OVEN_TYPE_V2
from .coordinator import AnovaOvenConfigEntry, AnovaOvenCoordinator
from .entity import AnovaOvenEntity
from .ice_probe import async_check_answer, async_public_stun_check

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
# go2rtc as run by Home Assistant restreams every camera over local RTSP
GO2RTC_RTSP_URL = "rtsp://127.0.0.1:18554/{name}"
# How long a background ffmpeg grab may take
FRAME_GRAB_TIMEOUT_SECONDS = 30
# How long a snapshot request waits for a grab before returning the last frame
SNAPSHOT_WAIT_SECONDS = 8
# Hard limits so a stuck step cannot block all later snapshots
GO2RTC_SNAPSHOT_TIMEOUT_SECONDS = 15
GO2RTC_INFO_TIMEOUT_SECONDS = 10
GRAB_TOTAL_TIMEOUT_SECONDS = 60


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


def _go2rtc_stream_name(camera: Camera) -> str:
    """Return the name HA's go2rtc gives this camera's stream."""
    if get_camera_identifier is None:
        return camera.entity_id
    return get_camera_identifier(camera)


async def _async_go2rtc_stream_info(provider: Any, camera: Camera) -> str:
    """Summarise go2rtc's connections for this stream (diagnostics only).

    Uses go2rtc's /api/streams through HA's client internals, so any error
    just yields a note instead of failing the snapshot.
    """
    try:
        resp = await provider._rest_client.streams._client.request(  # noqa: SLF001
            "GET", "/api/streams", params={"src": _go2rtc_stream_name(camera)}
        )
        info = await resp.json()
    except Exception as err:
        return f"unavailable ({err!r})"

    # go2rtc's WHEP client keeps no SDP; its medias come from the applied
    # answer, and remote_addr is set only once ICE selects a candidate pair
    parts = []
    for prod in info.get("producers") or []:
        parts.append(
            f"[{prod.get('format_name')}/{prod.get('protocol')} "
            f"remote={prod.get('remote_addr')} recv={prod.get('bytes_recv', 0)}B "
            f"medias={prod.get('medias')}]"
        )
    consumers = len(info.get("consumers") or [])
    return f"{len(parts)} producer(s) {' '.join(parts)}; {consumers} consumer(s)"


def _probe_offer() -> tuple[str, str]:
    """Build a receive-only SDP offer, enough for a WHEP server to answer.

    Returns the offer and its ICE username fragment. Only the answer and
    an ICE check are used; no media connection is made.
    """
    ufrag = secrets.token_hex(4)
    fingerprint = ":".join(f"{b:02X}" for b in secrets.token_bytes(32))
    lines = [
        "v=0",
        "o=- 0 0 IN IP4 127.0.0.1",
        "s=-",
        "t=0 0",
        "a=group:BUNDLE 0 1",
        f"a=ice-ufrag:{ufrag}",
        f"a=ice-pwd:{secrets.token_hex(12)}",
        f"a=fingerprint:sha-256 {fingerprint}",
        "a=setup:actpass",
        "m=video 9 UDP/TLS/RTP/SAVPF 96 97 98",
        "c=IN IP4 0.0.0.0",
        "a=mid:0",
        "a=recvonly",
        "a=rtcp-mux",
        "a=rtpmap:96 H264/90000",
        "a=fmtp:96 level-asymmetry-allowed=1;packetization-mode=1;profile-level-id=42e01f",
        "a=rtpmap:97 VP8/90000",
        "a=rtpmap:98 H265/90000",
        "m=audio 9 UDP/TLS/RTP/SAVPF 111",
        "c=IN IP4 0.0.0.0",
        "a=mid:1",
        "a=recvonly",
        "a=rtcp-mux",
        "a=rtpmap:111 opus/48000/2",
    ]
    return "\r\n".join(lines) + "\r\n", ufrag


async def _async_whep_probe(hass: HomeAssistant, whep_url: str) -> str:
    """Ask the WHEP server for an answer and summarise it (diagnostics only).

    While the probe's session is open, also send an ICE check from this
    host to each of the answer's candidates, then a plain STUN request to
    Cloudflare, to see whether UDP to Cloudflare works from here.
    """
    http = async_get_clientsession(hass)
    offer, ufrag = _probe_offer()
    location = None
    ice = "not checked"
    try:
        async with asyncio.timeout(10):
            async with http.post(
                whep_url,
                data=offer,
                headers={"Content-Type": "application/sdp"},
            ) as resp:
                status = resp.status
                body = await resp.text()
                location = resp.headers.get("Location")
        if status == 201:
            try:
                async with asyncio.timeout(15):
                    ice = await async_check_answer(body, ufrag)
            except Exception as err:
                ice = f"check failed ({err!r})"
    except Exception as err:
        return f"failed ({err!r})"
    finally:
        if location:
            try:
                async with asyncio.timeout(5):
                    async with http.delete(urljoin(whep_url, location)):
                        pass
            except Exception:
                pass
    try:
        async with asyncio.timeout(10):
            stun = await async_public_stun_check()
    except Exception as err:
        stun = f"STUN check failed ({err!r})"

    if status != 201:
        return f"HTTP {status}: {body.strip()[:200]}; {stun}"
    answer = []
    for section in re.split(r"(?m)^(?=m=)", body)[1:]:
        kind, port = section.split()[0][2:], section.split()[1]
        codecs = sorted(set(re.findall(r"a=rtpmap:\d+ ([^/]+)/", section)))
        answer.append(f"{kind}:{port}:{'/'.join(codecs)}")
    return f"HTTP 201, answer={answer}; {ice}; {stun}"


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
        self._probing_providers = False
        self._grab_task: asyncio.Task | None = None
        self._grab_step = ""
        self._last_image: bytes | None = None

    async def async_camera_image(
        self, width: int | None = None, height: int | None = None
    ) -> bytes | None:
        """Return a fresh frame if one arrives in time, else the last frame."""
        if not self._is_cooking:
            return self._last_image

        if self._grab_task is None or self._grab_task.done():
            self._grab_task = self.hass.async_create_background_task(
                self._async_grab_frame_limited(), f"{self.entity_id} frame grab"
            )
        try:
            await asyncio.wait_for(asyncio.shield(self._grab_task), SNAPSHOT_WAIT_SECONDS)
        except TimeoutError:
            pass
        return self._last_image

    async def _async_grab_frame_limited(self) -> None:
        """Run one grab with an overall limit, logging the step it stopped at."""
        self._grab_step = "starting"
        try:
            async with asyncio.timeout(GRAB_TOTAL_TIMEOUT_SECONDS):
                await self._async_grab_frame()
        except TimeoutError:
            _LOGGER.warning(
                "Oven camera frame grab stopped after %ss during: %s",
                GRAB_TOTAL_TIMEOUT_SECONDS,
                self._grab_step,
            )
        except Exception:
            _LOGGER.exception("Oven camera frame grab failed during: %s", self._grab_step)

    async def _async_grab_frame(self) -> None:
        """Grab a frame through go2rtc, logging why if none arrives."""
        provider = self._webrtc_provider
        if provider is None:
            _LOGGER.warning(
                "Oven camera snapshots need Home Assistant's go2rtc, which is not available"
            )
            return

        started = time.monotonic()
        # Also registers the stream with go2rtc
        self._grab_step = "go2rtc snapshot"
        _LOGGER.debug("Oven camera grab: go2rtc snapshot")
        go2rtc_error = "returned no image"
        try:
            async with asyncio.timeout(GO2RTC_SNAPSHOT_TIMEOUT_SECONDS):
                image = await provider.async_get_image(self)
        except Exception as err:
            go2rtc_error = repr(err)
            image = None
        if image:
            self._last_image = image
            _LOGGER.debug("go2rtc snapshot took %.1fs", time.monotonic() - started)
            return

        self._grab_step = f"ffmpeg via go2rtc RTSP (go2rtc snapshot {go2rtc_error})"
        _LOGGER.debug("Oven camera grab: %s", self._grab_step)
        proc = await asyncio.create_subprocess_exec(
            get_ffmpeg_manager(self.hass).binary,
            "-hide_banner",
            "-loglevel",
            "warning",
            "-rtsp_transport",
            "tcp",
            "-i",
            GO2RTC_RTSP_URL.format(name=_go2rtc_stream_name(self)),
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
        communicate = asyncio.ensure_future(proc.communicate())
        go2rtc_info = "not read"
        try:
            # Read go2rtc's view of the stream while ffmpeg is still attached
            done, _ = await asyncio.wait({communicate}, timeout=FRAME_GRAB_TIMEOUT_SECONDS / 2)
            if not done:
                try:
                    async with asyncio.timeout(GO2RTC_INFO_TIMEOUT_SECONDS):
                        go2rtc_info = await _async_go2rtc_stream_info(provider, self)
                except TimeoutError:
                    go2rtc_info = "no reply from go2rtc"
                # asyncio.wait, not wait_for: wait_for would cancel communicate,
                # losing ffmpeg's output once it is killed below
                done, _ = await asyncio.wait(
                    {communicate}, timeout=FRAME_GRAB_TIMEOUT_SECONDS / 2
                )
            if not done:
                proc.kill()
            stdout, stderr = await communicate
            if not done:
                stderr += b"\n(timed out waiting for a frame)"
        finally:
            if proc.returncode is None:
                proc.kill()

        elapsed = time.monotonic() - started
        if stdout:
            self._last_image = stdout
            _LOGGER.debug("Oven camera frame via go2rtc RTSP took %.1fs", elapsed)
            return
        whep_probe = "not run"
        if self._whep_url:
            whep_probe = await _async_whep_probe(self.hass, self._whep_url)
        _LOGGER.warning(
            "No frame from the oven camera after %.0fs; go2rtc snapshot %s; "
            "ffmpeg via go2rtc RTSP exit %s: %s; go2rtc stream: %s; "
            "direct WHEP probe: %s",
            elapsed,
            go2rtc_error,
            proc.returncode,
            stderr.decode(errors="replace").strip()[-1500:] or "no output",
            go2rtc_info,
            whep_probe,
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
