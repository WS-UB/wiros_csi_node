#!/bin/sh
# Experimental CSI configuration that preserves the existing AP radio mode.
# It deliberately does not reload dhd, retune the radio, or enable monitor mode.
set -eu

CH=${1:?channel required}
BW=${2:?bandwidth required}
SS=${3:?spatial stream count required}
MAC=${4:-}
IFACE=eth6

cd "$(dirname "$0")"

case "$SS" in
  1) SS_HEX=1 ;;
  2) SS_HEX=3 ;;
  3) SS_HEX=7 ;;
  4) SS_HEX=f ;;
  *) echo "invalid spatial stream count: $SS" >&2; exit 2 ;;
esac

CURRENT=$(/usr/sbin/wl -i "$IFACE" chanspec)
echo "existing_chanspec=$CURRENT"
echo "$CURRENT" | grep -q "$CH/$BW" || {
  echo "AP is not already on requested channel $CH/$BW" >&2
  exit 3
}

if [ -n "$MAC" ]; then
  PARAMS=$(./makecsiparams -e 1 -m "$MAC" -c "$CH/$BW" -C 0xf -N "0x$SS_HEX" -d 10)
else
  PARAMS=$(./makecsiparams -e 1 -c "$CH/$BW" -C 0xf -N "0x$SS_HEX" -d 10)
fi

./nexutil -I "$IFACE" -s500 -b -l38 -v "$PARAMS"
echo "ap_mode_preserved=1"
/usr/sbin/wl -i "$IFACE" status | head -4
