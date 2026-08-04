# Operational diagnostics

These scripts connect to the two CSI Raspberry Pis over SSH. They require
Python with `paramiko` installed and the `WIRES_PI_PASSWORD` environment
variable.

- `csi_rate_diagnostic.py` checks each CSI systemd service and configuration,
  shows recent service logs, and counts incoming UDP CSI packets on port 5500.
- `pi_diagnostic.py` compares each Pi clock with the computer running the
  script and reports round-trip time and NTP synchronization status.
- `router_diagnostic.py` connects to each router through its associated Pi and
  runs the router's `/jffs/csi/setup.sh` CSI configuration command.

Run a diagnostic from the repository root, for example:

```powershell
$env:WIRES_PI_PASSWORD='your-password'
py -3.13 tools/csi_rate_diagnostic.py
```

The deployment utility is in `deploy/deploy_csi_matrix.py`. It uploads the
current `csi.py` and `nexcsiserver.py` to both Pis, syntax-checks them, creates
timestamped backups, restarts the CSI services, and verifies that they are
active.
