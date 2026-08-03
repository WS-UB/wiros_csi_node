#!/bin/sh
set -eu

name=wiros-router
address=${1:-192.168.48.20/24}

if nmcli connection show "$name" >/dev/null 2>&1; then
    sudo nmcli connection modify "$name" ipv4.method manual ipv4.addresses "$address" ipv4.never-default yes ipv6.method link-local
else
    sudo nmcli connection add type ethernet ifname eth0 con-name "$name" ipv4.method manual ipv4.addresses "$address" ipv4.never-default yes ipv6.method link-local
fi

sudo nmcli connection up "$name"
