"""Dump every entity value and cook command for a v1 oven; run once per code version."""
import asyncio, copy, json, sys, types, uuid
from unittest.mock import patch
sys.path.insert(0, sys.argv[1])
from anova_ovens import coordinator as co, sensor, binary_sensor, climate, switch

base = json.load(open(sys.argv[2]))
out = {}

async def run(label, state):
    msg = json.dumps({"command": "EVENT_APO_STATE", "payload": {"cookerId": "dev1", "type": "oven_v1", "state": copy.deepcopy(state)}})
    hass = types.SimpleNamespace()
    c = object.__new__(co.AnovaOvenCoordinator)
    c.devices = {"dev1": {"info": {"cookerId": "dev1", "type": "oven_v1", "name": "Oven"}}}
    c.async_set_updated_data = lambda d: None
    await c._handle_message(msg)
    st = c.devices["dev1"]["state"]
    sent = []
    async def send_command(dev, cmd, payload=None, request_id=None): sent.append((cmd, payload))
    async def refresh(): pass
    c.send_command = send_command; c.async_request_refresh = refresh
    r = out.setdefault(label, {})
    for d in sensor.SENSOR_DESCRIPTIONS:
        avail = d.available_fn(st) if d.available_fn else True
        r["sensor." + d.key] = [avail, d.value_fn(st) if avail else None, d.extra_attrs_fn(st) if d.extra_attrs_fn else None]
    for d in binary_sensor.BINARY_SENSOR_DESCRIPTIONS:
        r["binary." + d.key] = d.is_on_fn(st)
    cl = object.__new__(climate.AnovaOvenClimate); cl.coordinator = c; cl._device_id = "dev1"; cl._idle_preset = "dry"
    cl.async_write_ha_state = lambda: None
    feats = cl.supported_features
    r["climate"] = dict(mode=str(cl.hvac_mode), action=str(cl.hvac_action), cur=cl.current_temperature,
        target=cl.target_temperature, attrs=cl.extra_state_attributes, max=cl.max_temp,
        features=int(feats), presets=getattr(cl, "preset_modes", None), preset=getattr(cl, "preset_mode", None))
    c.app_session = None
    sw = object.__new__(switch.AnovaOvenLampSwitch); sw.coordinator = c; sw._device_id = "dev1"
    r["switch.lamp"] = sw.is_on
    ids = iter(f"uuid-{i}" for i in range(100))
    with patch.object(uuid, "uuid4", lambda: next(ids)):
        await cl.async_set_temperature(temperature=200)
        await cl.async_turn_on()
        await cl.async_turn_off()
        await sw.async_turn_on()
    r["commands"] = sent

base_wet = copy.deepcopy(base)
tb = base_wet["nodes"]["temperatureBulbs"]
tb["mode"] = "wet"; tb.setdefault("wet", {})["setpoint"] = {"celsius": 55, "fahrenheit": 131}
idle = copy.deepcopy(base); idle["state"]["mode"] = "idle"
asyncio.run(run("v1_dry_cooking", base)); asyncio.run(run("v1_wet_cooking", base_wet)); asyncio.run(run("v1_idle", idle))
json.dump(out, open(sys.argv[3], "w"), indent=1, sort_keys=True, default=str)
