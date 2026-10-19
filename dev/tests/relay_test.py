"""Camera through a real MediaMTX relay (live; the oven must be streaming).

Runs the camera entity with stand-ins for Home Assistant and the coordinator,
against a MediaMTX started locally with anova_camera_relay/mediamtx.yml
(API on 127.0.0.1:9997, RTSP on 127.0.0.1:8554). The stand-in coordinator
answers the start command with the WHEP URL given on the command line, so
keep the oven's stream alive meanwhile (for example by asking HA's camera
for a picture). Needs ffmpeg on PATH.

    python3 relay_test.py ../../custom_components <WHEP URL>
"""
import asyncio
import logging
import sys
import time
import types
from unittest.mock import patch

sys.path.insert(0, sys.argv[1])
import aiohttp

from anova_ovens import camera
from anova_ovens.relay import CameraRelay, RelayError, relay_host

logging.basicConfig(level=logging.WARNING, format="LOG %(levelname)s %(message)s")
WHEP_URL = sys.argv[2]

state = {"state": {"mode": "cook"}}
sent = []


async def send_app_command(device_id, command, payload=None):
    sent.append(command)
    return {"payload": {"status": "ok", "data": {"webRTCPlayback": {"url": WHEP_URL}}}}


coord = types.SimpleNamespace(
    app_session=object(),
    get_device_state=lambda d: state,
    get_device_info=lambda d: {"type": "oven_v2"},
    devices={"dev1": {}},
    last_update_success=True,
    send_app_command=send_app_command,
)


class Hass:
    def async_create_background_task(self, coro, name):
        return asyncio.ensure_future(coro)


def make(relay, live_view):
    cam = camera.AnovaOvenCamera.__new__(camera.AnovaOvenCamera)
    camera.AnovaOvenCamera.__init__(cam, coord, "dev1", relay, live_view)
    cam.hass = Hass()
    cam.entity_id = "camera.oven"
    return cam


async def main():
    print("relay_host:", [relay_host(v) for v in (
        "abcd-anova-camera-relay", "abcd-anova-camera-relay:9997",
        "http://abcd-anova-camera-relay:9997/", " host ")])
    async with aiohttp.ClientSession() as http:
        relay = CameraRelay(http, "127.0.0.1")
        down = CameraRelay(http, "127.0.0.1:1")
        down._api = "http://127.0.0.1:1/v3"
        await relay.async_check()
        try:
            await down.async_check()
        except RelayError as err:
            print("relay down check ->", err)

        with patch.object(camera, "get_ffmpeg_manager", lambda h: types.SimpleNamespace(binary="ffmpeg")), \
             patch.object(camera, "async_track_time_interval", lambda h, f, i: (lambda: None)):
            snap_only = make(relay, False)
            print("snapshots only: features", int(snap_only.supported_features),
                  "stream_source", await snap_only.stream_source())
            live = make(relay, True)
            print("live view: features", int(live.supported_features),
                  "stream_source", await live.stream_source())

            t = time.monotonic()
            img = await snap_only.async_camera_image()
            while img is None and time.monotonic() - t < 40:
                await asyncio.sleep(1)
                img = snap_only._last_image
            is_jpeg = bool(img) and img[:2] == bytes([0xFF, 0xD8])
            print(f"snapshot -> {len(img or b'')} bytes, JPEG={is_jpeg}, "
                  f"{time.monotonic() - t:.1f}s; commands {sent}")
            print("readers now:", await relay.async_reader_count(snap_only._relay_path))

            # A restarted relay has lost its API-added paths: the next grab must re-add it
            await http.request("DELETE", f"http://127.0.0.1:9997/v3/config/paths/delete/{snap_only._relay_path}")
            snap_only._last_image = None
            await snap_only._async_grab_frame_limited()
            print("after path lost -> image", bool(snap_only._last_image))

            state["state"]["mode"] = "idle"
            sent.clear()
            print("idle -> image kept:", bool(await snap_only.async_camera_image()))
            await snap_only._async_ensure_stream()
            print("idle -> stream_source", await live.stream_source(), "commands", sent)

            state["state"]["mode"] = "cook"
            broken = make(down, False)
            await broken._async_grab_frame_limited()
            print("relay down grab -> image", broken._last_image)
        await http.request("DELETE", "http://127.0.0.1:9997/v3/config/paths/delete/anova_oven_dev1")


asyncio.run(main())
