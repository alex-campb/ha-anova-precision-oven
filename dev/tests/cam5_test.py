import asyncio, logging, sys, time, types
from unittest.mock import patch
sys.path.insert(0, sys.argv[1])
from anova_precision_oven import camera
logging.basicConfig(level=logging.WARNING, format="LOG %(levelname)s %(message)s")

state = {"state": {"mode": "cook"}}
coord = types.SimpleNamespace(app_session=object(), get_device_state=lambda d: state, get_device_info=lambda d: {"type": "oven_v2"},
    devices={"dev1": {}}, last_update_success=True)
class Hass:
    def async_create_background_task(self, coro, name): return asyncio.ensure_future(coro)
class Provider:
    def __init__(self, result): self.result = result
    async def async_get_image(self, cam, width=None, height=None):
        if isinstance(self.result, Exception): raise self.result
        return self.result

def make(provider):
    cam = camera.AnovaOvenCamera.__new__(camera.AnovaOvenCamera)
    camera.AnovaOvenCamera.__init__(cam, coord, "dev1"); cam.hass = Hass(); cam.entity_id = "camera.oven"
    cam._webrtc_provider = provider
    return cam

async def main():
    with patch.object(camera, "get_ffmpeg_manager", lambda h: types.SimpleNamespace(binary="ffmpeg")):
        print("use_stream_for_stills:", make(None).use_stream_for_stills)
        print("go2rtc returns frame ->", await make(Provider(b"\xff\xd8JPEG")).async_camera_image())
        t = time.monotonic(); r = await make(Provider(RuntimeError("timeout"))).async_camera_image()
        print(f"go2rtc fails -> fallback ran, returned {r} after {time.monotonic()-t:.1f}s")
        print("no go2rtc ->", await make(None).async_camera_image())
asyncio.run(main())
