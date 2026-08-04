import os
import paramiko
import sys
import time
from concurrent.futures import ThreadPoolExecutor

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HOSTS = ["100.103.185.11", "100.95.115.44"]
COMMAND = "date +%s%N; timedatectl show -p NTPSynchronized -p NTP 2>&1"

def inspect(host):
    print(f"=== {host} ===")
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        client.connect(host, username="wiloc", password=os.environ["WIRES_PI_PASSWORD"], timeout=10)
        started = time.time_ns()
        stdin, stdout, stderr = client.exec_command(COMMAND, timeout=20)
        stdin.write(os.environ["WIRES_PI_PASSWORD"] + "\n")
        stdin.flush()
        output = stdout.read().decode(errors="replace")
        finished = time.time_ns()
        lines = output.splitlines()
        remote_ns = int(lines[0])
        midpoint = (started + finished) // 2
        print(f"{host} offset_ms={(remote_ns - midpoint) / 1_000_000:.1f} rtt_ms={(finished - started) / 1_000_000:.1f}")
        print("\n".join(lines[1:]))
        error = stderr.read().decode(errors="replace")
        if error:
            print(error)
    except Exception as exc:
        print(f"{type(exc).__name__}: {exc}")
    finally:
        client.close()


with ThreadPoolExecutor(max_workers=len(HOSTS)) as pool:
    list(pool.map(inspect, HOSTS))
