import asyncio, json, sys, types
sys.path.insert(0, sys.argv[1])
from homeassistant.core import HomeAssistant, ServiceCall
from anova_ovens import coordinator as co
import anova_ovens as integ

class FakeWS:
    """Replies to each command the way Anova's server does."""
    def __init__(self, coord): self.coord = coord
    async def send(self, raw):
        msg = json.loads(raw)
        ok = msg["command"] != "CMD_APO_SET_LAMP"
        reply = {"command": "RESPONSE", "requestId": msg["requestId"],
                 "payload": {"status": "ok"} if ok else {"status": "error", "error": "UNIMPLEMENTED: Method not found"}}
        asyncio.get_running_loop().call_later(0.05, lambda: asyncio.ensure_future(self.coord._handle_message(json.dumps(reply))))

async def main():
    c = object.__new__(co.AnovaOvenCoordinator)
    c.devices = {"dev1": {"info": {"type": "oven_v2"}, "state": {}}}
    c._pending, c._sent_commands = {}, {}
    from collections import deque; c.recent_responses = deque(maxlen=20)
    c.websocket = FakeWS(c)
    entry = types.SimpleNamespace(runtime_data=types.SimpleNamespace(coordinator=c))
    hass = types.SimpleNamespace(config_entries=types.SimpleNamespace(async_loaded_entries=lambda d: [entry]))
    for cmd, payload in [("CMD_APO_SET_LAMP", {"on": True}), ("CMD_APO_START_LIVE_STREAM", None)]:
        call = ServiceCall(hass, "anova_ovens", "send_command", integ.SEND_COMMAND_SCHEMA({"command": cmd, **({"payload": payload} if payload else {})}))
        print(cmd, "->", (await integ._async_send_command(call))["response"]["payload"])
    print("recorded:", [(r["command"], r["response"]["payload"]["status"]) for r in c.recent_responses])
    print("schema rejects non-oven command:", end=" ")
    try: integ.SEND_COMMAND_SCHEMA({"command": "rm -rf"}); print("NO")
    except Exception: print("yes")
asyncio.run(main())
