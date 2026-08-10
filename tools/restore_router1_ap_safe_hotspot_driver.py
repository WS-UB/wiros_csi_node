"""Restore the verified stable hotspot/data module after a CSI experiment."""
import os
import paramiko

password = os.environ["WIRES_PI_PASSWORD"]
pi = paramiko.SSHClient(); pi.set_missing_host_key_policy(paramiko.AutoAddPolicy())
router = paramiko.SSHClient(); router.set_missing_host_key_policy(paramiko.AutoAddPolicy())
try:
    pi.connect("10.84.98.51", username="wiloc", password=password, timeout=10)
    tunnel = pi.get_transport().open_channel(
        "direct-tcpip", ("192.168.48.6", 22), ("127.0.0.1", 0)
    )
    router.connect("192.168.48.6", username="wiloc", password=password,
                   sock=tunnel, timeout=10)
    command = (
        "set -e; export PATH=/sbin:/usr/sbin:/bin:/usr/bin:$PATH; "
        "test \"$(md5sum /jffs/wiros-backups/ap-safe-hotspot-data/"
        "dhd-ap-safe-hotspot-data.ko | cut -d' ' -f1)\" = "
        "2fe09349ce72d5155f8b52e172819514; "
        "/sbin/rmmod dhd; /sbin/insmod /jffs/wiros-backups/ap-safe-hotspot-data/"
        "dhd-ap-safe-hotspot-data.ko; ln -sf /sbin/rc /tmp/service; "
        "/tmp/service restart_wireless >/tmp/ap-safe-restore.log 2>&1; sleep 25; "
        "/usr/sbin/wl -i eth6 monitor 0; /sbin/ifconfig eth6 up; "
        "touch /tmp/wiros-ap-safe-success; "
        "echo restored_md5=$(md5sum /jffs/wiros-backups/ap-safe-hotspot-data/"
        "dhd-ap-safe-hotspot-data.ko | cut -d' ' -f1); "
        "/usr/sbin/wl -i eth6 status | head -8; /usr/sbin/wl -i eth6 assoclist"
    )
    _, stdout, stderr = router.exec_command(command, timeout=55)
    output = stdout.read().decode(errors="replace")
    error = stderr.read().decode(errors="replace")
    status = stdout.channel.recv_exit_status()
    print(output)
    if status:
        raise RuntimeError(error)
finally:
    router.close(); pi.close()
