"""Grab one JPEG frame from a WebRTC-HTTP Egress (WHEP) playback URL.

The oven's camera publishes to Cloudflare Stream over WebRTC, which can
only be played back over WebRTC. This follows the sequence of the Anova
app (and of the anova-oven-mcp project, verified on an oven_v2): a
receive-only video offer posted to the WHEP URL, retried while the oven's
publisher is still coming up (HTTP 409).

aiortc is imported by the caller (in an executor) and passed in, because
it is installed on demand and importing it is slow.
"""
from __future__ import annotations

import asyncio
from io import BytesIO
import logging
from types import ModuleType
from urllib.parse import urljoin

import aiohttp

_LOGGER = logging.getLogger(__name__)

ICE_SERVERS = ["stun:stun.cloudflare.com:3478", "stun:stun.l.google.com:19302"]
WHEP_ATTEMPTS = 3


class WhepError(Exception):
    """No frame could be read from the WHEP URL."""


async def async_grab_jpeg(
    aiortc: ModuleType,
    http: aiohttp.ClientSession,
    whep_url: str,
    timeout: float,
) -> bytes:
    """Connect to the WHEP URL, decode one video frame and return it as JPEG."""
    pc = aiortc.RTCPeerConnection(
        aiortc.RTCConfiguration(iceServers=[aiortc.RTCIceServer(urls=ICE_SERVERS)])
    )
    resource_url: str | None = None
    loop = asyncio.get_running_loop()
    track_future: asyncio.Future = loop.create_future()

    @pc.on("track")
    def on_track(track) -> None:
        if track.kind == "video" and not track_future.done():
            track_future.set_result(track)

    try:
        async with asyncio.timeout(timeout):
            pc.addTransceiver("video", direction="recvonly")
            # aiortc gathers all ICE candidates here, so the offer is complete
            await pc.setLocalDescription(await pc.createOffer())

            answer = None
            status = None
            for attempt in range(WHEP_ATTEMPTS):
                async with http.post(
                    whep_url,
                    data=pc.localDescription.sdp.encode(),
                    headers={"Content-Type": "application/sdp"},
                ) as resp:
                    status = resp.status
                    if status == 201:
                        answer = await resp.text()
                        if location := resp.headers.get("Location"):
                            resource_url = urljoin(whep_url, location)
                        break
                await asyncio.sleep(0.5 * 2**attempt)
            if not answer:
                raise WhepError(f"WHEP playback rejected (HTTP {status})")

            await pc.setRemoteDescription(
                aiortc.RTCSessionDescription(sdp=answer, type="answer")
            )
            track = await track_future
            frame = await track.recv()
    except TimeoutError as err:
        raise WhepError(f"no video frame within {timeout:.0f}s") from err
    finally:
        await _async_close(pc, http, resource_url)

    # Converting to JPEG is CPU work; keep it off the event loop
    return await loop.run_in_executor(None, _to_jpeg, frame)


def _to_jpeg(frame) -> bytes:
    output = BytesIO()
    frame.to_image().save(output, format="JPEG", quality=85)
    return output.getvalue()


async def _async_close(
    pc, http: aiohttp.ClientSession, resource_url: str | None
) -> None:
    """Close the peer connection and release the WHEP viewer session."""
    try:
        async with asyncio.timeout(3):
            await pc.close()
    except Exception as err:
        _LOGGER.debug("Closing the WebRTC connection failed: %r", err)
    if resource_url:
        try:
            async with http.delete(
                resource_url, timeout=aiohttp.ClientTimeout(total=3)
            ):
                pass
        except Exception as err:
            _LOGGER.debug("Releasing the WHEP session failed: %r", err)
