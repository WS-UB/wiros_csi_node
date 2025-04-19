import socket
import struct
import subprocess
import re
import json
import time
import datetime
import signal
import sys
import random
import string
import ast
from paho.mqtt import client as mqtt_client
import time
from typing import List, Tuple
import numpy as np

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

# Define masks (based on C++ code)
E_MASK = 0x000F0000  # Extracts exponent
R_MANT_MASK = 0x0003FFC0  # Extracts real mantissa (corrected from original)
I_MANT_MASK = 0x0000003F  # Extracts imaginary mantissa
R_SIGN_MASK = 0x00080000  # Extracts real sign
I_SIGN_MASK = 0x00000008  # Extracts imaginary sign
COUNT_MASK = 0x200  # Used for mantissa shifting
MANT_MASK = 0x3FF  # Final mantissa mask

def decode_csi(csi_data, n_sub):
    """Decode CSI data according to the IEEE 754 double-precision format"""
    csi = struct.unpack(f"<{n_sub * 4 * 4}I", csi_data)
    csi_r_buf = []
    csi_i_buf = []

    for i in range(n_sub * 4 * 4):  # 4x4 MIMO
        c = csi[i]

        # Extract exponent and shift to IEEE 754 format
        exp = ((c & E_MASK) >> 16) - 31 + 1023
        r_exp = exp
        i_exp = exp

        # Extract mantissas (corrected shifts based on C++ code)
        r_mant = (c & R_MANT_MASK) >> 6  # Corrected shift from C++ code
        i_mant = (c & I_MANT_MASK) << 0  # No shift for imaginary

        # Normalize real mantissa
        e_shift = 0
        while not (r_mant & COUNT_MASK) and e_shift < 10:
            r_mant <<= 1
            e_shift += 1
        
        if e_shift == 10:
            r_exp = 1023  # NaN or infinity
            r_mant = 0
        else:
            r_exp -= e_shift

        # Normalize imaginary mantissa
        e_shift = 0
        while not (i_mant & COUNT_MASK) and e_shift < 10:
            i_mant <<= 1
            e_shift += 1
            
        if e_shift == 10:
            i_exp = 1023  # NaN or infinity
            i_mant = 0
        else:
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

# MQTT Configuration
address = "128.205.218.189"
mqtt_port = 1883
client_id = "".join(random.choices((string.ascii_letters + string.digits), k=6))
CLIENT = mqtt_client.Client(mqtt_client.CallbackAPIVersion.VERSION1, "client")
topic = "/csi-ap3"

def connect_mqtt():
    def on_connect(client, userdata, flags, rc):
        if rc == 0:
            print("Connected to MQTT Broker!")
        else:
            print(f"Failed to connect, return code {rc}\n")

    client = mqtt_client.Client(mqtt_client.CallbackAPIVersion.VERSION1, client_id)
    client.on_connect = on_connect
    client.connect(address, mqtt_port)
    return client

class MacFilter:
    def __init__(self, mac: List[int], length: int):
        self.mac = mac
        self.len = length
        
    def matches(self, other_mac: List[int]) -> bool:
        if self.len == 0:
            return True
        return self.mac[:self.len] == other_mac[:self.len]

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
        self.complex_csi = None  # Added for MATLAB-like complex representation

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

# Load configuration
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

    filter = MacFilter(mac_filter, length)

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
        return result.stdout
    except subprocess.TimeoutExpired:
        print("Process Timeout")
        return ""

def handle_shutdown(sig, frame):
    print("Shutting down.")
    sys.exit(0)

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

def set_mac_filter(filt: MacFilter) -> bool:
    global filter, use_software_mac_filter
    if filt.len < 0 or filt.len > 6:
        return True
    filter = filt
    print(f"Set MAC FILTER to {filt.mac}")
    if filt.len == 2:
        use_software_mac_filter = False
    return False

def reconfigure() -> str:
    global iface, tx_nss
    if ch >= 32:
        iface = "eth6"
        tx_nss = min(tx_nss, 4)
    else:
        iface = "eth5"
        tx_nss = min(tx_nss, 3)

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

def parse_csi(data: bytes, nbytes: int):
    global channel_current, last_seq
    
    if nbytes < 18:
        print(f"Packet too small: {nbytes} bytes")
        return

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

    if use_software_mac_filter and not filter.matches(rxframe.src_mac):
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

    n_sub = int(out.bw * 3.2)
    out.n_sub = n_sub
    
    # Calculate expected CSI data size (4x4 MIMO)
    expected_csi_size = n_sub * 4 * 4 * 4  # 4 bytes per int32, 4x4 MIMO
    if len(data) < 18 + expected_csi_size:
        print(f"CSI data too small: {len(data)-18} bytes, expected {expected_csi_size}")
        return

    # Decode CSI data
    out.csi_r, out.csi_i = decode_csi(data[18:18+expected_csi_size], n_sub)
    
    # Create complex CSI representation (similar to MATLAB)
    out.complex_csi = np.array(out.csi_r) + 1j * np.array(out.csi_i)
    
    # Check for new CSI data
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

def publish_csi(channel_current: List[CsiInstance]):
    print(f"Publishing CSI data for {len(channel_current)} channels...")
    for csi in channel_current:
        # Create a dictionary with all CSI data
        csi_data = {
            "mac": ":".join(f"{x:02x}" for x in csi.source_mac),
            "rssi": csi.rssi,
            "channel": csi.channel,
            "bw": csi.bw,
            "n_sub": csi.n_sub,
            "tx": csi.tx,
            "rx": csi.rx,
            "n_rows": csi.n_rows,
            "n_cols": csi.n_cols,
            "ap_id": csi.ap_id,
            "mcs": csi.mcs,
            "rx_id": csi.rx_id,
            "stamp": csi.stamp.isoformat(),
            "complex_csi": [f"{x.real}+{x.imag}j" for x in csi.complex_csi] if csi.complex_csi is not None else None
        }
        
        # Convert to JSON and publish
        msg = json.dumps(csi_data)
        CLIENT.publish(topic, msg)
        print(f"Published CSI data for MAC: {csi_data['mac']}, RSSI: {csi.rssi}")

def main():
    global rx_ip, rx_pass, rx_host, ch, bw, beacon, tx_nss, iface, filter, use_tcp, no_config, lock_topic

    signal.signal(signal.SIGINT, handle_shutdown)

    iface = "eth0"

    if not no_config:
        print("Configuring Receiver...")
        reconfigure()

    print("Starting CSI collection")
    sockfd = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sockfd.settimeout(1.0)
    sockfd.bind(("0.0.0.0", PORT))

    while True:
        try:
            data, addr = sockfd.recvfrom(CSI_BUF_SIZE)
            if data:
                parse_csi(data, len(data))
        except socket.timeout:
            print("Socket Timeout")
            reload_router()
            print("Reloading Router...")
            time.sleep(3)
            reconnect()
            print("Starting CSI collection")
            sockfd = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sockfd.settimeout(1.0)
            sockfd.bind(("0.0.0.0", PORT))
            continue
        except Exception as e:
            print(f"Socket Error: {e}")
            continue

if __name__ == "__main__":
    CLIENT = connect_mqtt()
    CLIENT.loop_start()
    main()