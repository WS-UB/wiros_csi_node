"""Preserve Router 1's stable hotspot/data driver before post-ACK experiments."""
import os
import paramiko

password = os.environ["WIRES_PI_PASSWORD"]
pi = paramiko.SSHClient()
pi.set_missing_host_key_policy(paramiko.AutoAddPolicy())
router = paramiko.SSHClient()
router.set_missing_host_key_policy(paramiko.AutoAddPolicy())
try:
    pi.connect("10.84.98.51", username="wiloc", password=password, timeout=10)
    tunnel = pi.get_transport().open_channel(
        "direct-tcpip", ("192.168.48.6", 22), ("127.0.0.1", 0)
    )
    router.connect("192.168.48.6", username="wiloc", password=password,
                   sock=tunnel, timeout=10)
    command = (
        "set -e; mkdir -p /jffs/wiros-backups/ap-safe-hotspot-data; "
        "cp /jffs/csi/dhd-ap-safe-full-csi.ko "
        "/jffs/wiros-backups/ap-safe-hotspot-data/dhd-ap-safe-hotspot-data.ko; "
        "md5sum /jffs/csi/dhd-ap-safe-full-csi.ko "
        "/jffs/wiros-backups/ap-safe-hotspot-data/dhd-ap-safe-hotspot-data.ko; "
        "ls -l /jffs/wiros-backups/ap-safe-hotspot-data/dhd-ap-safe-hotspot-data.ko"
    )
    _, stdout, stderr = router.exec_command(command, timeout=30)
    output = stdout.read().decode(errors="replace")
    error = stderr.read().decode(errors="replace")
    status = stdout.channel.recv_exit_status()
    print(output)
    if status:
        raise RuntimeError(error)
finally:
    router.close()
    pi.close()
