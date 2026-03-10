# bt-monitor 🎧📡

A Raspberry Pi 3B+ tool that plays a **continuous test tone** to Bluetooth earbuds and logs
**RSSI, Link Quality, TX Power, Packet Loss and HCI error counters** to a CSV file.

Tested with: Raspberry Pi OS Bookworm/Bullseye · BlueZ 5.x · Minor IV earbuds (A2DP Classic BT)

---

## Repository contents

```
bt_monitor.py   Main monitor script (tone + metrics logging)
plot_log.py     Optional: plot bt_log.csv with matplotlib
setup.sh        One-shot dependency installer
README.md       This file
```

---

## Understanding the two MAC addresses 🔑

This is the most common source of confusion. Your earbuds advertise **two different Bluetooth radios**:

| Radio | Protocol | Used for | Address type |
|-------|----------|----------|--------------|
| **Classic BT (BR/EDR)** | A2DP / HFP | **Audio streaming** ← use this | Static, e.g. `AA:BB:CC:DD:EE:FF` |
| **BLE (LE)** | GATT / BLE Audio | App control, pairing assist | Random / rotating |

**Always use the Classic BT MAC for audio and for this tool.**

### How to find the correct MAC

```bash
# Scan for Classic BT devices (BR/EDR)
hcitool scan

# Or, if already paired:
bluetoothctl paired-devices
```

The address in `paired-devices` is the Classic BT MAC — it's the one that supports A2DP and
will have a live ACL connection when audio is playing.

> **BLE MACs** show up in `hcitool lescan` or `bluetoothctl scan le`. They are **not** used
> for audio streaming and `hcitool rssi` will return an error on them.

---

## Quick start

### 1. Install dependencies

```bash
chmod +x setup.sh
sudo ./setup.sh
```

Or manually:

```bash
sudo apt-get update
sudo apt-get install -y bluez bluez-tools alsa-utils pulseaudio pulseaudio-module-bluetooth
```

### 2. Pair and connect your earbuds

```bash
bluetoothctl
```

Inside the bluetoothctl prompt:

```
power on
agent on
default-agent
scan on
```

Wait until you see your earbuds listed, e.g.:
```
[NEW] Device AA:BB:CC:DD:EE:FF Minor IV
```

Then:
```
scan off
pair AA:BB:CC:DD:EE:FF
trust AA:BB:CC:DD:EE:FF
connect AA:BB:CC:DD:EE:FF
exit
```

### 3. Verify the A2DP audio sink is available

```bash
pactl list sinks | grep -A2 bluez
```

You should see a sink like `bluez_sink.AA_BB_CC_DD_EE_FF.a2dp_sink`.

If not, force BlueZ to expose the A2DP sink:

```bash
pacmd set-default-sink bluez_sink.AA_BB_CC_DD_EE_FF.a2dp_sink
```

### 4. Run the monitor

```bash
sudo python3 bt_monitor.py --mac AA:BB:CC:DD:EE:FF
```

**Common options:**

| Flag | Default | Description |
|------|---------|-------------|
| `--mac` | *(required)* | Classic BT MAC of your earbuds |
| `--interval` | `2` | Seconds between measurements |
| `--duration` | `0` (∞) | Stop after N seconds |
| `--out` | `bt_log.csv` | Output CSV path |
| `--tone-freq` | `1000` | Sine tone frequency in Hz |
| `--skip-ping` | off | Skip l2ping (less disruptive to audio) |

Example — run for 5 minutes, poll every 3 s, skip l2ping:

```bash
sudo python3 bt_monitor.py --mac AA:BB:CC:DD:EE:FF --interval 3 --duration 300 --skip-ping
```

Press **Ctrl+C** to stop early.

---

## Output CSV columns

| Column | Unit | Notes |
|--------|------|-------|
| `timestamp` | ISO 8601 | Local time of sample |
| `mac` | — | Device MAC |
| `rssi_dbm` | dBm | Signal strength. See RSSI notes below |
| `link_quality` | 0–255 | 255 = best. See LQ notes below |
| `tx_power_dbm` | dBm | Adapter TX power |
| `packet_loss_pct` | % | From l2ping (skipped if `--skip-ping`) |
| `hci_err_rx` | count Δ | HCI RX errors since last sample |
| `hci_err_tx` | count Δ | HCI TX errors / retransmissions since last sample |
| `hci_acl_tx` | count Δ | ACL packets TX since last sample |
| `hci_acl_rx` | count Δ | ACL packets RX since last sample |
| `notes` | — | Warnings about unexpected values |

---

## Troubleshooting known issues

### RSSI is always N/A or shows 0

**Why it happens:** `hcitool rssi` requires a live ACL connection *and* CAP_NET_RAW.
Running without `sudo` or before the device is actually connected returns nothing or errors.

**Fixes (try in order):**

1. **Use sudo** — most reliable:
   ```bash
   sudo python3 bt_monitor.py --mac AA:BB:CC:DD:EE:FF
   ```

2. **Grant raw socket capability to hcitool** (avoids full sudo):
   ```bash
   sudo setcap 'cap_net_raw,cap_net_admin+eip' $(which hcitool)
   ```

3. **Confirm the device is connected** (not just paired):
   ```bash
   hcitool con
   ```
   You must see an entry for your MAC. If not, connect first:
   ```bash
   bluetoothctl connect AA:BB:CC:DD:EE:FF
   ```

4. **BlueZ version note:** On BlueZ ≥ 5.56 some `hcitool` commands were deprecated.
   The script will print `RSSI unavailable(see README)` in the notes column when this happens.
   A workaround using `btmgmt` is planned for a future version.

5. **Verify directly:**
   ```bash
   sudo hcitool rssi AA:BB:CC:DD:EE:FF
   ```
   Expected output: `RSSI return value: -55` (a negative dBm value).
   If you see `Not connected` the earbuds disconnected. Re-connect and retry.

---

### Link Quality is always 255

**Why it happens:** 255 is the *maximum valid* value on most BR/EDR controllers — it means the
link is excellent. It's not a bug; the metric is saturated at the top of its range.
You will only see lower values when there is genuine RF interference or distance.

**To stress-test the link** (force lower LQ readings):
- Move the earbuds 5–10 m away or place obstacles (walls, body) between Pi and earbuds.
- Introduce 2.4 GHz interference by running a Wi-Fi speed test simultaneously:
  ```bash
  sudo apt-get install -y iperf3
  iperf3 -c <server_ip>   # in a second terminal while monitoring
  ```
- Wrap the earbuds in a cardboard box lined with aluminium foil for a quick attenuation test.

---

### TX Power is always 12 (or another fixed value)

**Why it happens:** `hcitool tpl` reads the controller's *maximum* TX power level, which is a
static hardware capability register — not the instantaneous power. BlueZ's power control
(Adaptive Frequency Hopping + power adjustment) happens at the radio level and is not exposed
to userspace via a simple ioctl on most controllers.

**This is expected behaviour.** The value confirms the hardware spec, not the current output.
If you need dynamic TX power data, you would need a Bluetooth protocol analyser (e.g. Ubertooth One).

---

### Audio does not route to earbuds

```bash
# List PulseAudio sinks
pactl list short sinks

# Set default sink (replace with your actual sink name)
pacmd set-default-sink bluez_sink.AA_BB_CC_DD_EE_FF.a2dp_sink

# Verify aplay can see it
aplay -l
```

If the A2DP sink is missing, restart PulseAudio with Bluetooth support:

```bash
pulseaudio --kill
pulseaudio --start
# then reconnect the earbuds
bluetoothctl connect AA:BB:CC:DD:EE:FF
```

---

### hcitool / hciconfig not found

On newer Raspberry Pi OS the classic tools may not be installed by default:

```bash
sudo apt-get install -y bluez-deprecated   # Bookworm
# or
sudo apt-get install -y bluez              # includes hcitool on Bullseye
```

---

## Plotting results (optional)

```bash
pip3 install --break-system-packages matplotlib pandas
python3 plot_log.py bt_log.csv
```

This saves `bt_log_plot.png` with four subplots: RSSI, Link Quality, Packet Loss, HCI TX errors.

---

## How the tone works

The script generates a **1000 Hz sine wave** (configurable with `--tone-freq`) as raw 16-bit PCM,
writes it to `/tmp/bt_tone.raw`, and plays it in a loop via `aplay` piped to the PulseAudio
Bluetooth sink. This keeps a continuous A2DP audio stream active so that:

- The ACL connection stays alive (earbuds don't power-save/disconnect)
- RSSI and LQ readings reflect an actively-transmitting link
- Packet counters accumulate meaningfully

---

## Requirements

- Raspberry Pi 3B+ running Raspberry Pi OS (Bullseye or Bookworm)
- Python 3.9+
- BlueZ 5.x (`bluetoothd`)
- `aplay` (ALSA utils)
- PulseAudio with Bluetooth module
- Root / sudo for RSSI and l2ping

---

## License

MIT — see LICENSE file.
