"""Read-only comparison of Router 1 and Router 2 wireless driver inventories."""
import os

import paramiko


PASSWORD = os.environ["WIRES_PI_PASSWORD"]
TARGETS = (
    ("router1", "10.84.98.51", "192.168.48.6"),
    ("router2", "10.84.118.167", "192.168.48.5"),
)
COMMAND = (
    "echo firmware; nvram get buildno; nvram get extendno; uname -a; "
    "echo modules; cat /proc/modules | grep '^dhd '; "
    "find /lib/modules -name dhd.ko -type f -exec md5sum {} \\; 2>/dev/null; "
    "md5sum /jffs/csi/*.ko 2>/dev/null; "
    "echo wl_version; /usr/sbin/wl -i eth6 ver 2>&1; "
    "echo csi_tools; md5sum /jffs/csi/nexutil /jffs/csi/makecsiparams 2>/dev/null; "
    "echo ioctl501; /jffs/csi/nexutil -I eth6 -s501 -b -l2 2>&1; "
    "echo interfaces; ifconfig -a | grep '^[a-zA-Z]' | cut -d' ' -f1; "
    "echo nvram_ifnames; nvram get wl_ifnames; nvram get wl1_vifnames; "
    "echo monitor; /usr/sbin/wl -i eth6 monitor 2>&1; "
    "for i in eth6 wl1 wl1.1; do echo ioctl501_$i; "
    "/jffs/csi/nexutil -I $i -s501 -b -l2 2>&1; done; "
    "echo radio; /usr/sbin/wl -i eth6 status 2>&1 | head -5"
)


for name, pi_host, router_host in TARGETS:
    pi = paramiko.SSHClient()
    pi.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    router = paramiko.SSHClient()
    router.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        pi.connect(pi_host, username="wiloc", password=PASSWORD, timeout=10)
        tunnel = pi.get_transport().open_channel(
            "direct-tcpip", (router_host, 22), ("127.0.0.1", 0)
        )
        router.connect(router_host, username="wiloc", password=PASSWORD,
                       sock=tunnel, timeout=10)
        _, stdout, stderr = router.exec_command(COMMAND, timeout=30)
        print(f"=== {name} ===")
        print(stdout.read().decode(errors="replace"))
        print(stderr.read().decode(errors="replace"))
    finally:
        router.close()
        pi.close()
