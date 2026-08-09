"""Disable the temporary Pi 1 Mosquitto relay without deleting its configuration."""

import argparse
import os

import paramiko


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="192.168.48.20")
    args = parser.parse_args()
    password = os.environ["WIRES_PI_PASSWORD"]
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        client.connect(args.host, username="wiloc", password=password, timeout=10)
        command = (
            "sudo -S sh -c '"
            "test ! -f /etc/mosquitto/conf.d/wiros-relay.conf || "
            "mv /etc/mosquitto/conf.d/wiros-relay.conf "
            "/etc/mosquitto/conf.d/wiros-relay.conf.disabled; "
            "test ! -f /etc/systemd/system/mosquitto.service.d/wiros.conf || "
            "mv /etc/systemd/system/mosquitto.service.d/wiros.conf "
            "/etc/systemd/system/mosquitto.service.d/wiros.conf.disabled; "
            "systemctl disable --now mosquitto; systemctl daemon-reload'"
        )
        stdin, stdout, stderr = client.exec_command(command, timeout=30)
        stdin.write(password + "\n")
        stdin.flush()
        output = stdout.read().decode(errors="replace")
        error = stderr.read().decode(errors="replace")
        status = stdout.channel.recv_exit_status()
        if status:
            raise RuntimeError(output + error)
        _, stdout, _ = client.exec_command(
            "systemctl is-active mosquitto; systemctl is-enabled mosquitto", timeout=10
        )
        print(stdout.read().decode(errors="replace").strip())
    finally:
        client.close()


if __name__ == "__main__":
    main()
