import asyncio, json, sys, types
from datetime import datetime, timezone
from unittest.mock import patch
sys.path.insert(0, sys.argv[1])
from anova_precision_oven import coordinator as co, climate, binary_sensor
from homeassistant.components.climate import HVACMode

state = json.load(open(sys.argv[2]))
state["cook"]["activeStageIndex"] = 0
state["nodes"]["temperatureProbe"] = {"connected": True, "current": {"celsius": 39.54}}
now = datetime(2026, 10, 19, 2, 25, 38, tzinfo=timezone.utc)  # 180 s into 600 s timer
sent = []
async def send_command(dev, cmd, payload=None): sent.append((cmd, payload))
async def refresh(): pass
fake = types.SimpleNamespace(get_device_state=lambda _id: state,
    get_device_info=lambda _id: {"type": "oven_v2"},
    send_command=send_command, async_request_refresh=refresh)
c = object.__new__(climate.AnovaOvenClimate)
c.coordinator = fake; c._device_id = "dev123"; c._idle_preset = "dry"
c.async_write_ha_state = lambda: None

async def main():
    with patch.object(co.dt_util, "utcnow", return_value=now), patch.object(climate.dt_util, "utcnow", return_value=now):
        co._normalize_v2_state(state)
        print("cooking: preset", c.preset_mode, "max", c.max_temp, "target", c.target_temperature)
        await c.async_set_temperature(temperature=55)
        await c.async_set_temperature(temperature=120)   # too hot for wet -> rejected
        await c.async_set_preset_mode("dry")
    print("commands sent:", len(sent))
    cmd, p = sent[0]
    st = p["stages"][0]
    print(cmd, "bulbs", st["do"]["temperatureBulbs"], "timer", st["do"]["timer"], "steam", st["do"]["steamGenerators"]["relativeHumidity"], "fan", st["do"]["fan"], "exit kept", "exit" in st, "type", p["type"])
    print("preset->dry bulbs", sent[1][1]["stages"][0]["do"]["temperatureBulbs"])
    # idle oven: wet preset then turn on -> fresh wet cook
    state["state"]["mode"] = "idle"; sent.clear()
    await c.async_set_preset_mode("wet")
    print("idle: preset", c.preset_mode, "max", c.max_temp, "sent", len(sent))
    await c.async_turn_on()
    d = sent[0][1]["stages"][0]["do"]
    print("fresh wet cook bulbs", d["temperatureBulbs"], "steam", d["steamGenerators"]["relativeHumidity"])
    print("lamp (v2 doorLamp via normalization):", state["nodes"].get("lamp"))
asyncio.run(main())
