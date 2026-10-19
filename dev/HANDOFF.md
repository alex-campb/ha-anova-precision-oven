# Handoff: Anova Precision Oven v2 integration for Home Assistant

## Goal
The user owns an **Anova Precision Oven v2** (`oven_v2`) and uses a custom Home Assistant integration: [wrongdoug/ha-anova-precision-oven](https://github.com/wrongdoug/ha-anova-precision-oven), v1.3.0, domain `anova_precision_oven`. The work so far:
1. Fix the integration for v2 ovens without changing v1 behaviour (the user requires this).
2. Add lamp control.
3. Add a camera snapshot. This is unfinished.

## Where the code is
- **Fork:** [alex-campb/ha-anova-precision-oven](https://github.com/alex-campb/ha-anova-precision-oven).
- **Branch:** `fix-oven-v2-cook-payload`, on top of `830fd13` (the original v1.3.0). The fork's `main` is still the original v1.3.0.
- **Commit identity:** `gurulives` / `gurulives.ac@gmail.com`.
- **Installed via HACS:** the fork is a HACS custom repository (HACS ID `1425101408`), installed at version `fix-oven-v2-cook-payload`. wrongdoug's HACS entry (ID `1215505472`) was removed.
- **HACS caveat:** HACS shows a "pending update" for the fork, because it compares the branch against the fork's `main`. Accepting it would install the original v1.3.0, so it must not be accepted.
- **Update workflow:** commit and push to the fork, then `ha_manage_hacs update_information` (ID `1425101408`), then `ha_manage_hacs download` with version `fix-oven-v2-cook-payload`, then `ha_restart`. Restarts take about 4 minutes. The restart call returns a 502 because Home Assistant drops the connection as it goes down.
- **Tests:** see `dev/tests/README.md`.

## User's Home Assistant
- **Version:** Home Assistant OS, HA **2026.10.2**, Python 3.14. HA pins `av==19.0.0` and `go2rtc-client==0.4.0`.
- **Access:** the HA MCP tools (`ha_*`) can read state, logs and diagnostics, call services, manage HACS and restart HA.
- **Integration config entry:** `01M4T15BVGMZFZJ9Z4C1W6J8FV`. The oven's device ID is `01234e588840907201`; its entities are prefixed `anova_precision_cooker` (for example `camera.kitchen_anova_precision_cooker_camera`), because Anova's cloud names the oven "Anova Precision Cooker".
- **App login:** configured through the integration's options, with an email and password account. The user's password is not stored; only the Firebase refresh token is.
- **Logging:** the integration's log level is back to the default.
- **Wrong reference version:** an earlier local test environment had HA 2026.2. For HA internals, check the real 2026.10.2 source (`git clone --branch 2026.10.2` of home-assistant/core, `homeassistant/components/go2rtc` and `camera`).

## Findings, all verified on the user's oven
**v2 commands:**
- **Cook start:** v2 stages wrap their settings in `do: {...}`, use `exhaustVent: {"state": ...}`, and the top-level `type` is `"oven_v2"`. Source: Anova's official reference client, [anova-culinary/developer-project-wifi](https://github.com/anova-culinary/developer-project-wifi).

**v2 state (`EVENT_APO_STATE`):**
- **Where things are:** the cook is nested under `state.cook`. The timer reports `startedAtTimestamp` plus `initial`, with no `current`. Fan speed is a word (`"max"`, `"mid"`). Temperatures are in Celsius only. The light is reported as **`doorLamp`**; `cavityLamp` never changes.
- **Normalization:** `coordinator._normalize_v2_state` maps all of this onto the v1 field layout, so the v1 entities work unchanged.

**Command permissions:**

| How the command is sent | Result |
|---|---|
| Unknown command name, either connection | `Unknown command type` |
| `CMD_APO_SET_LAMP`, `CMD_APO_SET_LAMP_PREFERENCE` or `CMD_APO_START_LIVE_STREAM` with the Personal Access Token | `unauthorized` |
| The same commands over the **app login** (Firebase sign-in, WebSocket subprotocol `ANOVA_V2`, `platform=android`) | accepted |

- **Lamp format:** `CMD_APO_SET_LAMP {"on": bool}` over the app login works; the user confirmed it physically.

**Live stream:** `CMD_APO_START_LIVE_STREAM` returns `srtPlayback` (`srt://live.cloudflare.com:778` with a stream ID and passphrase) and `webRTCPlayback.url` (a Cloudflare Stream WHEP URL). The URL is stable for this oven. The app re-sends the start command about every 50 seconds to keep the stream alive.
- **The oven's `cavityCamera.streaming` flag stays `false`** even while the app shows live video, so it's unreliable.

## Status

**Working:**
- **v2 sensors:** all populate correctly, including the probe.
- **Climate entity:** v2 ovens get `dry` / `wet` presets, and the setpoint follows the active bulb. Changing the temperature mid-cook re-sends the running cook from its active stage with only the setpoint or mode changed, and the remaining timer is kept.
- **Lamp switch:** uses the app login when configured, otherwise the original PAT path.
- **Admin service:** `anova_precision_oven.send_command` (fields `command`, `payload`, `cooker_id`, `use_app_login`) returns Anova's reply.
- **Reply logging:** RESPONSE messages are recorded, and diagnostics hide credentials and stream details.
- **v1 unchanged:** `dev/tests/v1_check.sh` runs the new code on the same v1 states as the original and gives identical entity values and commands.

**Camera, not working:**
- **Design:** the camera is created only for v2 ovens with the app login. `stream_source()` returns `webrtc:<WHEP URL>` for HA's go2rtc. Snapshots come from a background grab: go2rtc's snapshot API with a 15-second limit, then ffmpeg reading go2rtc's local RTSP feed `rtsp://127.0.0.1:18554/<identifier>` with up to 30 seconds. The identifier comes from HA's `go2rtc.util.get_camera_identifier`, which is `anova_precision_oven_<unique_id>` in 2026.x. The whole grab is capped at 60 seconds. Stream keep-alive runs every 50 seconds, with an idle stop after 10 minutes.
- **Approaches that failed:**
  - **SRT via ffmpeg:** connects, but no video ever arrives. Cloudflare Stream appears to serve WebRTC-ingested streams only over WebRTC.
  - **aiortc:** no release (latest 1.15.0) supports `av` 19, so it can't install on HA 2026.10. It has been removed.
- **Latest diagnostic:**
  - **go2rtc's own state:** go2rtc's `/api/streams` shows the WebRTC producer with **no SDP answer, no remote address and 0 bytes received**.
  - **Direct WHEP check from HA:** a request sent straight to the same URL gets **HTTP 201**, with an answer offering H264, H265 and VP8 video plus Opus audio.
  - **Conclusion:** Cloudflare and the network are fine, and the formats match. **go2rtc's WHEP client is failing silently**: go2rtc logs dial errors only at trace level.
- **Correction (2026-10-19):** the "no SDP answer" reading above was wrong. go2rtc's WHEP client never stores an SDP, so that field is always empty. Its `medias` come from the applied answer, so the WHEP POST and answer **succeeded**. `remote_addr` is set only when ICE selects a candidate pair, so the failure is **ICE connectivity** between go2rtc and Cloudflare. go2rtc logs nothing for this, because `Conn.Start()` returns nil when the connection closes.
- **Verified from the user's PC on the same LAN (192.168.0.222):** Cloudflare's answer is ICE-lite, with one candidate `udp 141.101.90.0:1473 host`. aiortc connects with **and without** STUN and decodes 600×950 frames within about 1 to 4 seconds.
- **App behaviour (decompiled APK, `com.anovaculinary.anovaoven`):**
  - Offer: video-only and receive-only, with `max-bundle`.
  - ICE: three STUN servers (Cloudflare and two Google). The app waits a fixed 3 seconds to gather candidates and does no trickle ICE or PATCH.
  - WHEP: up to 3 POSTs, retrying on any non-201 with 500 ms backoff, doubled each time.
  - Keep-alive: every 60 seconds.
- **ICE diagnostics (`ice_probe.py`):** when no frame arrives, the direct WHEP probe now also:
  - logs the answer's ICE-lite flag and candidates;
  - sends an authenticated ICE Binding check from the HA host to each UDP IPv4 candidate, while the probe session is open;
  - sends a plain STUN request to `stun.cloudflare.com:3478`.
  - **Reading the result:** a successful ICE check means the HA host can reach Cloudflare's media port over UDP, which points at go2rtc and pion. No reply means the problem is UDP from the host. Note that Cloudflare answers 406 to some minimal offers.
- **Temporary diagnostic code:** `camera.py` still contains `_async_go2rtc_stream_info` (reads go2rtc's stream status through HA's private `_rest_client`) and `_async_whep_probe` (posts a placeholder offer to Cloudflare and runs `ice_probe`). These and `ice_probe.py` should be removed once the camera is resolved.

## Next steps offered to the user, none chosen yet
1. **Recommended:** the user adds `go2rtc: debug_ui: true` to `configuration.yaml` and restarts. That exposes go2rtc's UI at `http://<HA>:11984`, which shows the exact WHEP or ICE error. The agent can't edit `configuration.yaml`.
2. **Wait for aiortc:** once a release supports av 19, switch to direct WHEP with aiortc. A working client (`whep.py`) existed in commit `032dbf9` and was tested end to end against a local WHEP server.
3. **Stop here:** remove the diagnostics and mark the camera as experimental, or remove it.

## Other notes
- **Pre-existing bug:** the original coordinator leaves its WebSocket task running at HA shutdown ("still running after final writes shutdown stage"). This was flagged but not fixed.
- **Stop hook:** the session's stop hook requires everything to be committed and pushed.
