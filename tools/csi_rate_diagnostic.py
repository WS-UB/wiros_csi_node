import os
import sys
from concurrent.futures import ThreadPoolExecutor

import paramiko

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HOSTS = [("router-2", "100.103.185.11"), ("router-1", "100.95.115.44")]
PASSWORD = os.environ["WIRES_PI_PASSWORD"]
COMMAND = r'''set -eu
echo "--- service ---"
systemctl is-active wiros-csi.service || systemctl is-active wiros-csi-node.service || true
systemctl cat wiros-csi.service 2>/dev/null || systemctl cat wiros-csi-node.service 2>/dev/null || true
echo "--- config ---"
cat /opt/wiros_csi_node/src/pythonNoRos/config*.json 2>/dev/null || true
echo "--- recent publishes ---"
journalctl -u wiros-csi.service --since '-10 min' --no-pager 2>/dev/null | tail -80 || true
echo "--- UDP CSI datagrams in 15 seconds ---"
timeout 15 sudo -S tcpdump -ni any udp port 5500 2>/dev/null | wc -l
'''


def inspect(target):
    label, host = target
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        client.connect(host, username="wiloc", password=PASSWORD, timeout=10)
        stdin, stdout, stderr = client.exec_command(COMMAND, timeout=30)
        stdin.write(PASSWORD + "\n")
        stdin.flush()
        return f"=== {label} {host} ===\n{stdout.read().decode(errors='replace')}\n{stderr.read().decode(errors='replace')}"
    except Exception as error:
        return f"=== {label} {host} ===\n{type(error).__name__}: {error}"
    finally:
        client.close()


with ThreadPoolExecutor(max_workers=2) as pool:
    for output in pool.map(inspect, HOSTS):
        print(output)
