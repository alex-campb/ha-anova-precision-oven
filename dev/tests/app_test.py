import asyncio, json, sys, types
from urllib.parse import parse_qs, urlparse
sys.path.insert(0, sys.argv[1])
import websockets
from anova_precision_oven import app_session as ap, coordinator as co, switch
import anova_precision_oven as integ

seen = {}
async def server(ws):
    q = parse_qs(urlparse(ws.request.path).query)
    seen.update(token=q["token"][0], platform=q["platform"][0], subprotocol=ws.subprotocol)
    await ws.send(json.dumps({"command": "EVENT_APO_WIFI_LIST", "payload": []}))  # noise first
    msg = json.loads(await ws.recv()); seen["msg"] = msg
    await ws.send(json.dumps({"command": "RESPONSE", "requestId": msg["requestId"], "payload": {"status": "ok"}}))

class Resp:
    def __init__(s, status, data): s.status, s._d = status, data
    async def json(s, content_type=None): return s._d
    async def __aenter__(s): return s
    async def __aexit__(s, *a): pass
class FakeHttp:
    calls = []
    def post(self, url, **kw):
        self.calls.append(url)
        if "signInWithPassword" in url:
            ok = kw["json"]["password"] == "right"
            return Resp(200 if ok else 400, {"refreshToken": "RT1"} if ok else {"error": {"message": "INVALID_PASSWORD"}})
        return Resp(200, {"id_token": "ID1", "refresh_token": "RT2", "expires_in": "3600"})

async def main():
    srv = await websockets.serve(server, "127.0.0.1", 8765, subprotocols=["ANOVA_V2"])
    ap.WS_ENDPOINT = "ws://127.0.0.1:8765"
    http = FakeHttp()
    print("sign-in good:", await ap.async_sign_in(http, "a@b.c", "right"))
    try: await ap.async_sign_in(http, "a@b.c", "wrong")
    except ap.AppAuthError as e: print("sign-in bad:", e)

    c = object.__new__(co.AnovaOvenCoordinator)
    from collections import deque; c.recent_responses = deque(maxlen=20)
    c.app_session = ap.AnovaAppSession(http, None, "RT1")
    saved = []; c.on_app_token_refreshed = saved.append
    sw = object.__new__(switch.AnovaOvenLampSwitch); sw.coordinator = c; sw._device_id = "dev1"
    await sw.async_turn_on()
    print("server saw: subprotocol", seen["subprotocol"], "platform", seen["platform"], "token", seen["token"])
    print("command:", seen["msg"]["command"], seen["msg"]["payload"])
    print("rotated refresh token saved:", saved)
    await sw.async_turn_off(); print("2nd command reused id token, refresh calls:", sum("securetoken" in u for u in http.calls))

    # without app login: original PAT path
    c2 = object.__new__(co.AnovaOvenCoordinator); c2.app_session = None
    sent = []
    async def send_command(d, cmd, p=None, request_id=None): sent.append((cmd, p))
    async def refresh(): pass
    c2.send_command = send_command; c2.async_request_refresh = refresh
    sw2 = object.__new__(switch.AnovaOvenLampSwitch); sw2.coordinator = c2; sw2._device_id = "dev1"
    await sw2.async_turn_on(); print("no app login -> PAT path:", sent)

    # reload rule
    reloads = []
    hass = types.SimpleNamespace(config_entries=types.SimpleNamespace(async_reload=lambda eid: reloads.append(eid) or asyncio.sleep(0)))
    def entry(tok, sess, options=None, setup_options=None): return types.SimpleNamespace(entry_id="e", data={"app_refresh_token": tok} if tok else {}, options=options or {}, runtime_data=types.SimpleNamespace(coordinator=types.SimpleNamespace(app_session=sess), options=setup_options or {}))
    s = types.SimpleNamespace(refresh_token="RT2")
    relay = {"camera_relay": "abcd-anova-camera-relay", "camera_live_view": False}
    for label, e in [("rotation", entry("RT2", s)), ("new login", entry("RT9", None)), ("removed", entry(None, s)), ("unchanged none", entry(None, None)),
                     ("camera relay set", entry("RT2", s, relay)), ("camera options unchanged", entry("RT2", s, relay, dict(relay)))]:
        reloads.clear(); await integ._async_update_listener(hass, e); print(f"reload on {label}:", bool(reloads))
    srv.close()
asyncio.run(main())
