import asyncio, sys, types
sys.path.insert(0, sys.argv[1])
import aiohttp
from aiohttp import web
from aiortc import RTCPeerConnection, RTCSessionDescription
from aiortc.contrib.media import MediaPlayer
from anova_precision_oven import camera
calls = []
async def whep(request):
    offer = await request.text()
    if not calls: calls.append("409"); return web.Response(status=409, text="stream not live")
    pc = RTCPeerConnection(); player = MediaPlayer("testsrc=size=320x240:rate=10", format="lavfi")
    await pc.setRemoteDescription(RTCSessionDescription(sdp=offer, type="offer"))
    for t in pc.getTransceivers():
        if t.kind == "video": pc.addTrack(player.video)
    await pc.setLocalDescription(await pc.createAnswer()); calls.append("201")
    return web.Response(status=201, text=pc.localDescription.sdp, headers={"Location": "/s/1"})
async def delete(request): calls.append("DELETE"); return web.Response()
async def main():
    app = web.Application(); app.router.add_post("/play", whep); app.router.add_delete("/s/1", delete)
    r = web.AppRunner(app); await r.setup(); await web.TCPSite(r, "127.0.0.1", 8898).start()
    async with aiohttp.ClientSession() as s:
        with __import__("unittest.mock").mock.patch.object(camera, "async_get_clientsession", lambda h: s):
            print("not live ->", await camera._async_whep_probe(None, "http://127.0.0.1:8898/play"))
            print("live     ->", await camera._async_whep_probe(None, "http://127.0.0.1:8898/play"))
            print("down     ->", await camera._async_whep_probe(None, "http://127.0.0.1:1/play"))
    print("server saw:", calls); await r.cleanup()
asyncio.run(main())
