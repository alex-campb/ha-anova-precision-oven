import asyncio, logging, sys, time, types
from unittest.mock import patch
sys.path.insert(0, sys.argv[1])
from anova_precision_oven import camera
logging.basicConfig(level=logging.WARNING, format="LOG %(levelname)s %(message)s")
state = {"state": {"mode": "cook"}}
coord = types.SimpleNamespace(app_session=object(), get_device_state=lambda d: state, get_device_info=lambda d: {"type": "oven_v2"}, devices={"dev1": {}}, last_update_success=True)
class Hass:
    def async_create_background_task(self, coro, name): return asyncio.ensure_future(coro)
class HangingProvider:
    async def async_get_image(self, cam, width=None, height=None): await asyncio.sleep(3600)
async def main():
    with patch.object(camera, "get_ffmpeg_manager", lambda h: types.SimpleNamespace(binary="ffmpeg")), \
         patch.object(camera, "GO2RTC_SNAPSHOT_TIMEOUT_SECONDS", 1):
        cam = camera.AnovaOvenCamera.__new__(camera.AnovaOvenCamera)
        camera.AnovaOvenCamera.__init__(cam, coord, "dev1"); cam.hass = Hass(); cam.entity_id = "camera.oven"
        cam._webrtc_provider = HangingProvider()
        t = time.monotonic(); await cam.async_camera_image(); await cam._grab_task
        print(f"hung go2rtc call -> grab finished after {time.monotonic()-t:.1f}s, task done: {cam._grab_task.done()}")
        with patch.object(camera, "GRAB_TOTAL_TIMEOUT_SECONDS", 2):
            async def stuck(): cam._grab_step = "example step"; await asyncio.sleep(3600)
            cam._async_grab_frame = stuck
            await cam.async_camera_image(); await cam._grab_task
            print("whole grab stuck -> task done:", cam._grab_task.done())
asyncio.run(main())
