import socket
import struct
import subprocess
import re
import json
import time
import datetime
import signal
import sys
import numpy as np
import random
import string
import ast
from paho.mqtt import client as mqtt_client
import time
from typing import List, Tuple

# Constants
CSI_BUF_SIZE = 16384
PORT = 5500
PORT_TCP = 50005

# Global variables
channel_current = []
last_seq = 0
rx_ip = ""
rx_pass = "password"
rx_host = "admin"
use_tcp = False
no_config = False
hostname = ""
ch = 157
bw = 20
beacon = 200.0
iface = ""
tx_nss = 4
filter = []
use_software_mac_filter = False
lock_topic = ""
csi_buf = bytearray(CSI_BUF_SIZE)
csi_data = bytearray(CSI_BUF_SIZE)
csi_r_out = None
csi_i_out = None
csi_size = 0

AP_LAT1, AP_LONG1 = (12, 15)
AP_LAT2, AP_LONG2 = (12, 15)
AP_LAT, AP_LONG = (12, 15)

# Define masks (based on C++ code)
E_MASK = 0x000F0000  # Extracts exponent
R_MANT_MASK = 0x0003FFC0  # Extracts real mantissa
I_MANT_MASK = 0x0000003F  # Extracts imaginary mantissa
R_SIGN_MASK = 0x00080000  # Extracts real sign
I_SIGN_MASK = 0x00000008  # Extracts imaginary sign
COUNT_MASK = 0x200  # Used for mantissa shifting
MANT_MASK = 0x3FF  # Final mantissa mask


def decode_csi(csi_data, n_sub):
    csi = struct.unpack(f"<{n_sub * 4 * 4}I", csi_data)
    csi_r_buf = []
    csi_i_buf = []

    for i in range(n_sub):
        c = csi[i]

        # Extract exponent and shift to IEEE 754 format
        exp = ((c & E_MASK) >> 16) - 31 + 1023
        r_exp = exp
        i_exp = exp

        # Extract mantissas
        r_mant = (c & R_MANT_MASK) >> 6
        i_mant = c & I_MANT_MASK

        # Normalize real mantissa
        e_shift = 0
        while not (r_mant & COUNT_MASK):
            r_mant <<= 1
            e_shift += 1
            if e_shift == 10:
                r_exp = 1023
                r_mant = 0
                break
        r_exp -= e_shift

        # Normalize imaginary mantissa
        e_shift = 0
        while not (i_mant & COUNT_MASK):
            i_mant <<= 1
            e_shift += 1
            if e_shift == 10:
                i_exp = 1023
                i_mant = 0
                break
        i_exp -= e_shift

        # Construct IEEE 754 double-precision representation
        c_r = ((c & R_SIGN_MASK) << 34) | ((r_mant & MANT_MASK) << 42) | (r_exp << 52)
        c_i = ((c & I_SIGN_MASK) << 46) | ((i_mant & MANT_MASK) << 42) | (i_exp << 52)

        # Convert to float using struct
        csi_r_buf.append(struct.unpack("d", struct.pack("Q", c_r))[0])
        csi_i_buf.append(struct.unpack("d", struct.pack("Q", c_i))[0])

    return csi_r_buf, csi_i_buf


def string_to_bool(string):
    string = string.lower()
    if string == "true":
        return True
    elif string == "false":
        return False
    else:
        raise ValueError("Invalid input: string must be 'true' or 'false'")


address = "128.205.218.189"
mqtt_port = 1883
client_id = "".join(random.choices((string.ascii_letters + string.digits), k=6))
CLIENT = mqtt_client.Client(mqtt_client.CallbackAPIVersion.VERSION1, "client")
topic = "/csi-ap1"


def connect_mqtt():
    def on_connect(client, userdata, flags, rc):
        if rc == 0:
            print("Connected to MQTT Broker!")
        else:
            print("Failed to connect, return code %d\n", rc)

    client = mqtt_client.Client(mqtt_client.CallbackAPIVersion.VERSION1, client_id)
    # client.username_pw_set(username, password)
    client.on_connect = on_connect
    client.connect(address, mqtt_port)
    return client


# MAC address filter
class MacFilter:
    def __init__(self, mac: List[int], length: int):
        self.mac = mac
        self.len = length


# CSI instance
class CsiInstance:
    def __init__(self):
        self.source_mac = [0] * 6
        self.seq_num = 0
        self.rssi = 0
        self.tx = 4
        self.rx = 4
        self.channel = 0
        self.bw = 0
        self.n_sub = 0
        self.csi_r = []
        self.csi_i = []
        self.seq = 0
        self.fc = 0
        self.n_cols = 4
        self.n_rows = 4
        self.ap_id = 0
        self.mcs = 0
        self.rx_id = 0
        self.stamp = None


# CSI UDP frame
class CsiUdpFrame:
    def __init__(self):
        self.kk1 = 0
        self.id = 0
        self.rssi = 0
        self.fc = 0
        self.src_mac = [0] * 6
        self.seqCnt = 0
        self.csiconf = 0
        self.chanspec = 0
        self.chip = 0


with open("config.json", "r") as file:
    config_data = json.load(file)

    rx_ip = config_data["login"][0]["host_ip"]
    rx_host = config_data["login"][0]["host"]
    rx_pass = config_data["login"][0]["host_passwd"]

    ch = int(config_data["channel_params"][0]["channel"])
    bw = int(config_data["channel_params"][0]["bw"])

    use_software_mac_filter = string_to_bool(
        config_data["packet_params"][0]["use_software_mac_filter"]
    )
    beacon = float(config_data["packet_params"][0]["beacon_rate"])
    tx_nss = int(config_data["packet_params"][0]["beacon_tx_nss"])

    use_tcp = string_to_bool(config_data["broadcast"][0]["tcp_forward"])

    mac_filter = ast.literal_eval(config_data["packet_params"][0]["mac_filter"])
    length = int(config_data["packet_params"][0]["length"])

    AP_LAT = float(config_data["aoa_info"][0]["AP_lat"])
    AP_LONG = float(config_data["aoa_info"][0]["AP_long"])
    AP_LAT1 = float(config_data["aoa_info"][0]["lat1"])
    AP_LONG1 = float(config_data["aoa_info"][0]["long1"])
    AP_LAT2 = float(config_data["aoa_info"][0]["lat2"])
    AP_LONG2 = float(config_data["aoa_info"][0]["long2"])

    filter = MacFilter(mac_filter, length)


# Execute a shell command and return the output
def sh_exec_block(cmd: str) -> str:
    try:
        result = subprocess.run(
            cmd,
            shell=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=10,
        )
    except subprocess.TimeoutExpired:
        print("Process Timeout")
        print("Retrying...")
        return
    print(result.stdout)
    return result.stdout


# Handle shutdown signal
def handle_shutdown(sig, frame):
    print("Shutting down.")
    sys.exit(0)


# Set channel and bandwidth
def set_chanspec(s_chan: int, s_bw: int) -> bool:
    global ch, bw
    if not (s_bw == -1 or s_bw == 20 or s_bw == 40 or s_bw == 80):
        return True
    if s_chan != -1 and s_chan != ch:
        ch = s_chan
        print(f"Setting CHANNEL to {ch}")
    if s_bw != -1 and s_bw != bw:
        bw = s_bw
        print(f"Setting BW to {bw}")
    return False


# Set MAC filter
def set_mac_filter(filt: MacFilter) -> bool:
    global filter, use_software_mac_filter
    if filt.len < 0 or filt.len > 6:
        return True
    filter = filt
    print(f"Set MAC FILTER to {filt.mac}")
    if filt.len == 2:
        use_software_mac_filter = False
    return False


# Reconfigure the router
def reconfigure() -> str:
    global iface, tx_nss
    if ch >= 32:
        iface = "eth6"
        tx_nss = min(tx_nss, 4)
    else:
        iface = "eth5"
        tx_nss = min(tx_nss, 3)

    print(ch)
    print(bw)

    if filter.len > 1:
        configcmd = f"sshpass -p {rx_pass} ssh -o strictHostKeyChecking=no {rx_host}@{rx_ip} /jffs/csi/setup.sh {ch} {bw} 4 {filter.mac[0]:02x}:{filter.mac[1]:02x}:{filter.mac[2]:02x}:{filter.mac[3]:02x}:{filter.mac[4]:02x}:{filter.mac[5]:02x} 2>&1"

    else:
        configcmd = f"sshpass -p {rx_pass} ssh -o strictHostKeyChecking=no {rx_host}@{rx_ip} /jffs/csi/setup.sh {ch} {bw} 4 2>&1"
    print(configcmd)
    return sh_exec_block(configcmd)


def reconnect() -> str:
    global iface, tx_nss
    if ch >= 32:
        iface = "eth6"
        tx_nss = min(tx_nss, 4)
    else:
        iface = "eth5"
        tx_nss = min(tx_nss, 3)

    if filter.len > 1:
        configcmd = f"sshpass -p {rx_pass} ssh -o strictHostKeyChecking=no {rx_host}@{rx_ip} /jffs/csi/configcsi.sh {ch} {bw} 4 {filter.mac[0]:02x}:{filter.mac[1]:02x}:{filter.mac[2]:02x}:{filter.mac[3]:02x}:{filter.mac[4]:02x}:{filter.mac[5]:02x} 2>&1"

    else:
        configcmd = f"sshpass -p {rx_pass} ssh -o strictHostKeyChecking=no {rx_host}@{rx_ip} /jffs/csi/configcsi.sh {ch} {bw} 4 2>&1"
    print(configcmd)
    return sh_exec_block(configcmd)


def reload_router() -> str:
    configcmd = f"sshpass -p {rx_pass} ssh -o strictHostKeyChecking=no {rx_host}@{rx_ip} /jffs/csi/reload.sh 2>&1"
    print(configcmd)
    return sh_exec_block(configcmd)

def ifconfig() -> str:
    configcmd = f"sudo ifconfig eth0 {rx_ip} netmask 255.255.255.0"
    print(configcmd)
    return sh_exec_block(configcmd)

# Parse CSI data
def parse_csi(data: bytes, nbytes: int):
    global channel_current, last_seq
    rxframe = CsiUdpFrame()
    rxframe.kk1 = data[0]
    rxframe.id = data[1]
    rxframe.rssi = struct.unpack("b", data[2:3])[0]
    rxframe.fc = data[3]
    rxframe.src_mac = list(data[4:10])
    rxframe.seqCnt = struct.unpack("<H", data[10:12])[0]
    rxframe.csiconf = struct.unpack("<H", data[12:14])[0]
    rxframe.chanspec = struct.unpack("<H", data[14:16])[0]
    rxframe.chip = struct.unpack("<H", data[16:18])[0]

    if use_software_mac_filter and rxframe.src_mac != filter.mac:
        return

    out = CsiInstance()
    out.rssi = rxframe.rssi
    out.source_mac = rxframe.src_mac
    out.seq = rxframe.seqCnt
    out.fc = rxframe.fc
    out.tx = (rxframe.csiconf >> 11) & 0x3
    out.rx = (rxframe.csiconf >> 8) & 0x3
    out.bw = (rxframe.chanspec >> 11) & 0x07
    out.channel = rxframe.chanspec & 255
    out.n_rows = 4
    out.n_cols = 4
    out.ap_id = 0
    out.rx_id = rx_ip
    out.stamp = datetime.datetime.now()

    if out.bw == 0x4:
        out.bw = 80
    elif out.bw == 0x3:
        out.bw = 40
    elif out.bw == 0x2:
        out.bw = 20
    else:
        print(f"Invalid Bandwidth received {out.bw}")
        return

    NFFT = out.bw*3.2
    n_sub = int(out.bw * 3.2)
    out.n_sub = n_sub
    print(out.n_sub)
    out.csi_r = [0.0] * n_sub * 4 * 4
    out.csi_i = [0.0] * n_sub * 4 * 4

    # out.csi_r, out.csi_i = decode_csi(data[18 : 18 + n_sub * 4 *4 *4], n_sub)

    print(out.rx)

    n_rx = 4  # Number of RX antennas
    csi = struct.unpack(f"<{n_sub*4 * 4}I", data[18 : 18 + n_sub * n_rx * 4 * 4])
    #csi = np.concatenate((csi[len(csi)//2:], csi[:len(csi)//2]), axis = 0)

    #print(data[18 : 18 + n_sub * n_rx * 4 * 4])
    
    

    for i in range(n_sub * 4 * 4):
        out.csi_r[i] = float(csi[i] & 0xFFFF)  # Simplified CSI decoding
        out.csi_i[i] = float((csi[i] >> 16) & 0xFFFF)

    new_csi = False
    for ch_it in channel_current:
        if ch_it.tx * 4 + ch_it.rx == out.tx * 4 + out.rx:
            new_csi = True
    if out.seq != last_seq and len(channel_current) > 0:
        new_csi = True

    if new_csi:
        publish_csi(channel_current)
        channel_current.clear()

    last_seq = out.seq
    channel_current.append(out)


# Publish CSI data (replace with your own logic)
def publish_csi(channel_current: List[CsiInstance]):
    print("Publishing CSI data...")
    for csi in channel_current:
        msg = f"MAC: {csi.source_mac}, RSSI: {csi.rssi}, Channel: {csi.channel}, BW: {csi.bw}, csi_i: {csi.csi_i}, csi_r: {csi.csi_r}, fc: {csi.fc}, n_sub: {csi.n_sub}, tx: {csi.tx}, n_rows: {csi.n_rows}, n_cols:{csi.n_cols}, ap_id: {csi.ap_id}, mcs: {csi.mcs}, rx_id: {csi.rx_id}, stamp;{csi.stamp}, AP_location: {[AP_LAT, AP_LONG]}, AP_L1: {[AP_LAT1, AP_LONG1]}, AP_L2: {[AP_LAT2, AP_LONG2]}"
        CLIENT.publish(topic, msg)
        print(
            f"MAC: {csi.source_mac}, RSSI: {csi.rssi}, Channel: {csi.channel}, BW: {csi.bw}, csi_i: {csi.csi_i}, csi_r: {csi.csi_r}, fc: {csi.fc}, n_sub: {csi.n_sub}, tx: {csi.tx}, n_rows: {csi.n_rows}, n_cols:{csi.n_cols}, ap_id: {csi.ap_id}, mcs: {csi.mcs}, rx_id: {csi.rx_id}, stamp;{csi.stamp}, AP_location: {[AP_LAT, AP_LONG]}, AP_L1: {[AP_LAT1, AP_LONG1]}, AP_L2: {[AP_LAT2, AP_LONG2]}"
        )


# Main function
def main():
    global rx_ip, rx_pass, rx_host, ch, bw, beacon, tx_nss, iface, filter, use_tcp, no_config, lock_topic

    # Handle shutdown signal
    signal.signal(signal.SIGINT, handle_shutdown)

    # Setup parameters (replace with your own configuration)
    iface = "eth0"

    # Configure the router
    if not no_config:
        print("Configuring Receiver...")
        reconfigure()

    # Start CSI collection
    print("Starting CSI collection")
    sockfd = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sockfd.settimeout(10.0)
    sockfd.bind(("0.0.0.0", PORT))

    while True:
        try:
            total_data = b""
            while len(total_data) < CSI_BUF_SIZE:
                data, addr = sockfd.recvfrom(CSI_BUF_SIZE)
                total_data += data

            print(f"Total data size: {len(total_data)}")
            parse_csi(total_data, len(total_data))
        except socket.timeout:
            ifconfig()
            print("Socket Timeout")
            reload_router()
            print("Reloading Router...")
            time.sleep(1)
            reconnect()
            print("Starting CSI collection")
            sockfd = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sockfd.settimeout(10.0)
            sockfd.bind(("0.0.0.0", PORT))

            continue
        except Exception as e:
            print(f"Socket Error: {e}")
            continue


if __name__ == "__main__":
    CLIENT = connect_mqtt()
    main()
