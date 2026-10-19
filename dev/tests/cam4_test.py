import asyncio, sys, types
from unittest.mock import patch
sys.path.insert(0, sys.argv[1])
from anova_precision_oven import camera
from homeassistant.components.camera.webrtc import DATA_WEBRTC_PROVIDERS

sent = []
REPLY = {"payload": {"status": "ok", "data": {"webRTCPlayback": {"url": "https://cf.example/abc/webRTC/play"}}}}
async def send_app_command(dev, cmd, payload=None):
    sent.append(cmd); return REPLY if cmd.endswith("START_LIVE_STREAM") else {"payload": {"status": "ok"}}
state = {"state": {"mode": "idle"}}
coord = types.SimpleNamespace(app_session=object(), get_device_state=lambda d: state, get_device_info=lambda d: {"type": "oven_v2"},
    send_app_command=send_app_command, devices={"dev1": {}}, last_update_success=True)
class FakeGo2rtc:
    def async_is_supported(self, src): return src.partition(":")[0] == "webrtc"

async def main():
    with patch.object(camera, "async_track_time_interval", lambda h, f, d: (lambda: None)), patch.object(camera, "STREAM_WARMUP_SECONDS", 0):
        cam = camera.AnovaOvenCamera.__new__(camera.AnovaOvenCamera)
        camera.AnovaOvenCamera.__init__(cam, coord, "dev1")
        cam.hass = types.SimpleNamespace(data={DATA_WEBRTC_PROVIDERS: [FakeGo2rtc()]})
        await cam.async_refresh_providers(write_state=False)   # what HA runs when the entity is added
        print("oven idle at startup -> linked to go2rtc:", isinstance(cam._webrtc_provider, FakeGo2rtc), "| commands sent:", sent)
        print("WebRTC live view offered to frontend:", [str(t) for t in cam.camera_capabilities.frontend_stream_types])
        print("real call while idle ->", await cam.stream_source())
        state["state"]["mode"] = "cook"
        print("real call while cooking ->", await cam.stream_source(), "| commands:", sent)
asyncio.run(main())
