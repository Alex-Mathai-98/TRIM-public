#!/usr/bin/env python3
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

# Bug: SubFigure.legend() raised because Legend only accepted Figure (not FigureBase).
# Strong check: run the operation WITHOUT try/except so it raises (non-zero exit)
# when buggy, and additionally assert the legend attached to the SubFigure.
subfig = plt.figure().subfigures()
ax = subfig.subplots()
ax.plot([0, 1, 2], [0, 1, 2], label="test")
legend = subfig.legend()

# Agent's own checks: legend is non-axes and parented on the subfigure.
assert legend is not None
assert legend.isaxes is False
assert legend.parent is subfig

plt.close("all")
print("ASSERT-OK")
