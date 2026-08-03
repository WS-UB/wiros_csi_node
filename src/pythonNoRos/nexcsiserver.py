import argparse
import json
import logging
import os
import socket
import struct
import subprocess
from pathlib import Path

from paho.mqtt import client as mqtt

from csi import parse_frame


BASE_DIR = Path(__file__).resolve().parent
LOG = logging.getLogger("wiros-csi")
VALID_BANDWIDTHS = {20, 40, 80}
MATRIX_SIZE = 4
STREAM_COUNT = MATRIX_SIZE * MATRIX_SIZE


def assemble_csi_matrix(streams):
    """Combine a complete 4x4 stream set and FFT-shift each stream."""
    first = next(iter(streams.values()))
    n_subcarriers = first["n_subcarriers"]
    matrix_length = STREAM_COUNT * n_subcarriers
    csi_r = [0.0] * matrix_length
    csi_i = [0.0] * matrix_length
    half = n_subcarriers // 2

    for (tx, rx), stream in streams.items():
        if stream["n_subcarriers"] != n_subcarriers:
            raise ValueError("inconsistent subcarrier counts in CSI stream set")
        offset = (tx * MATRIX_SIZE + rx) * n_subcarriers
        csi_r[offset : offset + n_subcarriers] = (
            stream["csi_r"][half:] + stream["csi_r"][:half]
        )
        csi_i[offset : offset + n_subcarriers] = (
            stream["csi_i"][half:] + stream["csi_i"][:half]
        )

    payload = dict(first)
    payload.update(
        {
            "n_tx": MATRIX_SIZE,
            "n_rx": MATRIX_SIZE,
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
    mac_filter = capture.get("mac_filter")
    if mac_filter:
        command.append(mac_filter)
    subprocess.run(command, check=True)


def connect_mqtt(config):
    mqtt_config = config["mqtt"]
    client = mqtt.Client(
        mqtt.CallbackAPIVersion.VERSION2,
        client_id=config["receiver"]["id"],
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


def run(config):
    client = connect_mqtt(config)
    receiver = config["receiver"]
    topic = config["mqtt"]["topic"]
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
    publish_timeout = float(
        config["mqtt"].get("publish_timeout_seconds", 10)
    )
    current_key = None
    current_streams = {}
    try:
        while True:
            data, source = sock.recvfrom(16384)
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
            mac_filter = config["capture"].get("mac_filter", "").lower()
            if mac_filter and payload["source_mac"].lower() != mac_filter:
                continue
            key = (payload["source_mac"], payload["sequence"])
            if key != current_key:
                current_key = key
                current_streams = {}
            stream_key = (payload["tx"], payload["rx"])
            current_streams[stream_key] = payload
            if len(current_streams) < STREAM_COUNT:
                continue
            payload = assemble_csi_matrix(current_streams)
            current_key = None
            current_streams = {}
            result = client.publish(topic, json.dumps(payload), qos=qos)
            if result.rc == mqtt.MQTT_ERR_QUEUE_SIZE:
                LOG.warning("discarding CSI frame because the MQTT queue is full")
                continue
            if result.rc != mqtt.MQTT_ERR_SUCCESS:
                raise RuntimeError(f"MQTT publish failed with code {result.rc}")
            if qos > 0:
                result.wait_for_publish(timeout=publish_timeout)
                if not result.is_published():
                    raise TimeoutError(
                        "MQTT publish timed out after "
                        f"{publish_timeout:g} seconds"
                    )
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
        LOG.error("receiver stopped: %s", error)
        raise SystemExit(1) from error
