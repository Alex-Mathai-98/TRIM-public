#!/usr/bin/env python3
# Manual assert derived from the agent's own verify_fix.py. The agent set up a
# Continuous scale on large-valued data and checked that the legend labels,
# parsed as floats, equal the original data values (np.allclose). With the bug,
# matplotlib's ScalarFormatter offset makes the labels small (e.g. "2.7" with a
# separate "1e6" offset) so they do NOT match the true large values.
import matplotlib
matplotlib.use("Agg")

import matplotlib.pyplot as plt
import seaborn as sns
import seaborn.objects as so

# Reproduce the agent's penguins scenario (test_before_after.py): a pointsize
# legend over very large values (body_mass in mg). The agent extracted the
# size-legend text and checked the values are full large magnitudes
# (2.7e6 .. 6.3e6 range, every value > 1e6). With the bug, matplotlib's
# ScalarFormatter offset strips the magnitude and the legend shows small numbers.
penguins = sns.load_dataset("penguins")
penguins["body_mass_mg"] = penguins["body_mass_g"] * 1000

plot = (
    so.Plot(
        penguins, x="bill_length_mm", y="bill_depth_mm",
        color="species", pointsize="body_mass_mg",
    )
    .add(so.Dot())
)
plot.show()
fig = plt.gcf()

def _legend_large_vals(figure, axis=None):
    """Collect numeric legend-entry magnitudes > 1000 (the size legend)."""
    legends = list(figure.legends)
    if axis is not None and axis.get_legend() is not None:
        legends.append(axis.get_legend())
    vals = []
    for legend in legends:
        for text_obj in legend.findobj(plt.Text):
            try:
                value = float(text_obj.get_text().replace("−", "-"))
            except ValueError:
                continue
            if abs(value) > 1000:  # numeric size entries, not small coords
                vals.append(value)
    return vals

# (1) Objects interface (seaborn.objects -> seaborn/_core/scales.py path).
objects_vals = _legend_large_vals(fig)
print("Objects size legend values:", objects_vals)
assert objects_vals, "no numeric size-legend values found (objects interface)"
assert all(abs(v) > 1e6 for v in objects_vals), (
    f"objects size legend not full-magnitude (scales.py offset bug): {objects_vals}"
)

# (2) Classic interface (seaborn.scatterplot -> seaborn/utils.py
# locator_to_legend_entries path). The agent's penguins/body_mass scenario also
# covers the legacy legend; with the bug the ScalarFormatter offset strips the
# magnitude here too, so the legend shows small numbers instead of the millions.
plt.close("all")
ax = sns.scatterplot(
    data=penguins, x="bill_length_mm", y="bill_depth_mm", size="body_mass_mg",
)
classic_vals = _legend_large_vals(ax.figure, ax)
print("Classic size legend values:", classic_vals)
assert classic_vals, "no numeric size-legend values found (classic interface)"
assert all(abs(v) > 1e6 for v in classic_vals), (
    f"classic size legend not full-magnitude (utils.py offset bug): {classic_vals}"
)

print("ASSERT-OK")
