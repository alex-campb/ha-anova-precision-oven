"""ICE reachability checks for the camera diagnostics (no media is sent).

Cloudflare's WHEP server is ICE-lite: it never starts connectivity checks
itself, it only answers STUN Binding requests that carry the session's
credentials. Sending one such check from Home Assistant's host shows
whether UDP to Cloudflare's media port works from there, independent of
go2rtc.
"""
from __future__ import annotations

import asyncio
import binascii
import hashlib
import hmac
import ipaddress
import re
import secrets
import socket
import struct
import time

MAGIC_COOKIE = 0x2112A442
BINDING_REQUEST = 0x0001
BINDING_SUCCESS = 0x0101
BINDING_ERROR = 0x0111
ATTR_USERNAME = 0x0006
ATTR_MESSAGE_INTEGRITY = 0x0008
ATTR_ERROR_CODE = 0x0009
ATTR_XOR_MAPPED_ADDRESS = 0x0020
ATTR_PRIORITY = 0x0024
ATTR_ICE_CONTROLLING = 0x802A
ATTR_FINGERPRINT = 0x8028
FINGERPRINT_XOR = 0x5354554E

PUBLIC_STUN_SERVER = ("stun.cloudflare.com", 3478)
CHECK_ATTEMPTS = 3
CHECK_WAIT_SECONDS = 1.5
# At most this many candidates are checked, to stay within the caller's limit
MAX_CHECKED_CANDIDATES = 3


def _attr(kind: int, value: bytes) -> bytes:
    pad = b"\x00" * (-len(value) % 4)
    return struct.pack("!HH", kind, len(value)) + value + pad


def _header(kind: int, length: int, txid: bytes) -> bytes:
    return struct.pack("!HHI", kind, length, MAGIC_COOKIE) + txid


def build_binding_request(
    txid: bytes, username: str | None = None, password: str | None = None
) -> bytes:
    """Build a STUN Binding request; with credentials, an ICE check."""
    if username is None or password is None:
        return _header(BINDING_REQUEST, 0, txid)
    body = (
        _attr(ATTR_USERNAME, username.encode())
        + _attr(ATTR_PRIORITY, struct.pack("!I", 0x6E0001FF))
        + _attr(ATTR_ICE_CONTROLLING, secrets.token_bytes(8))
    )
    # MESSAGE-INTEGRITY covers the header with a length that includes itself
    mi_input = _header(BINDING_REQUEST, len(body) + 24, txid) + body
    mac = hmac.new(password.encode(), mi_input, hashlib.sha1).digest()
    body += _attr(ATTR_MESSAGE_INTEGRITY, mac)
    # FINGERPRINT likewise covers everything before it, length including itself
    fp_input = _header(BINDING_REQUEST, len(body) + 8, txid) + body
    crc = (binascii.crc32(fp_input) ^ FINGERPRINT_XOR) & 0xFFFFFFFF
    body += _attr(ATTR_FINGERPRINT, struct.pack("!I", crc))
    return _header(BINDING_REQUEST, len(body), txid) + body


def parse_response(data: bytes, txid: bytes) -> str | None:
    """Summarise a STUN response matching txid, or None if it does not match."""
    if len(data) < 20:
        return None
    kind, length, cookie = struct.unpack("!HHI", data[:8])
    if cookie != MAGIC_COOKIE or data[8:20] != txid:
        return None
    mapped = ""
    error = ""
    pos = 20
    while pos + 4 <= min(len(data), 20 + length):
        attr, size = struct.unpack("!HH", data[pos : pos + 4])
        value = data[pos + 4 : pos + 4 + size]
        if attr == ATTR_XOR_MAPPED_ADDRESS and len(value) >= 8 and value[1] == 1:
            port = struct.unpack("!H", value[2:4])[0] ^ (MAGIC_COOKIE >> 16)
            addr = struct.unpack("!I", value[4:8])[0] ^ MAGIC_COOKIE
            mapped = f"{ipaddress.IPv4Address(addr)}:{port}"
        elif attr == ATTR_ERROR_CODE and len(value) >= 4:
            error = f"{value[2] * 100 + value[3]} {value[4:].decode(errors='replace')}"
        pos += 4 + size + (-size % 4)
    if kind == BINDING_SUCCESS:
        return f"success, mapped {mapped or '?'}"
    if kind == BINDING_ERROR:
        return f"error {error or '?'}"
    return f"type 0x{kind:04x}"


class _Receiver(asyncio.DatagramProtocol):
    def __init__(self) -> None:
        self.queue: asyncio.Queue[bytes] = asyncio.Queue()

    def datagram_received(self, data: bytes, addr) -> None:
        self.queue.put_nowait(data)


async def _async_send_check(
    host: str, port: int, username: str | None = None, password: str | None = None
) -> str:
    """Send a Binding request (retrying) and describe the reply."""
    loop = asyncio.get_running_loop()
    try:
        transport, proto = await loop.create_datagram_endpoint(
            _Receiver, remote_addr=(host, port), family=socket.AF_INET
        )
    except OSError as err:
        return f"socket failed ({err})"
    try:
        local = "%s:%s" % transport.get_extra_info("sockname")[:2]
        for attempt in range(1, CHECK_ATTEMPTS + 1):
            txid = secrets.token_bytes(12)
            started = time.monotonic()
            transport.sendto(build_binding_request(txid, username, password))
            deadline = started + CHECK_WAIT_SECONDS
            while (left := deadline - time.monotonic()) > 0:
                try:
                    data = await asyncio.wait_for(proto.queue.get(), left)
                except TimeoutError:
                    break
                if summary := parse_response(data, txid):
                    ms = (time.monotonic() - started) * 1000
                    return f"{summary} in {ms:.0f}ms (from {local}, try {attempt})"
        return f"no reply after {CHECK_ATTEMPTS} tries (from {local})"
    finally:
        transport.close()


def parse_ice(sdp: str) -> tuple[str | None, str | None, list[tuple[str, str, int, str]]]:
    """Return (ufrag, pwd, candidates as (proto, address, port, type))."""
    ufrag = re.search(r"(?m)^a=ice-ufrag:(\S+)", sdp)
    pwd = re.search(r"(?m)^a=ice-pwd:(\S+)", sdp)
    candidates = []
    seen = set()
    for proto, addr, port, typ in re.findall(
        r"(?m)^a=candidate:\S+ \d+ (\S+) \d+ (\S+) (\d+) typ (\S+)", sdp
    ):
        key = (proto.lower(), addr, int(port), typ)
        if key not in seen:
            seen.add(key)
            candidates.append(key)
    return (
        ufrag.group(1) if ufrag else None,
        pwd.group(1) if pwd else None,
        candidates,
    )


async def async_check_answer(answer: str, local_ufrag: str) -> str:
    """Summarise a WHEP answer's ICE and check each UDP IPv4 candidate."""
    ufrag, pwd, candidates = parse_ice(answer)
    parts = [
        f"ice-lite={'a=ice-lite' in answer}",
        "candidates=" + (
            ", ".join(f"{p} {a}:{n} {t}" for p, a, n, t in candidates) or "none"
        ),
    ]
    if ufrag and pwd:
        checkable = []
        for proto, addr, port, _typ in candidates:
            try:
                is_v4 = ipaddress.ip_address(addr).version == 4
            except ValueError:
                is_v4 = False
            if proto == "udp" and is_v4:
                checkable.append((addr, port))
        for addr, port in checkable[:MAX_CHECKED_CANDIDATES]:
            result = await _async_send_check(addr, port, f"{ufrag}:{local_ufrag}", pwd)
            parts.append(f"ICE check {addr}:{port}: {result}")
    else:
        parts.append("no ICE credentials in answer")
    return "; ".join(parts)


async def async_public_stun_check() -> str:
    """Plain STUN to Cloudflare's public server: is outbound UDP working?"""
    host, port = PUBLIC_STUN_SERVER
    return f"STUN {host}:{port}: " + await _async_send_check(host, port)
