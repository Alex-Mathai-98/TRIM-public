#!/usr/bin/env python3
# Manual assert derived from the agent's own reproductions (final_test.py /
# demonstration.py / test_limits_override.py). The agent established that a
# Nominal-scaled objects plot should adopt categorical axis behavior: xlim
# becomes +/- 0.5 from the first/last tick, and the horizontal (Nominal-y)
# plot inverts the y-axis. With the bug these categorical behaviors are absent.
import matplotlib
matplotlib.use("Agg")

import matplotlib.pyplot as plt
import seaborn.objects as so
import pandas as pd

data = pd.DataFrame({
    "category": ["A", "B", "C", "D"],
    "value": [10, 15, 12, 8],
})

# Vertical: Nominal x-scale should give categorical limits (-0.5, n-1+0.5).
fig, ax = plt.subplots()
(
    so.Plot(data, x="category", y="value")
    .add(so.Bar())
    .on(ax)
    .show()
)
xlim = ax.get_xlim()
print(f"Objects Nominal xlim: {xlim}  expected ~(-0.5, 3.5)")
assert abs(xlim[0] - (-0.5)) < 0.01, f"left xlim {xlim[0]} != -0.5"
assert abs(xlim[1] - 3.5) < 0.01, f"right xlim {xlim[1]} != 3.5"
plt.close(fig)

print("ASSERT-OK")
