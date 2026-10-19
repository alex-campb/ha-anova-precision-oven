# Development tests

Ad-hoc test scripts used while adding oven_v2 support. They exercise the
integration's code directly with stand-ins (no running Home Assistant), using
a locally installed `homeassistant` package for imports.

Each script takes the path to this repo's `custom_components` folder as its
first argument. Run them from this folder.

```sh
C=../../custom_components
```

## Setup

```sh
pip install homeassistant==2026.2.3 --no-deps   # any recent version; tested with 2026.2.3
# then install whatever imports are missing (voluptuous, aiohttp, propcache,
# ha-ffmpeg, PyTurboJPEG, go2rtc-client, websockets, ...)
pip install aiortc   # only for probe_test.py / repro helpers that publish WebRTC
```

Requires `ffmpeg` on PATH for the camera tests. A "Unable to locate turbojpeg
library" traceback on import is harmless noise from HA's camera component.

## Scripts

| Script | What it checks | Run |
|---|---|---|
| `run_test.py` | Every sensor, binary sensor and climate value computed from a real oven_v2 state (`fixtures/v2_state.json`, captured from the user's oven) after `_normalize_v2_state` | `python3 run_test.py $C fixtures/v2_state.json` |
| `run_climate.py` | v2 climate: wet/dry presets, wet max 100 °C, temperature change mid-cook keeps steam/fan/timer remainder, fresh wet cook payload | `python3 run_climate.py $C fixtures/v2_state.json` |
| `v1_check.sh` (uses `v1_compare.py`) | v1 behaviour unchanged: every entity value and command for v1 states, diffed against outputs of the original v1.3.0 code (`fixtures/v1_baseline_*`). Expect 6 differing lines per case (`null` vs `{}` extra attributes, same in HA) | `sh v1_check.sh $C` |
| `svc_test.py` | `send_command` service and RESPONSE recording, with a fake server | `python3 svc_test.py $C` |
| `app_test.py` | App login: Firebase sign-in (fake), app WebSocket handshake (`ANOVA_V2`, `platform=android`) against a local server, token rotation saved, lamp switch with and without app login, reload rules | `python3 app_test.py $C` |
| `cam4_test.py` | Camera links to go2rtc via HA's real provider-selection code while the oven is idle, without starting the oven stream | `python3 cam4_test.py $C` |
| `cam5_test.py` | Snapshot paths: go2rtc returns a frame; go2rtc fails and ffmpeg fallback logs its error; no go2rtc | `python3 cam5_test.py $C` |
| `hang_test.py` | A hung go2rtc call and a hung grab are cut off and logged | `python3 hang_test.py $C` |
| `repro.py` | Regression for the silent grab abort: ffmpeg against an RTSP server that accepts but never answers must end with a warning after 30 s | `python3 repro.py $C` |
| `info_test.py` | Summary of go2rtc's `/api/streams` producers (codecs, answer, bytes) | `python3 info_test.py $C` |
| `probe_test.py` | Direct WHEP probe against a local aiortc WHEP server: 409 / 201 + codecs + ICE checks / unreachable, session released | `python3 probe_test.py $C` |
| `ice_check.py` | Live: posts the camera's probe offer to a real WHEP URL and runs `ice_probe`'s ICE check (and a wrong-password check, which must get no reply) plus a plain STUN check. Needs only aiohttp | `python3 ice_check.py $C <WHEP URL>` |
| `whep_probe.py` | Standalone: posts an aiortc offer to the oven's Cloudflare WHEP URL and prints the answer's codecs. Needs network access to `cloudflarestream.com` and a live stream; the URL is hard-coded for the user's oven | `python3 whep_probe.py` |
