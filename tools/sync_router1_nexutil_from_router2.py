"""Back up Router 1 nexutil and replace it with Router 2's proven binary."""
import os

import paramiko


password = os.environ["WIRES_PI_PASSWORD"]


def connect(pi_host, router_host):
    pi = paramiko.SSHClient()
    pi.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    pi.connect(pi_host, username="wiloc", password=password, timeout=10)
    channel = pi.get_transport().open_channel(
        "direct-tcpip", (router_host, 22), ("127.0.0.1", 0)
    )
    router = paramiko.SSHClient()
    router.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    router.connect(router_host, username="wiloc", password=password,
                   sock=channel, timeout=10)
    return pi, router


pi1, router1 = connect("10.84.98.51", "192.168.48.6")
pi2, router2 = connect("10.84.118.167", "192.168.48.5")
try:
    _, stdout, stderr = router2.exec_command("cat /jffs/csi/nexutil", timeout=30)
    binary = stdout.read()
    if stdout.channel.recv_exit_status():
        raise RuntimeError(stderr.read().decode(errors="replace"))
    if not binary:
        raise RuntimeError("Router 2 nexutil was empty")

    command = (
        "set -e; mkdir -p /jffs/wiros-backups/tools; "
        "test -f /jffs/wiros-backups/tools/nexutil-router1-incompatible || "
        "cp /jffs/csi/nexutil /jffs/wiros-backups/tools/nexutil-router1-incompatible; "
        "cat > /jffs/csi/nexutil.new"
    )
    stdin, stdout, stderr = router1.exec_command(command, timeout=30)
    stdin.channel.sendall(binary)
    stdin.channel.shutdown_write()
    if stdout.channel.recv_exit_status():
        raise RuntimeError(stderr.read().decode(errors="replace"))

    _, stdout, stderr = router1.exec_command(
        "set -e; chmod 755 /jffs/csi/nexutil.new; "
        "mv /jffs/csi/nexutil.new /jffs/csi/nexutil; "
        "md5sum /jffs/csi/nexutil /jffs/wiros-backups/tools/"
        "nexutil-router1-incompatible; /jffs/csi/nexutil -I eth6 -s501 -b -l2",
        timeout=30,
    )
    print(stdout.read().decode(errors="replace"))
    error = stderr.read().decode(errors="replace")
    if stdout.channel.recv_exit_status():
        raise RuntimeError(error)
finally:
    router2.close()
    pi2.close()
    router1.close()
    pi1.close()
