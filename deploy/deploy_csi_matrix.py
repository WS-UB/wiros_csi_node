import os
import posixpath
from datetime import datetime, timezone
from pathlib import Path

import paramiko


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
LOCAL_SOURCE = REPOSITORY_ROOT / "src" / "pythonNoRos"
PASSWORD = os.environ["WIRES_PI_PASSWORD"]
TARGETS = (
    {
        "name": "router-2",
        "host": "100.103.185.11",
        "directory": "/opt/wiros_csi_node/src/pythonNoRos",
        "service": "wiros-csi.service",
        "config": "config2.json",
    },
    {
        "name": "router-1",
        "host": "100.95.115.44",
        "directory": "/opt/wiros_csi_node/src/pythonNoRos",
        "service": "wiros-csi-node.service",
        "config": "config.json",
    },
)
FILES = ("csi.py", "nexcsiserver.py")


def run(client, command, sudo=False):
    stdin, stdout, stderr = client.exec_command(command, timeout=30)
    if sudo:
        stdin.write(PASSWORD + "\n")
        stdin.flush()
    output = stdout.read().decode(errors="replace")
    error = stderr.read().decode(errors="replace")
    status = stdout.channel.recv_exit_status()
    if status:
        raise RuntimeError(f"command failed ({status}): {command}\n{output}{error}")
    return output.strip()


def deploy(target):
    print(f"=== {target['name']} {target['host']} ===")
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(
        target["host"], username="wiloc", password=PASSWORD, timeout=10
    )
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    uploaded = []
    try:
        sftp = client.open_sftp()
        for filename in FILES:
            remote = posixpath.join(target["directory"], filename)
            temporary = remote + f".new-{stamp}"
            sftp.put(str(LOCAL_SOURCE / filename), temporary)
            uploaded.append(temporary)
        config_remote = posixpath.join(target["directory"], "config.json")
        config_temporary = config_remote + f".new-{stamp}"
        sftp.put(str(LOCAL_SOURCE / target["config"]), config_temporary)
        sftp.close()

        quoted = " ".join(uploaded)
        run(client, f"python3 -m py_compile {quoted}")
        for filename, temporary in zip(FILES, uploaded):
            remote = posixpath.join(target["directory"], filename)
            backup = remote + f".bak-{stamp}"
            run(client, f"cp {remote} {backup} && mv {temporary} {remote}")
            print(f"updated {remote}; backup {backup}")

        config_backup = config_remote + f".bak-{stamp}"
        run(client, f"cp {config_remote} {config_backup} && mv {config_temporary} {config_remote}")
        print(f"updated {config_remote}; backup {config_backup}")

        run(client, f"sudo -S systemctl restart {target['service']}", sudo=True)
        state = run(client, f"systemctl is-active {target['service']}")
        details = run(
            client,
            "python3 -c \"import ast; "
            f"p='{posixpath.join(target['directory'], 'nexcsiserver.py')}'; "
            "s=open(p).read(); print('assembler=', 'assemble_csi_matrix' in s, "
            "'dynamic_dimensions=', 'required_streams' in s)\"",
        )
        print(f"service={state} {details}")
    finally:
        client.close()


for item in TARGETS:
    deploy(item)
