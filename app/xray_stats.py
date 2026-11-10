"""Minimal Xray gRPC stats client (Phase 4).

Talks to the Xray `StatsService` (`xray.app.stats.command.StatsService/
GetQueryStats`) exposed through the localhost `dokodemo-door` "api" inbound
that the config generator adds when `XRAY_STATS_ENABLED` is on.

No generated protobuf stubs: the two messages used here are tiny, so they are
encoded/decoded by hand:

    QueryStatsRequest:
        field 1 (string) pattern, e.g. "user>>>" to list user counters
        field 2 (bool)   reset      e.g. True to zero server-side counters

    QueryStatsResponse:
        field 1 (repeated message) stat
            Stat: field 1 = name (string), field 2 = value (int64)

gRPC wire framing: one DATA frame carrying `\x00` + 4-byte big-endian length
+ protobuf message. Transport is plain HTTP/2 (cleartext) to a loopback
address, via the `h2` state machine on a raw socket.
"""

from __future__ import annotations

import socket
import struct

import h2.connection
import h2.events

_GRPC_PATH = "/xray.app.stats.command.StatsService/GetQueryStats"


# ---------------------------------------------------------------------------
# Hand-rolled protobuf encode
# ---------------------------------------------------------------------------

def encode_varint(value: int) -> bytes:
    """Encode a non-negative integer as a protobuf base-128 varint."""
    out = bytearray()
    while True:
        byte = value & 0x7F
        value >>= 7
        if value:
            out.append(byte | 0x80)
        else:
            out.append(byte)
            return bytes(out)


def encode_string_field(field_no: int, value: str) -> bytes:
    """Length-delimited protobuf field (wire type 2)."""
    data = value.encode("utf-8")
    return encode_varint((field_no << 3) | 2) + encode_varint(len(data)) + data


def encode_bytes_field(field_no: int, data: bytes) -> bytes:
    """Length-delimited protobuf field carrying raw bytes (e.g. nested
    messages such as `Stat` inside `QueryStatsResponse`)."""
    return encode_varint((field_no << 3) | 2) + encode_varint(len(data)) + data


def encode_bool_field(field_no: int, value: bool) -> bytes:
    """Varint protobuf field (wire type 0)."""
    return encode_varint((field_no << 3) | 0) + encode_varint(1 if value else 0)


def build_query_stats_request(pattern: str, reset: bool) -> bytes:
    """Binary protobuf body for GetQueryStats(pattern, reset)."""
    body = encode_string_field(1, pattern)
    if reset:
        body += encode_bool_field(2, True)
    return body


def grpc_frame(payload: bytes) -> bytes:
    """Wrap a protobuf message into a single gRPC length-prefixed frame."""
    return b"\x00" + struct.pack(">I", len(payload)) + payload


# ---------------------------------------------------------------------------
# Hand-rolled protobuf decode
# ---------------------------------------------------------------------------

def _read_varint(buf: bytes, pos: int) -> tuple[int, int]:
    """Decode a varint at `pos`; returns (value, new_pos)."""
    result = 0
    shift = 0
    while True:
        if pos >= len(buf):
            raise ValueError("truncated varint")
        byte = buf[pos]
        pos += 1
        result |= (byte & 0x7F) << shift
        if not byte & 0x80:
            return result, pos
        shift += 7
        if shift > 63:
            raise ValueError("varint too long")


def _read_length_prefixed(buf: bytes, pos: int) -> tuple[bytes, int]:
    length, pos = _read_varint(buf, pos)
    if pos + length > len(buf):
        raise ValueError("truncated length-prefixed field")
    return buf[pos : pos + length], pos + length


def parse_stat_response(buf: bytes) -> dict[str, int]:
    """Decode a QueryStatsResponse into `{counter_name: value}`."""
    stats: dict[str, int] = {}
    pos = 0
    while pos < len(buf):
        tag, pos = _read_varint(buf, pos)
        field_no, wire = tag >> 3, tag & 0x07

        if field_no == 1 and wire == 2:  # repeated Stat
            stat_msg, pos = _read_length_prefixed(buf, pos)
            name, value = _parse_stat(stat_msg)
            if name:
                stats[name] = value
        elif wire == 2:  # unknown length-delimited: skip
            _, pos = _read_length_prefixed(buf, pos)
        elif wire == 0:  # unknown varint: skip
            _, pos = _read_varint(buf, pos)
        else:
            raise ValueError(f"unsupported wire type {wire}")

    return stats


def _parse_stat(stat_msg: bytes) -> tuple[str, int]:
    """Decode a `Stat` message: field 1 name (string), field 2 value (varint)."""
    name = ""
    value = 0
    pos = 0
    while pos < len(stat_msg):
        tag, pos = _read_varint(stat_msg, pos)
        field_no, wire = tag >> 3, tag & 0x07

        if field_no == 1 and wire == 2:
            name, pos = _read_length_prefixed(stat_msg, pos)
            name = name.decode("utf-8", errors="replace")
        elif field_no == 2 and wire == 0:
            value, pos = _read_varint(stat_msg, pos)
        elif wire == 2:
            _, pos = _read_length_prefixed(stat_msg, pos)
        elif wire == 0:
            _, pos = _read_varint(stat_msg, pos)
        else:
            raise ValueError(f"unsupported wire type {wire}")
    return name, value


# ---------------------------------------------------------------------------
# gRPC call over h2 (cleartext HTTP/2 to the loopback dokodemo inbound)
# ---------------------------------------------------------------------------

def _h2_headers(timeout_ms: int) -> list[tuple[str, str]]:
    return [
        (":method", "POST"),
        (":scheme", "http"),
        (":path", _GRPC_PATH),
        (":authority", "127.0.0.1"),
        ("content-type", "application/grpc"),
        ("user-agent", "xray-panel/1.0"),
        ("te", "trailers"),
        ("grpc-timeout", f"{timeout_ms}m"),
    ]


def query_stats(
    host: str = "127.0.0.1",
    port: int = 23231,
    pattern: str = "user>>>",
    reset: bool = False,
    timeout: float = 5.0,
) -> dict[str, int]:
    """Call GetQueryStats; return `{counter_name: int_value}`.

    `pattern` examples:
      * "user>>>"    -> every per-user counter (uplink + downlink)
      * "user>>>email>>>" -> one user's counters
      * "inbound>>>" -> per-inbound counters

    With `reset=True` Xray zeroes the counters it returns, so the caller must
    persist the returned values to keep accounting exact (see app.monitor).
    """
    sock = socket.create_connection((host, port), timeout=timeout)
    sock.settimeout(timeout)
    try:
        conn = h2.connection.H2Connection()
        conn.initiate_connection()
        sock.sendall(conn.data_to_send())

        timeout_ms = max(1, int(timeout * 1000))
        stream_id = conn.get_next_available_stream_id()
        conn.send_headers(stream_id, _h2_headers(timeout_ms), end_stream=False)
        conn.send_data(stream_id, grpc_frame(build_query_stats_request(pattern, reset)), end_stream=True)
        sock.sendall(conn.data_to_send())

        payload = b""
        grpc_status = None
        http_status = None
        while True:
            data = sock.recv(65536)
            if not data:
                break
            events = conn.receive_data(data)
            for event in events:
                if isinstance(event, h2.events.DataReceived):
                    payload += event.data
                    conn.acknowledge_received_data(event.flow_controlled_length, event.stream_id)
                elif isinstance(event, h2.events.ResponseReceived):
                    headers = dict(event.headers)
                    status_raw = headers.get(b":status") or headers.get(":status")
                    grpc_raw = headers.get(b"grpc-status") or headers.get("grpc-status")
                    http_status = int(status_raw) if status_raw else http_status
                    grpc_status = int(grpc_raw) if grpc_raw else grpc_status
                elif isinstance(event, h2.events.StreamEnded):
                    sock.shutdown(socket.SHUT_RDWR)
                    return _finish(payload, http_status, grpc_status)
                elif isinstance(event, h2.events.StreamReset):
                    raise RuntimeError(f"Xray API stream reset: {event.error_code}")
            out = conn.data_to_send()
            if out:
                sock.sendall(out)
        return _finish(payload, http_status, grpc_status)
    finally:
        try:
            sock.close()
        except OSError:
            pass


def _finish(payload: bytes, http_status: int | None, grpc_status: int | None) -> dict[str, int]:
    """Validate gRPC result and decode the protobuf response body."""
    if http_status is not None and http_status != 200:
        raise RuntimeError(f"Xray API HTTP status {http_status}")
    if grpc_status is not None and grpc_status != 0:
        raise RuntimeError(f"Xray API grpc-status {grpc_status}")

    # Strip gRPC framing: 1 reserved byte + 4-byte length may frame multiple
    # messages; merge all of them (the service returns a single one anyway).
    stats: dict[str, int] = {}
    pos = 0
    while pos + 5 <= len(payload):
        length = struct.unpack(">I", payload[pos + 1 : pos + 5])[0]
        message = payload[pos + 5 : pos + 5 + length]
        if len(message) < length:
            raise RuntimeError("Xray API truncated gRPC frame")
        stats.update(parse_stat_response(message))
        pos += 5 + length
    return stats
