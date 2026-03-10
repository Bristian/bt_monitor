#!/usr/bin/env python3
"""
bt_monitor.py — Bluetooth link quality monitor for Raspberry Pi 3B+
Generates a continuous tone via ALSA/BlueZ and logs BT link metrics.

Metrics logged:
  - RSSI (dBm)
  - Link Quality (0–255)
  - TX Power (dBm)
  - Packet Loss estimate (%)
  - Failed TX / Retransmissions (from hci_dump counters)

Usage:
  python3 bt_monitor.py --mac AA:BB:CC:DD:EE:FF [--interval 2] [--duration 60] [--out bt_log.csv]
"""

import argparse
import csv
import math
import os
import re
import signal
import struct
import subprocess
import sys
import time
import threading
from datetime import datetime

# ──────────────────────────────────────────────
# Tone generation via ALSA (aplay)
# ──────────────────────────────────────────────

def generate_sine_wav(frequency=1000, duration_s=3600, sample_rate=44100, amplitude=0.3):
    """
    Generate a raw 16-bit signed PCM sine wave and pipe it to aplay.
    Returns the Popen object so the caller can terminate it later.
    """
    # Build a 1-second buffer then loop it with aplay --period-size
    samples_per_cycle = sample_rate
    pcm_data = bytearray()
    for i in range(samples_per_cycle):
        sample = int(amplitude * 32767 * math.sin(2 * math.pi * frequency * i / sample_rate))
        pcm_data += struct.pack('<h', sample)

    # Write buffer to a temp file and play in a loop
    wav_path = '/tmp/bt_tone.raw'
    with open(wav_path, 'wb') as f:
        f.write(pcm_data)

    cmd = [
        'aplay',
        '-t', 'raw',
        '-f', 'S16_LE',
        '-r', str(sample_rate),
        '-c', '1',       # mono
        '--period-size=4096',
        wav_path
    ]
    # Loop by re-spawning when the process ends (handled in tone_loop thread)
    return cmd, wav_path


def tone_loop(cmd, stop_event):
    """Keep playing tone until stop_event is set."""
    proc = None
    while not stop_event.is_set():
        proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        # Wait up to 1 s at a time so we can respond to stop_event
        while not stop_event.is_set() and proc.poll() is None:
            time.sleep(0.5)
        if proc.poll() is None:
            proc.terminate()
            proc.wait()
    if proc and proc.poll() is None:
        proc.terminate()


# ──────────────────────────────────────────────
# HCI helpers
# ──────────────────────────────────────────────

def get_hci_handle(mac: str) -> str | None:
    """
    Return the ACL handle (hex string) for a connected device, or None.
    Parses: hcitool con
    """
    try:
        out = subprocess.check_output(['hcitool', 'con'], text=True, stderr=subprocess.DEVNULL)
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None
    for line in out.splitlines():
        if mac.upper() in line.upper():
            # Line format: < ACL AA:BB:CC:DD:EE:FF handle 11 state 1 lm MASTER AUTH ENCRYPT
            m = re.search(r'handle\s+(\d+)', line, re.IGNORECASE)
            if m:
                return m.group(1)
    return None


def read_rssi(mac: str) -> dict:
    """
    Read RSSI, Link Quality and TX Power via hcitool rssi / lq / tpl.
    Falls back to parsing hcidump / btmgmt for RSSI on kernels that restrict it.
    """
    result = {
        'rssi': None,
        'link_quality': None,
        'tx_power': None,
    }

    # --- RSSI ---
    try:
        out = subprocess.check_output(
            ['hcitool', 'rssi', mac], text=True, stderr=subprocess.STDOUT, timeout=3
        )
        m = re.search(r'RSSI return value:\s*(-?\d+)', out)
        if m:
            result['rssi'] = int(m.group(1))
    except Exception:
        pass

    # If RSSI is still None, try btmgmt (requires bluetoothd + root)
    if result['rssi'] is None:
        try:
            out = subprocess.check_output(
                ['btmgmt', 'find'], text=True, stderr=subprocess.DEVNULL, timeout=5
            )
            # btmgmt doesn't give per-connection RSSI easily; skip
        except Exception:
            pass

    # --- Link Quality ---
    try:
        out = subprocess.check_output(
            ['hcitool', 'lq', mac], text=True, stderr=subprocess.STDOUT, timeout=3
        )
        m = re.search(r'Link quality:\s*(\d+)', out)
        if m:
            result['link_quality'] = int(m.group(1))
    except Exception:
        pass

    # --- TX Power ---
    try:
        out = subprocess.check_output(
            ['hcitool', 'tpl', mac], text=True, stderr=subprocess.STDOUT, timeout=3
        )
        m = re.search(r'TX power level:\s*(-?\d+)', out)
        if m:
            result['tx_power'] = int(m.group(1))
    except Exception:
        pass

    return result


def read_hci_stats() -> dict:
    """
    Parse /proc/bluetooth/hci0 or hciconfig hci0 for packet error counters.
    Returns dict with err_rx, err_tx, acl_tx, acl_rx.
    """
    stats = {'err_rx': None, 'err_tx': None, 'acl_tx': None, 'acl_rx': None}
    try:
        out = subprocess.check_output(
            ['hciconfig', 'hci0', 'stats'], text=True, stderr=subprocess.DEVNULL
        )
        # Example lines:
        #   ACL packets tx:1234 rx:5678
        #   SCO packets tx:0 rx:0
        #   Events rx:99
        #   Errors rx:0 tx:2
        m = re.search(r'Errors\s+rx:(\d+)\s+tx:(\d+)', out)
        if m:
            stats['err_rx'] = int(m.group(1))
            stats['err_tx'] = int(m.group(2))
        m = re.search(r'ACL\s+pkts:(\d+):(\d+)', out)
        if m:
            stats['acl_tx'] = int(m.group(1))
            stats['acl_rx'] = int(m.group(2))
    except Exception:
        pass
    return stats


def read_l2ping_loss(mac: str, count: int = 10, size: int = 44) -> float | None:
    """
    Run l2ping and return packet loss percentage.
    Requires root (uses raw HCI).
    l2ping is non-blocking so we time-box it.
    """
    try:
        out = subprocess.check_output(
            ['l2ping', '-c', str(count), '-s', str(size), mac],
            text=True,
            stderr=subprocess.STDOUT,
            timeout=count * 2 + 5,
        )
        # Line: 10 sent, 9 received, 10% loss
        m = re.search(r'(\d+)\s+sent,\s+(\d+)\s+received', out)
        if m:
            sent, recv = int(m.group(1)), int(m.group(2))
            if sent > 0:
                return round(100.0 * (sent - recv) / sent, 1)
    except subprocess.TimeoutExpired:
        return None
    except Exception:
        pass
    return None


# ──────────────────────────────────────────────
# MAC address helper — pick Classic BT MAC
# ──────────────────────────────────────────────

def discover_mac_hint(mac: str) -> str:
    """
    Print a helpful note about which MAC to use.
    Most earbuds expose two MACs:
      - Classic BT (BR/EDR) for A2DP audio  ← use this one
      - BLE (LE) for control/pairing         ← NOT for audio streaming
    The Classic MAC is usually the one returned by `bluetoothctl paired-devices`
    or `hcitool scan`.
    """
    print("\n[INFO] MAC address guidance:")
    print("  Run:  bluetoothctl paired-devices")
    print("  The address shown there is the Classic BT MAC (BR/EDR) — use that for A2DP audio.")
    print("  BLE addresses are typically random/rotated and NOT used for audio.")
    print(f"  You supplied: {mac}\n")
    return mac


# ──────────────────────────────────────────────
# Main monitor loop
# ──────────────────────────────────────────────

def monitor(mac: str, interval: float, duration: float, out_path: str, tone_freq: int, skip_ping: bool):
    discover_mac_hint(mac)

    print(f"[INFO] Starting monitor  MAC={mac}  interval={interval}s  duration={duration}s")
    print(f"[INFO] Log → {out_path}")
    print(f"[INFO] Tone frequency: {tone_freq} Hz  (streaming to BT sink)")
    print("[INFO] Press Ctrl+C to stop early.\n")

    # Start tone
    stop_tone = threading.Event()
    tone_cmd, wav_path = generate_sine_wav(frequency=tone_freq)
    tone_thread = threading.Thread(target=tone_loop, args=(tone_cmd, stop_tone), daemon=True)
    tone_thread.start()
    time.sleep(1)  # let audio routing settle

    fieldnames = [
        'timestamp', 'mac',
        'rssi_dbm', 'link_quality', 'tx_power_dbm',
        'packet_loss_pct',
        'hci_err_rx', 'hci_err_tx', 'hci_acl_tx', 'hci_acl_rx',
        'notes'
    ]

    file_exists = os.path.exists(out_path)
    csvfile = open(out_path, 'a', newline='')
    writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
    if not file_exists:
        writer.writeheader()
        csvfile.flush()

    def shutdown(sig=None, frame=None):
        print("\n[INFO] Stopping…")
        stop_tone.set()
        csvfile.close()
        try:
            os.remove(wav_path)
        except OSError:
            pass
        sys.exit(0)

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    start_time = time.time()
    prev_stats = None

    while True:
        elapsed = time.time() - start_time
        if duration > 0 and elapsed >= duration:
            break

        ts = datetime.now().isoformat(timespec='seconds')
        row = {f: '' for f in fieldnames}
        row['timestamp'] = ts
        row['mac'] = mac

        # ── BT metrics ──
        bt = read_rssi(mac)
        row['rssi_dbm']     = bt['rssi']    if bt['rssi']    is not None else 'N/A'
        row['link_quality'] = bt['link_quality'] if bt['link_quality'] is not None else 'N/A'
        row['tx_power_dbm'] = bt['tx_power']    if bt['tx_power']    is not None else 'N/A'

        # ── HCI counters ──
        stats = read_hci_stats()
        if prev_stats:
            def delta(key):
                a = stats.get(key)
                b = prev_stats.get(key)
                if a is not None and b is not None:
                    return a - b
                return 'N/A'
            row['hci_err_rx'] = delta('err_rx')
            row['hci_err_tx'] = delta('err_tx')
            row['hci_acl_tx'] = delta('acl_tx')
            row['hci_acl_rx'] = delta('acl_rx')
        prev_stats = stats

        # ── Packet loss via l2ping ──
        if not skip_ping:
            loss = read_l2ping_loss(mac, count=5)
            row['packet_loss_pct'] = loss if loss is not None else 'N/A'
        else:
            row['packet_loss_pct'] = 'skipped'

        # ── Notes / warnings ──
        notes = []
        if row['link_quality'] == 255:
            notes.append('LQ=255(max or unread)')
        if row['rssi_dbm'] == 'N/A':
            notes.append('RSSI unavailable(see README)')
        row['notes'] = '; '.join(notes)

        writer.writerow(row)
        csvfile.flush()

        # Console summary
        print(
            f"[{ts}]  RSSI={row['rssi_dbm']} dBm  "
            f"LQ={row['link_quality']}  "
            f"TXPwr={row['tx_power_dbm']} dBm  "
            f"Loss={row['packet_loss_pct']}%  "
            f"HCI_err_tx={row['hci_err_tx']}"
        )

        time.sleep(interval)

    shutdown()


# ──────────────────────────────────────────────
# Entry point
# ──────────────────────────────────────────────

def main():
    p = argparse.ArgumentParser(
        description='Bluetooth link quality monitor with continuous tone output.'
    )
    p.add_argument('--mac',       required=True,           help='Classic BT MAC of earbuds, e.g. AA:BB:CC:DD:EE:FF')
    p.add_argument('--interval',  type=float, default=2.0, help='Polling interval in seconds (default: 2)')
    p.add_argument('--duration',  type=float, default=0,   help='Total duration in seconds; 0 = run until Ctrl+C')
    p.add_argument('--out',       default='bt_log.csv',    help='Output CSV file path (default: bt_log.csv)')
    p.add_argument('--tone-freq', type=int,   default=1000,help='Sine tone frequency in Hz (default: 1000)')
    p.add_argument('--skip-ping', action='store_true',     help='Skip l2ping (avoids disrupting audio stream)')
    args = p.parse_args()

    # Sanity checks
    if os.geteuid() != 0:
        print("[WARN] Not running as root. RSSI/l2ping may be unavailable. Try: sudo python3 bt_monitor.py …")

    monitor(
        mac=args.mac,
        interval=args.interval,
        duration=args.duration,
        out_path=args.out,
        tone_freq=args.tone_freq,
        skip_ping=args.skip_ping,
    )


if __name__ == '__main__':
    main()
