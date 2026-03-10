#!/usr/bin/env bash
# setup.sh — Install all dependencies for bt_monitor on Raspberry Pi 3B+ (Raspberry Pi OS Bookworm/Bullseye)
set -e

echo "═══════════════════════════════════════════════"
echo "  BT Monitor — Raspberry Pi Setup"
echo "═══════════════════════════════════════════════"

# ── 1. System packages ──
echo "[1/5] Updating package list…"
sudo apt-get update -q

echo "[2/5] Installing system dependencies…"
sudo apt-get install -y \
    bluez \
    bluez-tools \
    python3 \
    python3-pip \
    alsa-utils \
    pulseaudio \
    pulseaudio-module-bluetooth \
    blueman \
    rfkill

# ── 2. Ensure BT is not blocked ──
echo "[3/5] Unblocking Bluetooth…"
sudo rfkill unblock bluetooth || true

# ── 3. Python packages ──
echo "[4/5] Installing Python packages…"
pip3 install --break-system-packages pyserial 2>/dev/null || true
# (no extra Python packages required; stdlib only)

# ── 4. Permissions for HCI raw access ──
echo "[5/5] Granting HCI caps to python3 for non-root RSSI reads…"
# This lets python3 call hcitool rssi without full sudo
sudo setcap 'cap_net_raw,cap_net_admin+eip' "$(which hcitool)" 2>/dev/null || \
    echo "   [WARN] setcap on hcitool failed — run bt_monitor.py with sudo instead."
sudo setcap 'cap_net_raw+eip' "$(which l2ping)"   2>/dev/null || true

echo ""
echo "✔  Setup complete."
echo ""
echo "Next steps:"
echo "  1. Pair your earbuds:  bluetoothctl"
echo "     → power on / agent on / scan on / pair <MAC> / trust <MAC> / connect <MAC>"
echo "  2. Find your Classic BT MAC:"
echo "     bluetoothctl paired-devices"
echo "  3. Run the monitor:"
echo "     sudo python3 bt_monitor.py --mac AA:BB:CC:DD:EE:FF"
