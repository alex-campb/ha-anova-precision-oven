import asyncio, sys, types
sys.path.insert(0, sys.argv[1])
from anova_precision_oven import camera
SDP = "v=0\r\no=- 1 2 IN IP4 0.0.0.0\r\ns=-\r\nt=0 0\r\nm=video 9 UDP/TLS/RTP/SAVPF 96\r\na=rtpmap:96 VP8/90000\r\na=sendonly\r\nm=audio 0 UDP/TLS/RTP/SAVPF 111\r\na=rtpmap:111 opus/48000/2\r\n"
INFO = {"producers": [{"format_name": "webrtc", "protocol": "http", "remote_addr": "104.18.1.1:3478 host", "bytes_recv": 0,
        "medias": ["video, recvonly, VP8"], "sdp": SDP}], "consumers": [{"format_name": "rtsp"}]}
class Resp:
    async def json(self): return INFO
class C:
    async def request(self, m, p, params=None): self.params = params; return Resp()
client = C()
provider = types.SimpleNamespace(_rest_client=types.SimpleNamespace(streams=types.SimpleNamespace(_client=client)))
cam = types.SimpleNamespace(entity_id="camera.x", unique_id="u1_camera", platform=types.SimpleNamespace(platform_name="anova_precision_oven"))
print(asyncio.run(camera._async_go2rtc_stream_info(provider, cam)), "| asked for", client.params)
broken = types.SimpleNamespace()
print("internals changed ->", asyncio.run(camera._async_go2rtc_stream_info(broken, cam)))
