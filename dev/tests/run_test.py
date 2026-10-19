import json, sys, types
from datetime import datetime, timezone
from unittest.mock import patch
sys.path.insert(0, sys.argv[1])  # custom_components dir
from anova_ovens import coordinator as co, sensor, binary_sensor, climate
state = json.load(open(sys.argv[2]))
fake_now = datetime(2026, 10, 19, 2, 25, 38, tzinfo=timezone.utc)  # 180 s into timer
with patch.object(co.dt_util, "utcnow", return_value=fake_now):
    co._normalize_v2_state(state)
for d in sensor.SENSOR_DESCRIPTIONS:
    avail = d.available_fn(state) if d.available_fn else True
    val = d.value_fn(state) if avail else "unavailable"
    attrs = d.extra_attrs_fn(state) if d.extra_attrs_fn and avail else {}
    print(f"sensor {d.key:20} {val!s:22} {attrs}")
for d in binary_sensor.BINARY_SENSOR_DESCRIPTIONS if hasattr(binary_sensor,'BINARY_SENSOR_DESCRIPTIONS') else []:
    print(f"binary {d.key:20} {d.is_on_fn(state)}")
fake = types.SimpleNamespace(get_device_state=lambda _id: state, get_device_info=lambda _id: {"type": "oven_v2"})
c = object.__new__(climate.AnovaOvenClimate); c.coordinator = fake; c._device_id = "x"
print("climate", c.hvac_mode, c.hvac_action, "current", c.current_temperature, "target", c.target_temperature)
print("lamp", state["nodes"]["lamp"])
