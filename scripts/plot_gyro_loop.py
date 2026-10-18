#!/usr/bin/env python3
"""Plot recorded live RTL/plant results; requires the optional matplotlib package."""
import argparse
from pathlib import Path

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np

p = argparse.ArgumentParser(description=__doc__)
p.add_argument('run', type=Path)
args = p.parse_args()
data = np.genfromtxt(args.run / 'trajectory.csv', delimiter=',', names=True)
t = data['time_s'] * 1000
envelope = np.convolve(abs(data['drive_mode']), np.ones(16) / 16, 'valid')
fig, axes = plt.subplots(3, 1, figsize=(10, 8), constrained_layout=True)
fig.suptitle('MEMS controller — live RTL feedback experiment', fontsize=17, weight='bold')
axes[0].plot(t[15:], envelope, color='#007c91', label='Mean absolute drive, 16 samples')
axes[0].axhline(.3, color='#bd6520', linestyle='--', label='Target 0.3000')
axes[0].set_ylabel('Drive envelope')
axes[0].legend(loc='upper right')
axes[1].plot(t, data['agc_gain'], color='#475569')
axes[1].set_ylabel('AGC gain')
tail = t >= 395
axes[2].plot(t[tail], data['open_sense_mode'][tail], color='#bd6520', label='Without sense feedback')
axes[2].plot(t[tail], data['sense_mode'][tail], color='#007c91', label='With RTL sense feedback')
axes[2].set_ylabel('Sense displacement')
axes[2].set_xlabel('Time (ms)')
axes[2].legend(loc='upper right')
for ax in axes:
    ax.spines[['top', 'right']].set_visible(False)
    ax.grid(alpha=.15)
    ax.set_xlabel('Time (ms)')
fig.supxlabel('Synthetic resonators and demonstration coefficients; not sensor qualification', fontsize=10)
fig.savefig(args.run / 'feedback.png', dpi=160)
fig.savefig(args.run / 'feedback.svg')
