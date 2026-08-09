"""Persist Router 1 as the normal hotspot/data router with CSI autostart disabled."""
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
        "set -e; "
        "test ! -e /jffs/scripts/services-start; "
        "test -f /jffs/scripts/services-start.csi-disabled; "
        "if test -f /jffs/csi/csi_autostart.sh; then "
        "mv /jffs/csi/csi_autostart.sh /jffs/csi/csi_autostart.sh.disabled; fi; "
        "nvram set sw_mode=1; nvram set wlc_psta=0; "
        "nvram set wl1_radio=1; nvram set wl1_bss_enabled=1; nvram commit; "
        "echo permanent_hotspot_configuration_saved; "
        "ls -l /jffs/scripts/services-start* /jffs/csi/csi_autostart* 2>/dev/null; "
        "echo sw_mode=$(nvram get sw_mode); echo wl1_radio=$(nvram get wl1_radio); "
        "echo wl1_bss_enabled=$(nvram get wl1_bss_enabled); "
        "nohup sh -c 'sleep 2; /sbin/reboot' >/tmp/permanent-hotspot-reboot.log 2>&1 &"
    )
    _, stdout, stderr = router.exec_command(command, timeout=20)
    output = stdout.read().decode(errors="replace")
    error = stderr.read().decode(errors="replace")
    status = stdout.channel.recv_exit_status()
    print(output)
    if status:
        raise RuntimeError(error)
finally:
    router.close()
    pi.close()
