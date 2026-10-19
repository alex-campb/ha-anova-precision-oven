import asyncio, logging, sys, time, types
from unittest.mock import patch
sys.path.insert(0, sys.argv[1])
from anova_precision_oven import camera
logging.basicConfig(level=logging.DEBUG, format="%(asctime)s LOG %(levelname)s %(name)s %(message)s")
for n in ("asyncio",): logging.getLogger(n).setLevel(logging.WARNING)
state = {"state": {"mode": "cook"}}
coord = types.SimpleNamespace(app_session=object(), get_device_state=lambda d: state, get_device_info=lambda d: {"type": "oven_v2"}, devices={"dev1": {}}, last_update_success=True)
class Hass:
    def async_create_background_task(self, coro, name): return asyncio.ensure_future(coro)
class TimeoutProvider:
    async def async_get_image(self, cam, width=None, height=None): raise TimeoutError()
async def silent(reader, writer): await asyncio.sleep(3600)   # accepts RTSP, never answers
async def main():
    srv = await asyncio.start_server(silent, "127.0.0.1", 18554)
    with patch.object(camera, "get_ffmpeg_manager", lambda h: types.SimpleNamespace(binary="ffmpeg")):
        cam = camera.AnovaOvenCamera.__new__(camera.AnovaOvenCamera)
        camera.AnovaOvenCamera.__init__(cam, coord, "dev1"); cam.hass = Hass(); cam.entity_id = "camera.oven"
        cam._webrtc_provider = TimeoutProvider()
        t = time.monotonic(); await cam.async_camera_image()
        try:
            await asyncio.wait_for(asyncio.shield(cam._grab_task), 90)
        except TimeoutError:
            print(f"!! grab still running after {time.monotonic()-t:.0f}s, step={cam._grab_step}")
        print(f"grab ended after {time.monotonic()-t:.0f}s, done={cam._grab_task.done()}")
    srv.close()
asyncio.run(main())
