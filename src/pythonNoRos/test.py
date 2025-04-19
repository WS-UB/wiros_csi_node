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
from typing import List

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
ch = 157
bw = 20
beacon = 200.0
iface = ""
tx_nss = 4
use_software_mac_filter = False

# Define masks (exactly as in C++ code)
E_MASK = 0x000F0000  # Extracts exponent
R_MANT_MASK = 0x0003FFC0  # Extracts real mantissa
I_MANT_MASK = 0x0000003F  # Extracts imaginary mantissa
R_SIGN_MASK = 0x00080000  # Extracts real sign
I_SIGN_MASK = 0x00000008  # Extracts imaginary sign
COUNT_MASK = 0x200  # Used for mantissa shifting
MANT_MASK = 0x3FF  # Final mantissa mask

# MAC address filter
class MacFilter:
    def __init__(self, mac: List[int], length: int):
        self.mac = mac
        self.len = length
    
    def matches(self, other_mac):
        """Check if the MAC filter matches the given MAC address"""
        if self.len <= 0:
            return True
        for i in range(self.len):
            if self.mac[i] != other_mac[i]:
                return False
        return True

# CSI instance
class CsiInstance:
    def __init__(self):
        self.source_mac = [0] * 6
        self.seq = 0
        self.rssi = 0
        self.fc = 0
        self.tx = 0
        self.rx = 0
        self.channel = 0
        self.bw = 0
        self.n_sub = 0
        self.csi_r = []
        self.csi_i = []
        self.n_rows = 4
        self.n_cols = 4
        self.ap_id = 0
        self.mcs = 0
        self.rx_id = ""
        self.stamp = None

# CSI UDP frame
class CsiUdpFrame:
    def __init__(self, data):
        self.kk1 = data[0]
        self.id = data[1]
        self.rssi = struct.unpack("b", data[2:3])[0]
        self.fc = data[3]
        self.src_mac = list(data[4:10])
        self.seqCnt = struct.unpack("<H", data[10:12])[0]
        self.csiconf = struct.unpack("<H", data[12:14])[0]
        self.chanspec = struct.unpack("<H", data[14:16])[0]
        self.chip = struct.unpack("<H", data[16:18])[0]

def decode_csi(csi_data, n_sub):
    """
    Decode CSI data according to the exact bit manipulation in the C++ code
    """
    # Get only the needed bytes from the data (skipping the header)
    start_offset = 18  # Size of csi_udp_frame
    csi = []
    
    # Unpack integers directly from the binary data
    for i in range(n_sub):
        offset = start_offset + i * 4
        c = struct.unpack("<I", csi_data[offset:offset+4])[0]
        csi.append(c)
    
    csi_r_buf = []
    csi_i_buf = []
    
    for i in range(n_sub):
        c = csi[i]
        
        # Extract and adjust exponent (matching C++ code)
        exp = ((c & E_MASK) >> 16) - 31 + 1023
        r_exp = exp
        i_exp = exp
        
        # Extract mantissas (matching C++ code)
        r_mant = (c & R_MANT_MASK) >> 18
        i_mant = (c & I_MANT_MASK) >> 6
        
        # Normalize real mantissa (matching C++ code)
        e_shift = 0
        while not (r_mant & COUNT_MASK):
            r_mant *= 2
            e_shift += 1
            if e_shift == 10:
                r_exp = 1023
                e_shift = 0
                r_mant = 0
                break
        r_exp -= e_shift
        
        # Normalize imaginary mantissa (matching C++ code)
        e_shift = 0
        while not (i_mant & COUNT_MASK):
            i_mant *= 2
            e_shift += 1
            if e_shift == 10:
                i_exp = 1023
                e_shift = 0
                i_mant = 0
                break
        i_exp -= e_shift
        
        # Construct IEEE 754 double-precision representation
        c_r = ((c & R_SIGN_MASK) << 34) | ((r_mant & MANT_MASK) << 42) | (r_exp << 52)
        c_i = ((c & I_SIGN_MASK) << 46) | ((i_mant & MANT_MASK) << 42) | (i_exp << 52)
        
        # Convert to float using struct
        csi_r_buf.append(struct.unpack("d", struct.pack("<Q", c_r))[0])
        csi_i_buf.append(struct.unpack("d", struct.pack("<Q", c_i))[0])
    
    return csi_r_buf, csi_i_buf

def string_to_bool(string):
    string = string.lower()
    if string == "true":
        return True
    elif string == "false":
        return False
    else:
        raise ValueError("Invalid input: string must be 'true' or 'false'")

# MQTT setup
address = "128.205.218.189"
mqtt_port = 1883
client_id = "".join(random.choices((string.ascii_letters + string.digits), k=6))
CLIENT = mqtt_client.Client(mqtt_client.CallbackAPIVersion.VERSION1, client_id)
topic = "/csi-ap3"

def connect_mqtt():
    def on_connect(client, userdata, flags, rc):
        if rc == 0:
            print("Connected to MQTT Broker!")
        else:
            print(f"Failed to connect, return code {rc}")
    
    client = mqtt_client.Client(mqtt_client.CallbackAPIVersion.VERSION1, client_id)
    client.on_connect = on_connect
    client.connect(address, mqtt_port)
    return client

# Load configuration
def load_config():
    global rx_ip, rx_host, rx_pass, ch, bw, use_software_mac_filter, beacon, tx_nss, use_tcp, filter
    
    try:
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
    except Exception as e:
        print(f"Error loading config: {e}")
        sys.exit(1)

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
        return result.stdout
    except subprocess.TimeoutExpired:
        print("Process Timeout")
        print("Retrying...")
        return ""

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
    
    # Kill any existing send.sh processes
    killcmd = f"sshpass -p {rx_pass} ssh -o strictHostKeyChecking=no {rx_host}@{rx_ip} killall send.sh 2>/dev/null || true"
    sh_exec_block(killcmd)
    
    # Configure CSI setup
    if filter.len > 1:
        mac_str = ':'.join(f"{x:02x}" for x in filter.mac[:filter.len])
        configcmd = f"sshpass -p {rx_pass} ssh -o strictHostKeyChecking=no {rx_host}@{rx_ip} /jffs/csi/setup.sh {ch} {bw} 4 {mac_str} 2>&1"
    else:
        configcmd = f"sshpass -p {rx_pass} ssh -o strictHostKeyChecking=no {rx_host}@{rx_ip} /jffs/csi/setup.sh {ch} {bw} 4 2>&1"
    
    print(f"Running config command: {configcmd}")
    result = sh_exec_block(configcmd)
    
    # Start beaconing if required
    if beacon > 0:
        print("Starting tx...")
        # Format matches the C++ file's sprintf for beacon_mac values
        beaconcmd = f"sshpass -p {rx_pass} ssh -o strictHostKeyChecking=no {rx_host}@{rx_ip} \"/jffs/csi/send.sh {bw} {tx_nss} {int(beacon*1000)} {iface} 11 11 11 {0:x} {0:x} {0:x} 2>&1 & \""
        print(f"Running beacon command: {beaconcmd}")
        sh_exec_block(beaconcmd)
    
    return result

def reconnect() -> str:
    global iface, tx_nss
    if ch >= 32:
        iface = "eth6"
        tx_nss = min(tx_nss, 4)
    else:
        iface = "eth5"
        tx_nss = min(tx_nss, 3)
    
    # Use configcsi.sh if available
    if filter.len > 1:
        mac_str = ':'.join(f"{x:02x}" for x in filter.mac[:filter.len])
        configcmd = f"sshpass -p {rx_pass} ssh -o strictHostKeyChecking=no {rx_host}@{rx_ip} /jffs/csi/configcsi.sh {ch} {bw} 4 {mac_str} 2>&1"
    else:
        configcmd = f"sshpass -p {rx_pass} ssh -o strictHostKeyChecking=no {rx_host}@{rx_ip} /jffs/csi/configcsi.sh {ch} {bw} 4 2>&1"
    
    print(f"Running reconnect command: {configcmd}")
    return sh_exec_block(configcmd)

def reload_router() -> str:
    configcmd = f"sshpass -p {rx_pass} ssh -o strictHostKeyChecking=no {rx_host}@{rx_ip} /jffs/csi/reload.sh 2>&1"
    print(f"Running reload command: {configcmd}")
    return sh_exec_block(configcmd)

# Parse CSI data
def parse_csi(data: bytes, nbytes: int):
    global channel_current, last_seq, rx_ip, filter
    
    # Check for minimum valid packet size
    if nbytes < 18:  # minimum size of CSI UDP frame
        print(f"Packet too small: {nbytes} bytes")
        return
    
    # Create UDP frame from binary data
    rxframe = CsiUdpFrame(data)
    
    # Check MAC filter if applicable
    if use_software_mac_filter and not filter.matches(rxframe.src_mac):
        return
    
    # Create CSI instance
    out = CsiInstance()
    out.rssi = rxframe.rssi
    out.source_mac = rxframe.src_mac.copy()
    out.seq = rxframe.seqCnt
    out.fc = rxframe.fc
    
    # Extract tx/rx antenna information
    out.tx = (rxframe.csiconf >> 11) & 0x3
    out.rx = (rxframe.csiconf >> 8) & 0x3
    
    # Process bandwidth
    bw_code = (rxframe.chanspec >> 11) & 0x07
    if bw_code == 0x4:
        out.bw = 80
    elif bw_code == 0x3:
        out.bw = 40
    elif bw_code == 0x2:
        out.bw = 20
    else:
        print(f"Invalid Bandwidth received {bw_code}")
        return
    
    # Get channel and calculate number of subcarriers
    out.channel = rxframe.chanspec & 255
    n_sub = int(out.bw * 3.2)
    out.n_sub = n_sub
    
    # Additional metadata
    out.n_rows = 4
    out.n_cols = 4
    out.ap_id = 0
    out.rx_id = rx_ip
    out.stamp = datetime.datetime.now()
    
    # Decode CSI data
    # Check if we have enough data for CSI decoding
    expected_size = 18 + (n_sub * 4)  # header + CSI data
    if nbytes < expected_size:
        print(f"Not enough data for CSI: expected {expected_size}, got {nbytes}")
        return
    
    # Decode CSI values
    out.csi_r, out.csi_i = decode_csi(data, n_sub)
    
    # Check if this is a new MIMO channel or part of an existing one
    new_csi = False
    for ch_it in channel_current:
        out_comb = out.tx * 4 + out.rx
        ch_comb = ch_it.tx * 4 + ch_it.rx
        if ch_comb == out_comb:
            new_csi = True
            break
    
    if out.seq != last_seq and len(channel_current) > 0:
        new_csi = True
    
    if new_csi and len(channel_current) > 0:
        publish_csi(channel_current)
        channel_current.clear()
    
    last_seq = out.seq
    channel_current.append(out)

# Publish CSI data to MQTT
def publish_csi(channel_list: List[CsiInstance]):
    if not channel_list:
        return
    
    print(f"Publishing CSI data for {len(channel_list)} channels...")
    
    # Create a condensed representation for MQTT
    for csi in channel_list:
        # Create a JSON representation of the CSI data
        msg_data = {
            "mac": csi.source_mac,
            "rssi": csi.rssi,
            "channel": csi.channel,
            "bw": csi.bw,
            "seq": csi.seq,
            "fc": csi.fc,
            "n_sub": csi.n_sub,
            "tx": csi.tx,
            "rx": csi.rx,
            "timestamp": csi.stamp.isoformat() if csi.stamp else None,
            # Only send a few sample values to save bandwidth
            "csi_r_sample": csi.csi_r[:5] if csi.csi_r else [],
            "csi_i_sample": csi.csi_i[:5] if csi.csi_i else []
        }
        
        # Convert to JSON and publish
        json_msg = json.dumps(msg_data)
        CLIENT.publish(topic, json_msg)
        
        # Print confirmation
        print(f"Published CSI from MAC: {':'.join(f'{b:02x}' for b in csi.source_mac)}, "
              f"RSSI: {csi.rssi}, Channel: {csi.channel}, BW: {csi.bw}, "
              f"TX: {csi.tx}, RX: {csi.rx}")

# Main function
def main():
    global rx_ip, rx_pass, rx_host, ch, bw, beacon, tx_nss, iface, filter, use_tcp, no_config
    
    # Load configuration
    load_config()
    
    # Handle shutdown signal
    signal.signal(signal.SIGINT, handle_shutdown)
    
    # Configure the router
    if not no_config:
        print("Configuring Receiver...")
        reconfigure()
    
    # Start CSI collection
    print("Starting CSI collection")
    sockfd = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sockfd.settimeout(1.0)
    
    # Set socket options to match C++ version
    sockfd.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    sockfd.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        sockfd.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
    except AttributeError:
        # SO_REUSEPORT might not be available on all platforms
        print("SO_REUSEPORT not supported on this platform")
    
    try:
        sockfd.bind(("0.0.0.0", PORT))
        print(f"Listening on port {PORT}")
    except Exception as e:
        print(f"Failed to bind to port {PORT}: {e}")
        sys.exit(1)
    
    # Start MQTT client loop
    CLIENT.loop_start()
    
    # Main processing loop
    while True:
        try:
            data, addr = sockfd.recvfrom(CSI_BUF_SIZE)
            if data:
                parse_csi(data, len(data))
        except socket.timeout:
            print("Socket Timeout - No CSI data received")
            # Handle reconnection
            reload_router()
            print("Reloading Router...")
            time.sleep(3)
            reconnect()
            continue
        except KeyboardInterrupt:
            print("Interrupted by user")
            break
        except Exception as e:
            print(f"Socket Error: {e}")
            continue

if __name__ == "__main__":
    CLIENT = connect_mqtt()
    CLIENT.loop_start()
    main()