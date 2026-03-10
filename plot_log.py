#!/usr/bin/env python3
"""
plot_log.py — Simple plot of bt_log.csv metrics over time.
Requires: pip3 install matplotlib pandas
"""
import sys
import pandas as pd
import matplotlib.pyplot as plt
import matplotlib.dates as mdates

def main():
    path = sys.argv[1] if len(sys.argv) > 1 else 'bt_log.csv'
    df = pd.read_csv(path, parse_dates=['timestamp'])

    # Convert N/A and 'skipped' to NaN
    for col in ['rssi_dbm', 'link_quality', 'tx_power_dbm', 'packet_loss_pct', 'hci_err_tx', 'hci_err_rx']:
        df[col] = pd.to_numeric(df[col], errors='coerce')

    fig, axes = plt.subplots(4, 1, figsize=(12, 10), sharex=True)
    fig.suptitle(f'Bluetooth Link Quality  —  {path}', fontsize=13)

    def plot(ax, col, label, color, ylim=None):
        ax.plot(df['timestamp'], df[col], color=color, linewidth=1, marker='.', markersize=3)
        ax.set_ylabel(label, fontsize=9)
        ax.grid(True, alpha=0.3)
        if ylim:
            ax.set_ylim(ylim)

    plot(axes[0], 'rssi_dbm',        'RSSI (dBm)',        'steelblue')
    plot(axes[1], 'link_quality',     'Link Quality (0–255)', 'green', ylim=[0, 260])
    plot(axes[2], 'packet_loss_pct',  'Packet Loss (%)',   'red',   ylim=[0, 105])
    plot(axes[3], 'hci_err_tx',       'HCI TX Errors (Δ)','orange')

    axes[-1].xaxis.set_major_formatter(mdates.DateFormatter('%H:%M:%S'))
    fig.autofmt_xdate()
    plt.tight_layout()
    out = path.replace('.csv', '_plot.png')
    plt.savefig(out, dpi=150)
    print(f'Plot saved → {out}')
    plt.show()

if __name__ == '__main__':
    main()
