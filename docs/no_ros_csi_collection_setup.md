# NoROS CSI collection with a concurrent AP+CSI router

This guide describes the reproducible setup for collecting Pixel sensor data and CSI from every ASUS RT-AC86U in a deployment. One router keeps a Wi-Fi BSS available to the Pixel while collecting CSI; the remaining routers collect passively on the same channel. Raspberry Pis assemble the CSI without ROS and publish it to the central MQTT and Parquet pipeline.

The AP+CSI role is configurable. It does not depend on a router being named AP-6, `router-1`, or having a particular IP address.

## 1. Supported and tested configuration

The concurrent driver is specific to this platform:

- ASUS RT-AC86U
- Broadcom BCM4366c0
- firmware target `10_10_122_20`
- router kernel `4.1.27`
- 5 GHz channel 36 at 80 MHz
- four transmit streams and four receive cores
- Pixel frames selected by their Wi-Fi MAC address
- CSI extraction minimum interval of 100 ms
- NoROS receivers running on Raspberry Pis

Do not load the concurrent module on a different chipset, firmware target, kernel, or stock `dhd.ko` without rebuilding and validating it. The scripts refuse known hash mismatches, but a correct hash does not make an artifact compatible with different hardware.

The tested concurrent module has MD5:

```text
9ff275147762c7c7f53dc78f757cb55c
```

The tested router's stock module has MD5:

```text
ac4f5be9e63816e1eea59490853c1b6b
```

These are reference values for the tested artifacts, not universal BCM4366 hashes. Record and configure the hashes of the exact artifacts used in each deployment.

At the 100 ms safety limit, a controlled 5 Hz stream produced approximately 50–53 complete matrices per ten seconds. Two staggered 5 Hz streams produced approximately 61–79 matrices per ten seconds during a sustained test. The configured interval is a safety ceiling, not a guaranteed output rate; RF conditions and qualifying phone traffic determine the actual rate.

## 2. Architecture

```text
                                      eduroam
 Pixel 8a                                                    Central computer
  - discrete GPS/IMU/Wi-Fi samples                           - MQTT broker
  - ordinary Wi-Fi data frames                               - ingestion matcher
            |                                                - MinIO
            | WIRES-AP                                       - local Parquet archive
            v                                                       ^
 +-----------------------+       UDP :5500       +----------------+  | MQTT /csi
 | concurrent AP+CSI     |---------------------->| paired RPi     |--+
 | RT-AC86U              |                       | NoROS receiver |
 | BSS remains up        |                       | MQTT relay     |
 +-----------------------+                       +----------------+
            :                                                       ^
            : same-channel frames                                  |
     . . . .:. . . . . . . . . . . . . . . . . . . . . . . . .  |
            :                                                       |
 +-----------------------+       UDP :5500       +----------------+ |
 | passive CSI RT-AC86U  |---------------------->| paired RPi     |-+
 | monitor mode, BSS down|                       | NoROS receiver |
 +-----------------------+                       +----------------+
            :                                                       ^
            :                                                       |
 +-----------------------+       UDP :5500       +----------------+ |
 | passive CSI RT-AC86U  |---------------------->| paired RPi     |-+
 | monitor mode, BSS down|                       | NoROS receiver |
 +-----------------------+                       +----------------+
```

Every qualifying 802.11 data frame can produce one CSI matrix on every router that hears it. CSI collection remains enabled continuously, but it is traffic-driven: no matching frame means no new matrix. A phone collection session does not arm or disarm the routers.

For an 80 MHz, 4-by-4 configuration, a router emits 16 UDP datagrams for one sequence number. Its paired RPi combines them into one `4 x 4 x 256` matrix, containing 4,096 real and 4,096 imaginary values, and publishes the completed matrix to MQTT.

The Pixel publishes a discrete sensor record when the user confirms a collection point. Central ingestion matches that record with one fresh matrix from every configured router, writes one Parquet record, and returns an acknowledgement to the Pixel.

The RPi paired with the AP+CSI router has two jobs:

1. Run the same NoROS CSI receiver as every other Pi.
2. Expose an MQTT listener on the isolated router LAN so the Pixel can send application data, then bridge the required topics to the central broker over eduroam.

Do not stop the CSI receiver on this Pi when enabling its MQTT relay.

## 3. Plan the deployment

Create a table before configuring anything. Router IDs must be unique and must match the central `CSI_ROUTER_IDS` list exactly.

The current three-router lab layout is an example:

| Logical ID | Role | Router LAN IP | Paired RPi Ethernet IP | Paired RPi eduroam IP |
| --- | --- | --- | --- | --- |
| `router-1` | AP+CSI | `192.168.48.6` | `192.168.48.20/24` | assigned by eduroam |
| `router-2` | passive CSI | `192.168.48.5` | `192.168.48.100/24` | assigned by eduroam |
| `router-3` | passive CSI | `192.168.48.1` | `192.168.48.30/24` | assigned by eduroam |

For a six-router deployment, add three more router/RPi pairs and configure the central list as `router-1,router-2,router-3,router-4,router-5,router-6`. During a three-router test, use only the three live IDs. Ingestion waits for every listed router, so listing an offline router prevents a point from being completed.

Choose and record these deployment-wide values:

- SSID; the tested and default value is `WIRES-AP`.
- control channel and bandwidth; every router must use the same values.
- Pixel Wi-Fi MAC used in 802.11 frames.
- central computer's eduroam IP and MQTT port.
- one stable Ethernet IP for every paired RPi.
- one unique logical router ID and receiver ID per pair.

On Android, either configure the `WIRES-AP` network to use the device MAC or record the randomized MAC shown for that network. Use that same address in every router and RPi capture filter. If Android changes the address, all filters must be updated.

## 4. Prepare the central pipeline

The central application and ingestion services are in the `Server-central` repository. On the central computer, create `.env` before building the Android app or starting ingestion:

```dotenv
EXPO_PUBLIC_MQTT_URL=mqtt://192.168.48.20:1883
EXPO_PUBLIC_CSI_ROUTER_IDS=router-1,router-2,router-3
CSI_ROUTER_IDS=router-1,router-2,router-3
CSI_TIMESTAMP_MARGIN_MS=40000
```

`EXPO_PUBLIC_MQTT_URL` points to the Ethernet address of the AP+CSI router's paired Pi, not to the central computer. Expo embeds `EXPO_PUBLIC_*` values at build time.

`CSI_TIMESTAMP_MARGIN_MS` is the maximum server-side window for waiting for a qualifying matrix after a phone sample. It is not a CSI sampling interval and does not throttle the continuous router streams.

Start the central services:

```sh
cd Server-central
docker-compose up -d --build
docker-compose ps
```

The central computer must accept inbound TCP port 1883 from every RPi's eduroam interface. Record its current eduroam address; that address is used in every NoROS JSON configuration and in the AP+CSI Pi's Mosquitto bridge.

The ingestion service stores each completed record in both locations:

- MinIO bucket `wl-data` by default.
- `Server-central/server/data/parquet/<device-id>/<year>/<month>/<day>/*.parquet` on the central computer.

## 5. Prepare every router

The routers need SSH enabled and persistent `/jffs` storage. Configure this in the ASUS interface before installing CSI files.

All routers must listen on the same channel and bandwidth as `WIRES-AP`. The AP+CSI router owns the live channel; `configcsi-ap.sh` deliberately refuses to retune it. Passive routers are tuned to the same channel by `configcsi.sh`.

Copy the router utilities to each router:

```sh
cd wiros_csi_node
scp -r nexmon_firmware/csi <router-user>@<router-ip>:/jffs/
ssh <router-user>@<router-ip> 'chmod 755 /jffs/csi/*.sh /jffs/csi/nexutil /jffs/csi/makecsiparams'
```

The `nexmon_firmware/csi/dhd.ko` file is the passive CSI module. Do not use it as the AP+CSI candidate, and do not copy the concurrent module over it on the passive routers.

## 6. Configure passive CSI routers

Run this once on each passive router after the router has booted:

```sh
ssh <router-user>@<router-ip> \
  '/jffs/csi/setup.sh <channel> <bandwidth-mhz> 4 <pixel-wifi-mac>'
```

For the tested radio configuration:

```sh
ssh <router-user>@<router-ip> \
  '/jffs/csi/setup.sh 36 80 4 20:f0:94:2a:7d:47'
```

This path loads the passive driver once per router boot, enables monitor mode, brings the capture-radio BSS down, and reattaches the radio interfaces to `br0` so CSI UDP broadcasts reach the paired RPi.

The setup is intentionally different from the concurrent path. Never run `setup.sh` or `configcsi.sh` on the router assigned the AP+CSI role; those scripts bring the BSS down.

Verify a passive router:

```sh
ssh <router-user>@<router-ip> '
  /usr/sbin/wl -i eth6 chanspec
  /jffs/csi/nexutil -I eth6 -g501 -l2
  brctl show br0
'
```

The channel must match the AP+CSI router, CSI state must begin with `01 00`, and `eth6` must be attached to the bridge.

`reload.sh` uses a marker under `/tmp`, so it reloads the passive driver only once in one router boot. Run the setup command again after every passive-router reboot.

## 7. Use or build the concurrent AP+CSI module

The exact tested module is committed at `nexmon_firmware/csi/dhd-ap-corebatch-10hz.ko`. Its MD5 is `9ff275147762c7c7f53dc78f757cb55c`; use that file for the validated deployment. Rebuild only to reproduce the artifact or develop another candidate.

The tested source is based on:

- Nexmon base commit `0a5de391b20c237a064d8e5f433fda91c8495a80`.
- Nexmon-CSI submodule commit `fdb25ef0e4e1402e968bb644d4914ad1a3d0a84d`.
- The maintained source delta at `nexmon_firmware/ap-csi-concurrent.patch`.

The build uses Ubuntu 18.04 on `linux/amd64`. On an ARM Mac, keep the explicit platform selection. Start in the `wiros_csi_node` repository:

```sh
git submodule update --init nexmon_firmware/nexmon_csi
git -C nexmon_firmware/nexmon_csi apply --check ../ap-csi-concurrent.patch
git -C nexmon_firmware/nexmon_csi apply ../ap-csi-concurrent.patch
mkdir -p .build
git clone https://github.com/seemoo-lab/nexmon.git .build/nexmon
git -C .build/nexmon checkout 0a5de391b20c237a064d8e5f433fda91c8495a80
```

Build a reusable tool container:

```sh
docker build --platform linux/amd64 -t wires-nexmon-builder:18.04 -f - . <<'DOCKERFILE'
FROM ubuntu:18.04
RUN dpkg --add-architecture i386 \
 && apt-get update \
 && DEBIAN_FRONTEND=noninteractive apt-get install -y \
      git make gcc g++ gawk qpdf flex bison xxd patch zlib1g-dev \
      zlib1g-dev:i386 libc6:i386 libncurses5:i386 libstdc++6:i386 \
 && rm -rf /var/lib/apt/lists/*
CMD ["tail", "-f", "/dev/null"]
DOCKERFILE
```

Prepare Nexmon and build the module. The nested source is mounted over Nexmon's BCM4366 patch directory so the build uses this repository's changes:

```sh
docker run --rm --platform linux/amd64 \
  -v "$PWD/.build/nexmon:/nexmon" \
  -v "$PWD/nexmon_firmware/nexmon_csi:/nexmon/patches/bcm4366c0/10_10_122_20/nexmon_csi" \
  wires-nexmon-builder:18.04 \
  bash -lc 'cd /nexmon && source setup_env.sh && make -j2'

docker run --rm --platform linux/amd64 \
  -v "$PWD/.build/nexmon:/nexmon" \
  -v "$PWD/nexmon_firmware/nexmon_csi:/nexmon/patches/bcm4366c0/10_10_122_20/nexmon_csi" \
  wires-nexmon-builder:18.04 \
  bash -lc 'source /nexmon/setup_env.sh && cd /nexmon/patches/bcm4366c0/10_10_122_20/nexmon_csi && make clean && make -j1'
```

Use the sequential `-j1` module build. The output is:

```text
nexmon_firmware/nexmon_csi/dhd.ko
```

Record the repository revisions and the artifact checksum with the experiment or release:

```sh
git rev-parse HEAD
git -C nexmon_firmware/nexmon_csi rev-parse HEAD
md5 -q nexmon_firmware/nexmon_csi/dhd.ko       # macOS
md5sum nexmon_firmware/nexmon_csi/dhd.ko       # Linux
```

The module embeds build metadata, so a new build need not reproduce the reference MD5 byte-for-byte. Configure `AP_CSI_EXPECTED_CANDIDATE_MD5` with the checksum of the artifact that was actually reviewed and deployed.

## 8. Configure the AP+CSI router

First configure the router's ordinary wireless service in the ASUS interface:

- Set the LAN address selected in the deployment table.
- Enable only the intended 5 GHz BSS.
- Set its SSID to `WIRES-AP`.
- Fix the control channel and bandwidth; the tested values are 36 and 80 MHz.
- Ensure the Pixel receives an address and can reach the paired RPi's Ethernet address.

The tested lab router provides DHCP on its isolated LAN. An equivalent static-address arrangement also works as long as the Pixel and paired RPi can reach each other.

Wait for the stock wireless driver and BSS to finish booting before the first concurrent load.

### 8.1 Preserve the stock module

On the AP+CSI router, create a persistent backup before loading the candidate:

```sh
ssh <router-user>@<ap-csi-router-ip> '
  cp /lib/modules/4.1.27/extra/dhd.ko /jffs/csi/dhd-stock-original-ac4f5be9.ko
  md5sum /lib/modules/4.1.27/extra/dhd.ko /jffs/csi/dhd-stock-original-ac4f5be9.ko
'
```

Keep this file. The recovery path uses it if the underlying stock path is ever incorrect.

### 8.2 Install the candidate and AP-safe scripts

Copy the committed, checksum-verified candidate:

```sh
scp nexmon_firmware/csi/dhd-ap-corebatch-10hz.ko \
  <router-user>@<ap-csi-router-ip>:/jffs/csi/dhd-ap-corebatch-10hz.ko
```

If deploying a new build instead, give it a distinct filename, calculate its MD5, and update both candidate values below.

Copy and edit the environment template locally:

```sh
cp nexmon_firmware/csi/ap-csi.env.example ap-csi.env
```

At minimum, verify or change:

```dotenv
AP_CSI_MODULE_PATH=/lib/modules/4.1.27/extra/dhd.ko
AP_CSI_CANDIDATE=/jffs/csi/dhd-ap-corebatch-10hz.ko
AP_CSI_STOCK_BACKUP=/jffs/csi/dhd-stock-original-ac4f5be9.ko
AP_CSI_EXPECTED_CANDIDATE_MD5=9ff275147762c7c7f53dc78f757cb55c
AP_CSI_EXPECTED_STOCK_MD5=ac4f5be9e63816e1eea59490853c1b6b
AP_CSI_INTERFACE=eth6
AP_CSI_SSID=WIRES-AP
AP_CSI_NVRAM_PREFIX=wl1
AP_CSI_DISABLED_INTERFACES=eth5
AP_CSI_DISABLED_NVRAM_PREFIXES=wl0
AP_CSI_CHANNEL=36
AP_CSI_BANDWIDTH_MHZ=80
AP_CSI_TX_STREAMS=4
AP_CSI_RX_CORES=4
AP_CSI_TARGET_MAC=<pixel-wifi-mac>
AP_CSI_MIN_INTERVAL_MS=100
```

Then install it:

```sh
scp ap-csi.env <router-user>@<ap-csi-router-ip>:/jffs/csi/ap-csi.env
scp nexmon_firmware/csi/ap-csi-autostart.sh \
  nexmon_firmware/csi/configcsi-ap.sh \
  <router-user>@<ap-csi-router-ip>:/jffs/csi/
ssh <router-user>@<ap-csi-router-ip> \
  'chmod 600 /jffs/csi/ap-csi.env && chmod 755 /jffs/csi/ap-csi-autostart.sh /jffs/csi/configcsi-ap.sh'
```

Run the idempotent installer once:

```sh
ssh <router-user>@<ap-csi-router-ip> /jffs/csi/ap-csi-autostart.sh
```

The script does not permanently overwrite `/lib/modules/4.1.27/extra/dhd.ko`. It unloads the stock module, bind-mounts the candidate over the module path for the current boot, reloads wireless, preserves the AP BSS and channel, and enables CSI. A normal reboot returns to the stock file first; the paired RPi watchdog performs the late switch again.

If installation or validation fails after the candidate is loaded, the script attempts to unload it, restore the stock module, restart wireless, and exits unsuccessfully. If safe rollback cannot complete, it reboots the router so it returns to stock.

## 9. Configure each paired RPi

Each Pi uses Ethernet only for its paired router and Wi-Fi for eduroam. The Ethernet connection must not install a default route.

Install the repository and Python environment:

```sh
sudo install -d -o "$USER" -g "$USER" /opt/wiros_csi_node
git clone --recurse-submodules <wiros-csi-node-repository-url> /opt/wiros_csi_node
cd /opt/wiros_csi_node
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

Configure the Ethernet profile using the address from the deployment table:

```sh
cd /opt/wiros_csi_node
./deploy/configure-pi-ethernet.sh <rpi-ethernet-address>/24
ip -4 address show eth0
ip route
```

Create `/opt/wiros_csi_node/src/pythonNoRos/config.json` separately on every Pi. This example is for one pair:

```json
{
  "router": {
    "id": "router-1",
    "host": "192.168.48.6",
    "username": "wiloc",
    "password": ""
  },
  "receiver": {
    "id": "rpi-1",
    "bind": "0.0.0.0",
    "port": 5500,
    "ethernet_address": "192.168.48.20/24",
    "receive_buffer_bytes": 4194304
  },
  "capture": {
    "channel": 36,
    "bandwidth_mhz": 80,
    "tx_streams": 4,
    "mac_filter": "20:f0:94:2a:7d:47"
  },
  "mqtt": {
    "host": "<central-eduroam-ip>",
    "port": 1883,
    "transport": "tcp",
    "topic": "/csi",
    "qos": 0,
    "max_queued_messages": 1000,
    "max_inflight_messages": 100,
    "connect_timeout_seconds": 10,
    "publish_timeout_seconds": 10
  }
}
```

The `router.id`, `receiver.id`, router address, RPi Ethernet address, and central address are per deployment. The capture channel, bandwidth, stream count, and Pixel MAC must agree across every pair.

Install the generic service:

```sh
sudo install -m 0644 deploy/wiros-csi-node.service \
  /etc/systemd/system/wiros-csi-node.service
sudo systemctl daemon-reload
sudo systemctl enable --now wiros-csi-node.service
systemctl is-active wiros-csi-node.service
journalctl -u wiros-csi-node.service -n 50 --no-pager
```

The supplied unit runs as user `wiloc` from `/opt/wiros_csi_node`. Change `User`, ownership, and paths together if another account or install path is used.

The receiver listens for UDP port 5500, decodes Nexmon frames, groups all 16 stream/core packets for one MAC and sequence, and publishes only completed matrices. It does not require ROS.

## 10. Configure the Pixel MQTT relay

Perform this only on the Pi paired with the AP+CSI router. Install Mosquitto:

```sh
sudo apt-get update
sudo apt-get install -y mosquitto mosquitto-clients
```

Create `/etc/mosquitto/conf.d/wiros-relay.conf`, substituting the AP-side RPi address and current central eduroam address:

```text
listener 1883 <ap-csi-rpi-ethernet-ip>
allow_anonymous true

connection wires-laptop-eduroam
address <central-eduroam-ip>:1883
bridge_protocol_version mqttv311
remote_clientid <unique-rpi-relay-id>
try_private false
notifications false
restart_timeout 5

topic /csi out 1
topic /csi-trigger out 1
topic /ml-training out 1
topic /gps/raw out 1

topic /gps/corrected in 1
topic csi/ack/# in 1
```

Validate and start it:

```sh
sudo systemctl enable mosquitto
sudo systemctl restart mosquitto
systemctl is-active mosquitto
journalctl -u mosquitto -n 50 --no-pager
ss -ltn | grep ':1883'
```

Confirm that the NoROS receiver on this same Pi remains active. Mosquitto carries the Pixel's application messages; it does not replace the CSI receiver.

If the central computer receives a different eduroam address, update both this bridge address and every Pi's NoROS `mqtt.host`, then restart the affected services.

## 11. Install AP+CSI reboot recovery on its paired RPi

The watchdog waits for the router's stock boot to settle, invokes the router-local idempotent installer, checks it periodically, and reboots the router after repeated or timed-out failures.

Install its dependency and files on the AP+CSI Pi:

```sh
sudo apt-get install -y sshpass
cd /opt/wiros_csi_node
sudo install -m 0755 nexmon_firmware/csi/ap-csi-rpi-watchdog.sh \
  /usr/local/sbin/ap-csi-rpi-watchdog
sudo install -m 0644 nexmon_firmware/csi/ap-csi-rpi-watchdog.service \
  /etc/systemd/system/ap-csi-rpi-watchdog.service
sudo cp nexmon_firmware/csi/ap-csi-rpi-watchdog.env.example \
  /etc/default/ap-csi-rpi-watchdog
```

Edit `/etc/default/ap-csi-rpi-watchdog` and set at least:

```dotenv
AP_CSI_ROUTER_HOST=<ap-csi-router-ip>
AP_CSI_SOURCE_IP=<paired-rpi-ethernet-ip>
AP_CSI_LINK_INTERFACE=eth0
AP_CSI_ROUTER_USER=<router-user>
AP_CSI_ROUTER_LABEL=<logical-router-id>
AP_CSI_PASSWORD_FILE=/home/<rpi-user>/.config/wiros/ap_csi_router_password
AP_CSI_KNOWN_HOSTS=/home/<rpi-user>/.ssh/ap_csi_router_known_hosts
AP_CSI_REMOTE_COMMAND=/jffs/csi/ap-csi-autostart.sh
AP_CSI_BOOT_SETTLE=120
AP_CSI_HEALTH_INTERVAL=60
AP_CSI_RETRY_DELAY=20
AP_CSI_COMMAND_TIMEOUT=180
AP_CSI_MAX_FAILURES=3
```

Create the protected router password file. It must contain only the password and a trailing newline:

```sh
install -d -m 0700 /home/<rpi-user>/.config/wiros
printf '%s\n' '<router-password>' > /home/<rpi-user>/.config/wiros/ap_csi_router_password
chmod 600 /home/<rpi-user>/.config/wiros/ap_csi_router_password
install -d -m 0700 /home/<rpi-user>/.ssh
```

If the RPi account is not `wiloc`, also change `User` and `Group` in the installed service file. Then enable the watchdog:

```sh
sudo systemctl daemon-reload
sudo systemctl enable --now ap-csi-rpi-watchdog.service
systemctl is-enabled ap-csi-rpi-watchdog.service
systemctl is-active ap-csi-rpi-watchdog.service
journalctl -u ap-csi-rpi-watchdog.service -n 50 --no-pager
```

After a router reboot, seeing a `stock boot to settle` message for up to `AP_CSI_BOOT_SETTLE` seconds is expected. The Pixel can reconnect to the stock `WIRES-AP` BSS first; the late driver switch follows.

## 12. Build and connect the Android application

On the central development computer, connect the Pixel over USB and verify that `.env` contains the AP+CSI Pi's Ethernet MQTT address and the exact router list.

```sh
cd Server-central
npm ci
adb devices
npx expo run:android --device
```

Reconnect or rebuild after changing an `EXPO_PUBLIC_*` value. On the Pixel:

1. Connect to `WIRES-AP`.
2. Verify that the phone can reach the AP+CSI Pi's Ethernet address.
3. Verify the Wi-Fi MAC mode matches `capture.mac_filter` and `AP_CSI_TARGET_MAC`.
4. Open the second option, the data collection screen.
5. Select the correct floor, tap a location, inspect the sensor preview, and confirm the point.

Phone sampling is discrete, one record per confirmed point. CSI collection on the routers remains enabled between points and across 5–10 second movement sessions.

## 13. Start-up order

Use this order for a cold start:

1. Start the central computer and run `docker-compose up -d` in `Server-central`.
2. Confirm the central MQTT port is reachable from eduroam.
3. Start all Raspberry Pis and confirm their eduroam and Ethernet addresses.
4. Start all routers.
5. Wait for the AP+CSI watchdog to report healthy.
6. Run `setup.sh` once on every passive router after its boot.
7. Restart or verify every NoROS receiver.
8. Connect the Pixel to `WIRES-AP` and open the data collection screen.
9. Confirm all expected router IDs are reporting before collecting points.

The AP+CSI router recovers automatically through its paired RPi watchdog. The current passive path requires its setup command once after each passive-router reboot.

## 14. Validation

### 14.1 Router checks

On the AP+CSI router:

```sh
/usr/sbin/wl -i eth6 ssid
/usr/sbin/wl -i eth6 chanspec
/usr/sbin/wl -i eth6 bss
md5sum /lib/modules/4.1.27/extra/dhd.ko
/jffs/csi/nexutil -I eth6 -g501 -l20
tail -n 50 /tmp/ap-csi-autostart.log
```

Expected results are `WIRES-AP`, the configured channel and bandwidth, BSS `up`, the candidate's active MD5, and CSI state `01 00`.

The ten 16-bit values returned by ioctl 501 with length 20 are:

1. CSI enabled state.
2. firmware ready counter.
3. last processed ready counter.
4. observed ready events.
5. PHY table reads.
6. emitted CSI UDP packets.
7. rate-limited events.
8. configured minimum interval in milliseconds.
9. packet-allocation failures.
10. transmit failures.

Counters wrap at 65,536. With all four cores and streams, accepted matrices should account for four table reads and sixteen emitted packets each. Allocation and transmit failure counters should remain zero.

### 14.2 RPi checks

On every Pi:

```sh
systemctl is-active wiros-csi-node.service
journalctl -u wiros-csi-node.service -n 100 --no-pager
```

The receiver logs ten-second statistics. Under qualifying traffic, `matrices` must increase and invalid/incomplete datagrams must not dominate the log.

On the AP+CSI Pi, also verify:

```sh
systemctl is-active mosquitto
systemctl is-active ap-csi-rpi-watchdog.service
journalctl -u ap-csi-rpi-watchdog.service -n 100 --no-pager
```

### 14.3 Central checks

On the central computer:

```sh
cd Server-central
docker-compose ps
docker-compose logs --tail=100 ingestion
```

After one confirmed Pixel point, ingestion should log that the phone sample was matched and then stored. Find the newest local archive:

```sh
find server/data/parquet -type f -name '*.parquet' -print | sort | tail -1
```

A valid three-router record contains Pixel `GPS`, `IMU`, `WiFi`, and `ground_truth` fields plus `CSI` entries for `router-1`, `router-2`, and `router-3`. Each router entry should report `n_tx=4`, `n_rx=4`, `n_subcarriers=256`, and arrays of length 4,096. A six-router deployment must contain all six configured entries.

### 14.4 Reboot acceptance test

Before treating a deployment as ready:

1. Collect and verify one complete point.
2. Reboot the AP+CSI router without changing its underlying stock module.
3. Confirm `WIRES-AP` returns and the Pixel reconnects.
4. Confirm the RPi watchdog performs the late candidate switch and reports healthy.
5. Re-run the AP and RPi checks.
6. Reconfigure any passive router that was also rebooted.
7. Collect another point and verify a new complete Parquet file.

This test proves both connectivity and data persistence. Seeing matrices in a relay log alone does not prove that Pixel data, all routers, ingestion matching, acknowledgement, and Parquet storage work together.

## 15. Focused troubleshooting

### `WIRES-AP` is missing

- Confirm the router booted its stock driver before the watchdog attempted the switch.
- Check `wl -i eth6 bss`, `wl -i eth6 ssid`, and `wl -i eth6 chanspec`.
- Check `/tmp/ap-csi-autostart.log` and the RPi watchdog journal.
- Confirm no passive `setup.sh` or `configcsi.sh` command was run on the AP+CSI router.

### The Pixel connects but application MQTT fails

- Confirm `EXPO_PUBLIC_MQTT_URL` was present when the app was built.
- Confirm Mosquitto listens on the AP+CSI Pi's Ethernet address.
- Confirm the Pixel and Pi are in the same IPv4 subnet.
- Confirm the Mosquitto bridge uses the central computer's current eduroam IP.
- Confirm TCP 1883 is reachable from the Pi to the central computer.

### A router reports zero CSI

- Confirm its channel and bandwidth match `WIRES-AP`.
- Confirm ioctl 501 begins with `01 00`.
- Confirm the Pixel is transmitting qualifying data frames; CSI is traffic-driven.
- Confirm the Pixel's current Wi-Fi MAC matches both router and RPi filters.
- On a passive router, confirm the capture interface was reattached to `br0`.

### Matrices are incomplete or slow

- One full 4-by-4 matrix requires all 16 datagrams with the same sequence.
- Check the RPi receiver's `datagrams`, `accepted_streams`, `matrices`, and `pending` statistics.
- Keep `receive_buffer_bytes` at least 4 MiB.
- On the AP+CSI router, confirm four table reads and sixteen emitted packets per accepted matrix and zero allocation/transmit failures.
- Do not lower `AP_CSI_MIN_INTERVAL_MS` below the tested 100 ms without repeating packet-loss, latency, soak, and reboot tests.

### The phone sample remains buffered and no Parquet appears

- Ensure central `CSI_ROUTER_IDS` contains exactly the active router IDs.
- Ensure every Pi publishes a unique `router.id` to the same central broker.
- Check the ingestion log's nearest-router deltas.
- Confirm the MinIO and ingestion containers are healthy.
- Confirm the phone receives `csi/ack/<sample-id>` through the Pi bridge.

### The concurrent driver is rejected

- Compare the candidate, active, stock, and configured expected MD5 values.
- Confirm the target is BCM4366c0 firmware `10_10_122_20` on kernel `4.1.27`.
- Do not bypass the hash checks. Build or deploy the correct artifact and update the environment file deliberately.

---

*Documented by Codex.*
