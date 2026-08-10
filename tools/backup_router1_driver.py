"""Create a checksummed, router-side backup of Router 1 Wi-Fi driver assets."""

import argparse
import os
import shlex

import paramiko


def run(client, command, timeout=30):
    _, stdout, stderr = client.exec_command(command, timeout=timeout)
    output = stdout.read().decode(errors="replace")
    error = stderr.read().decode(errors="replace")
    status = stdout.channel.recv_exit_status()
    if status:
        raise RuntimeError(f"command failed ({status}): {command}\n{output}{error}")
    return output.strip()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--pi-host", default="10.84.98.51")
    parser.add_argument("--router-host", default="192.168.48.6")
    parser.add_argument("--label", default="pre-ap-csi-driver-work")
    args = parser.parse_args()
    password = os.environ["WIRES_PI_PASSWORD"]

    pi = paramiko.SSHClient()
    pi.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    router = paramiko.SSHClient()
    router.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        pi.connect(args.pi_host, username="wiloc", password=password, timeout=10)
        tunnel = pi.get_transport().open_channel(
            "direct-tcpip", (args.router_host, 22), ("127.0.0.1", 0)
        )
        router.connect(
            args.router_host,
            username="wiloc",
            password=password,
            sock=tunnel,
            timeout=10,
        )
        label = shlex.quote(args.label)
        command = r'''
set -eu
stamp=$(date +%Y%m%d-%H%M%S)
dest=/jffs/wiros-backups/${stamp}-LABEL
mkdir -p "$dest/files"

for source in \
    /jffs/csi/dhd.ko \
    /jffs/csi/reload.sh \
    /jffs/csi/setup.sh \
    /jffs/csi/configcsi.sh \
    /jffs/csi/configcsi-ap.sh \
    /jffs/scripts/services-start \
    /jffs/scripts/services-start.csi-disabled
do
    if [ -f "$source" ]; then
        target="$dest/files${source}"
        mkdir -p "${target%/*}"
        cp -p "$source" "$target"
    fi
done

find /lib/modules -name dhd.ko 2>/dev/null | while read source; do
    target="$dest/files${source}"
    mkdir -p "${target%/*}"
    cp -p "$source" "$target"
done

{
    echo "created_utc=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    echo "router=$(uname -a)"
    echo "dhd_module=$(grep '^dhd ' /proc/modules || true)"
    echo "eth6_driver=$(readlink /sys/class/net/eth6/device/driver 2>/dev/null || true)"
    echo "eth6_chanspec=$(/usr/sbin/wl -i eth6 chanspec 2>/dev/null || true)"
    echo "eth6_status_begin"
    /usr/sbin/wl -i eth6 status 2>/dev/null || true
    echo "eth6_status_end"
} > "$dest/manifest.txt"

(cd "$dest" && find files -exec md5sum {} \;) > "$dest/MD5SUMS"
test -s "$dest/MD5SUMS" || {
    echo "No driver assets were copied; source inventory follows:" >&2
    ls -la /jffs /jffs/csi /jffs/scripts /lib/modules 2>&1 >&2 || true
    find /lib/modules -name '*dhd*' 2>/dev/null >&2 || true
    exit 4
}
echo "$dest"
cat "$dest/MD5SUMS"
'''.replace("LABEL", label)
        print(run(router, command, timeout=60))
    finally:
        router.close()
        pi.close()


if __name__ == "__main__":
    main()
