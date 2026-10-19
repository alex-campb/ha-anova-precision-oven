"""Live ICE check against a real WHEP URL, using ice_probe.py only.

Posts a minimal receive-only offer, runs the same ICE checks the camera
diagnostics run, then releases the session. Needs only aiohttp.

    python3 ice_check.py ../../custom_components <WHEP URL>
"""
import asyncio
import importlib.util
import secrets
import sys
from urllib.parse import urljoin

import aiohttp

spec = importlib.util.spec_from_file_location(
    "ice_probe", f"{sys.argv[1]}/anova_precision_oven/ice_probe.py"
)
ice_probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ice_probe)


def offer(ufrag: str) -> str:
    fingerprint = ":".join(f"{b:02X}" for b in secrets.token_bytes(32))
    # Same offer as camera._probe_offer (Cloudflare answers 406 to some
    # smaller offers)
    return "\r\n".join([
        "v=0", "o=- 0 0 IN IP4 127.0.0.1", "s=-", "t=0 0", "a=group:BUNDLE 0 1",
        f"a=ice-ufrag:{ufrag}", f"a=ice-pwd:{secrets.token_hex(12)}",
        f"a=fingerprint:sha-256 {fingerprint}", "a=setup:actpass",
        "m=video 9 UDP/TLS/RTP/SAVPF 96 97 98", "c=IN IP4 0.0.0.0", "a=mid:0",
        "a=recvonly", "a=rtcp-mux", "a=rtpmap:96 H264/90000",
        "a=fmtp:96 level-asymmetry-allowed=1;packetization-mode=1;profile-level-id=42e01f",
        "a=rtpmap:97 VP8/90000", "a=rtpmap:98 H265/90000",
        "m=audio 9 UDP/TLS/RTP/SAVPF 111", "c=IN IP4 0.0.0.0", "a=mid:1",
        "a=recvonly", "a=rtcp-mux", "a=rtpmap:111 opus/48000/2",
    ]) + "\r\n"


async def main() -> None:
    url = sys.argv[2]
    ufrag = secrets.token_hex(4)
    async with aiohttp.ClientSession() as http:
        async with http.post(
            url, data=offer(ufrag), headers={"Content-Type": "application/sdp"}
        ) as resp:
            print("HTTP", resp.status)
            body = await resp.text()
            location = resp.headers.get("Location")
        try:
            if resp.status == 201:
                print(await ice_probe.async_check_answer(body, ufrag))
                # Wrong password must not get a success reply
                bad = body.replace("a=ice-pwd:", "a=ice-pwd:x")
                print("bad pwd:", await ice_probe.async_check_answer(bad, ufrag))
        finally:
            if location:
                async with http.delete(urljoin(url, location)):
                    pass
    print(await ice_probe.async_public_stun_check())


asyncio.run(main())
