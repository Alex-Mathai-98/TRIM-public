#!/usr/bin/env python3
import matplotlib
matplotlib.use("Agg")
import numpy as np
import matplotlib.pyplot as plt

# Reproduce: ax.clear() on a shared 2x2 subplot grid should restore
# the "outer label" visibility (same as without clear()).
fig, axes = plt.subplots(2, 2, sharex=True, sharey=True)

x = np.arange(0.0, 2 * np.pi, 0.01)
y = np.sin(x)

for ax in axes.flatten():
    ax.clear()
    ax.plot(x, y)

# (x_visible > 0, y_visible > 0) per axis after clear()
actual = []
for ax in axes.flatten():
    x_visible = len([l for l in ax.get_xticklabels() if l.get_visible()])
    y_visible = len([l for l in ax.get_yticklabels() if l.get_visible()])
    actual.append((x_visible > 0, y_visible > 0))

# Expected behavior the agent computed:
# Axis 0 (top-left):    x hidden, y visible
# Axis 1 (top-right):   x hidden, y hidden
# Axis 2 (bottom-left): x visible, y visible
# Axis 3 (bottom-right):x visible, y hidden
expected = [(False, True), (False, False), (True, True), (True, False)]

assert actual == expected, f"actual={actual} expected={expected}"

plt.close()
print("ASSERT-OK")
