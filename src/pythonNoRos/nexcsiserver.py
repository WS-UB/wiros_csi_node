import argparse
import json
import logging
import os
import re
import socket
import struct
import subprocess
import time
from pathlib import Path

from paho.mqtt import client as mqtt

from csi import parse_frame


BASE_DIR = Path(__file__).resolve().parent
LOG = logging.getLogger("wiros-csi")
VALID_BANDWIDTHS = {20, 40, 80}
DEFAULT_RX_CORES = 4
MAC_ADDRESS_PATTERN = re.compile(r"^(?:[0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2}$")


def get_mac_filters(capture):
    """Return the normalized source-MAC allowlist from new or legacy config."""
    if "mac_filters" in capture:
        raw_filters = capture["mac_filters"]
        if not isinstance(raw_filters, list) or not raw_filters:
            raise ValueError("capture mac_filters must be a non-empty list")
        if "mac_filter" in capture:
            raise ValueError("capture cannot define both mac_filter and mac_filters")
    else:
        legacy_filter = capture.get("mac_filter")
        raw_filters = [legacy_filter] if legacy_filter else []

    normalized = []
    for mac in raw_filters:
        if not isinstance(mac, str) or not MAC_ADDRESS_PATTERN.fullmatch(mac):
            raise ValueError("capture mac_filters entries must be full MAC addresses")
        normalized.append(mac.lower())
    if len(set(normalized)) != len(normalized):
        raise ValueError("capture mac_filters must not contain duplicates")
    return tuple(normalized)


def is_source_mac_allowed(source_mac, allowed_macs):
    return not allowed_macs or source_mac.lower() in allowed_macs


def assemble_csi_matrix(streams, n_tx, n_rx):
    """Combine a complete stream set and FFT-shift each stream."""
    expected_streams = {(tx, rx) for tx in range(n_tx) for rx in range(n_rx)}
    if set(streams) != expected_streams:
        raise ValueError("incomplete CSI matrix; every configured stream is required")
    first = next(iter(streams.values()))
    n_subcarriers = first["n_subcarriers"]
    matrix_length = n_tx * n_rx * n_subcarriers
    csi_r = [0.0] * matrix_length
    csi_i = [0.0] * matrix_length
    half = n_subcarriers // 2

    for (tx, rx), stream in streams.items():
        if stream["n_subcarriers"] != n_subcarriers:
            raise ValueError("inconsistent subcarrier counts in CSI stream set")
        if not 0 <= tx < n_tx or not 0 <= rx < n_rx:
            raise ValueError("CSI stream index is outside the configured matrix")
        offset = (tx * n_rx + rx) * n_subcarriers
        csi_r[offset : offset + n_subcarriers] = (
            stream["csi_r"][half:] + stream["csi_r"][:half]
        )
        csi_i[offset : offset + n_subcarriers] = (
            stream["csi_i"][half:] + stream["csi_i"][:half]
        )

    payload = dict(first)
    payload.update(
        {
            "n_tx": n_tx,
            "n_rx": n_rx,
            "layout": "tx,rx,subcarrier",
            "csi_r": csi_r,
            "csi_i": csi_i,
        }
    )
    payload.pop("tx", None)
    payload.pop("rx", None)
    return payload


def load_config(path):
    with Path(path).open(encoding="utf-8") as file:
        config = json.load(file)
    config["mqtt"]["host"] = os.getenv("MQTT_HOST", config["mqtt"]["host"])
    config["mqtt"]["port"] = int(os.getenv("MQTT_PORT", config["mqtt"]["port"]))
    config["router"]["password"] = os.getenv(
        "ROUTER_PASSWORD", config["router"].get("password", "")
    )
    return config


def validate_config(config, require_router_password=False):
    required_sections = {"router", "receiver", "capture", "mqtt"}
    missing = required_sections.difference(config)
    if missing:
        raise ValueError(f'missing config sections: {", ".join(sorted(missing))}')

    receiver = config["receiver"]
    capture = config["capture"]
    mqtt_config = config["mqtt"]
    router = config["router"]

    if not router.get("host") or not router.get("username"):
        raise ValueError("router host and username are required")
    if require_router_password and not router.get("password"):
        raise ValueError(
            "router password is required with --configure-router; "
            "set ROUTER_PASSWORD"
        )
    if not receiver.get("id"):
        raise ValueError("receiver id is required")
    if not 1 <= int(receiver["port"]) <= 65535:
        raise ValueError("receiver port must be between 1 and 65535")
    if int(receiver.get("receive_buffer_bytes", 4 * 1024 * 1024)) <= 0:
        raise ValueError("receive_buffer_bytes must be positive")
    if int(capture["bandwidth_mhz"]) not in VALID_BANDWIDTHS:
        raise ValueError("capture bandwidth_mhz must be 20, 40, or 80")
    if not 1 <= int(capture["tx_streams"]) <= 4:
        raise ValueError("capture tx_streams must be between 1 and 4")
    capture_tx_streams = int(
        capture.get("capture_tx_streams", capture["tx_streams"])
    )
    if capture_tx_streams != int(capture["tx_streams"]):
        raise ValueError("capture capture_tx_streams must equal tx_streams")
    if not 1 <= int(capture.get("rx_cores", DEFAULT_RX_CORES)) <= 4:
        raise ValueError("capture rx_cores must be between 1 and 4")
    get_mac_filters(capture)
    if not mqtt_config.get("host") or not mqtt_config.get("topic"):
        raise ValueError("MQTT host and topic are required")
    if not 1 <= int(mqtt_config["port"]) <= 65535:
        raise ValueError("MQTT port must be between 1 and 65535")
    if int(mqtt_config.get("qos", 1)) not in {0, 1, 2}:
        raise ValueError("MQTT qos must be 0, 1, or 2")
    if int(mqtt_config.get("max_queued_messages", 1000)) <= 0:
        raise ValueError("MQTT max_queued_messages must be positive")
    if int(mqtt_config.get("max_inflight_messages", 100)) <= 0:
        raise ValueError("MQTT max_inflight_messages must be positive")
    if float(mqtt_config.get("publish_timeout_seconds", 10)) <= 0:
        raise ValueError("MQTT publish_timeout_seconds must be positive")
    if float(mqtt_config.get("connect_timeout_seconds", 10)) <= 0:
        raise ValueError("MQTT connect_timeout_seconds must be positive")


def configure_router(config):
    router = config["router"]
    capture = config["capture"]
    command = [
        "sshpass",
        "-p",
        router["password"],
        "ssh",
        "-o",
        "StrictHostKeyChecking=no",
        f'{router["username"]}@{router["host"]}',
        "/jffs/csi/setup.sh",
        str(capture["channel"]),
        str(capture["bandwidth_mhz"]),
        str(capture["tx_streams"]),
    ]
    mac_filters = get_mac_filters(capture)
    if len(mac_filters) == 1:
        command.append(mac_filters[0])
    subprocess.run(command, check=True)


def connect_mqtt(config):
    mqtt_config = config["mqtt"]
    client = mqtt.Client(
        mqtt.CallbackAPIVersion.VERSION2,
        client_id=config["receiver"]["id"],
        transport=mqtt_config.get("transport", "tcp"),
    )
    client.max_queued_messages_set(
        int(mqtt_config.get("max_queued_messages", 1000))
    )
    client.max_inflight_messages_set(
        int(mqtt_config.get("max_inflight_messages", 100))
    )
    connect_timeout = float(mqtt_config.get("connect_timeout_seconds", 10))
    previous_timeout = socket.getdefaulttimeout()
    socket.setdefaulttimeout(connect_timeout)
    try:
        LOG.info(
            "connecting to MQTT broker host=%r port=%r transport=%r",
            mqtt_config["host"],
            mqtt_config["port"],
            mqtt_config.get("transport", "tcp"),
        )
        client.connect(mqtt_config["host"], mqtt_config["port"])
    finally:
        socket.setdefaulttimeout(previous_timeout)
    client.loop_start()
    LOG.info(
        "connected to MQTT broker %s:%s",
        mqtt_config["host"],
        mqtt_config["port"],
    )
    return client


def publish_matrix(client, topic, payload, qos):
    """Queue a matrix for MQTT delivery without blocking UDP reception."""
    result = client.publish(topic, json.dumps(payload), qos=qos)
    if result.rc == mqtt.MQTT_ERR_QUEUE_SIZE:
        LOG.warning("discarding CSI frame because the MQTT queue is full")
        return False
    if result.rc != mqtt.MQTT_ERR_SUCCESS:
        raise RuntimeError(f"MQTT publish failed with code {result.rc}")
    return True


def run(config):
    client = connect_mqtt(config)
    receiver = config["receiver"]
    capture = config["capture"]
    topic = config["mqtt"]["topic"]
    n_tx = int(capture["tx_streams"])
    capture_n_tx = int(capture.get("capture_tx_streams", n_tx))
    n_rx = int(capture.get("rx_cores", DEFAULT_RX_CORES))
    required_streams = {
        (tx, rx) for tx in range(capture_n_tx) for rx in range(n_rx)
    }
    allowed_macs = set(get_mac_filters(capture))
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    requested_buffer = int(receiver.get("receive_buffer_bytes", 4 * 1024 * 1024))
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, requested_buffer)
    sock.bind((receiver["bind"], receiver["port"]))
    actual_buffer = sock.getsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF)
    LOG.info(
        "listening on %s:%s (UDP receive buffer %s bytes)",
        receiver["bind"],
        receiver["port"],
        actual_buffer,
    )
    qos = int(config["mqtt"].get("qos", 1))
    pending_streams = {}
    datagram_count = 0
    accepted_stream_count = 0
    completed_matrix_count = 0
    last_stats_at = time.monotonic()
    try:
        while True:
            data, source = sock.recvfrom(16384)
            datagram_count += 1
            try:
                payload = parse_frame(
                    data, receiver["id"], config["router"]["id"]
                )
            except (ValueError, struct.error) as error:
                LOG.warning(
                    "discarding invalid CSI datagram from %s:%s: %s",
                    source[0],
                    source[1],
                    error,
                )
                continue
            payload["udp_source"] = source[0]
            if not is_source_mac_allowed(payload["source_mac"], allowed_macs):
                continue
            key = (payload["source_mac"], payload["sequence"])
            streams = pending_streams.setdefault(key, {})
            stream_key = (payload["tx"], payload["rx"])
            if stream_key not in required_streams:
                continue
            accepted_stream_count += 1
            streams[stream_key] = payload
            if not required_streams.issubset(streams):
                while len(pending_streams) > 128:
                    pending_streams.pop(next(iter(pending_streams)))
                continue
            payload = assemble_csi_matrix(
                {key: streams[key] for key in required_streams}, n_tx, n_rx
            )
            del pending_streams[key]
            completed_matrix_count += 1
            publish_matrix(client, topic, payload, qos)
            now = time.monotonic()
            if now - last_stats_at >= 10:
                LOG.info(
                    "CSI stats datagrams=%s accepted_streams=%s matrices=%s pending=%s",
                    datagram_count,
                    accepted_stream_count,
                    completed_matrix_count,
                    len(pending_streams),
                )
                datagram_count = 0
                accepted_stream_count = 0
                completed_matrix_count = 0
                last_stats_at = now
    finally:
        sock.close()
        client.loop_stop()
        client.disconnect()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default=BASE_DIR / "config.json")
    parser.add_argument("--configure-router", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    config = load_config(args.config)
    validate_config(config, require_router_password=args.configure_router)
    if args.configure_router:
        configure_router(config)
    run(config)


if __name__ == "__main__":
    try:
        main()
    except (OSError, RuntimeError) as error:
        LOG.exception("receiver stopped: %s", error)
        raise SystemExit(1) from error
