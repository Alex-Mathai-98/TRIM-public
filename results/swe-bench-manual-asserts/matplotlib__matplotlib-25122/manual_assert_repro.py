#!/usr/bin/env python3
import matplotlib
matplotlib.use("Agg")
import numpy as np
import matplotlib.mlab as mlab

# Bug: _spectral_helper normalized 'magnitude'/'complex'/psd(scale_by_freq=False)
# by np.abs(window).sum() instead of window.sum(). For a window with negative
# values (flattop) these differ, so the magnitude spectrum is mis-scaled.
# Build the flattop window from its cosine-sum coefficients (numpy only; the
# container image has no scipy). These are the standard flattop coefficients
# scipy.signal.windows.flattop uses; the window has negative lobes.
N = 512
a = [0.21557895, 0.41663158, 0.277263158, 0.083578947, 0.006947368]
n = np.arange(N)
window = np.zeros(N)
for k, ak in enumerate(a):
    window += ((-1) ** k) * ak * np.cos(2 * np.pi * k * n / (N - 1))
assert np.any(window < 0)  # flattop has negative lobes (agent's own observation)

# DC input: the windowed FFT at bin 0 equals window.sum() exactly.
x = np.ones(512)

# (a) magnitude mode (line ~398): |FFT|/divisor. Correct divisor == window.sum()
#     gives bin-0 == 1.0; bug divisor == np.abs(window).sum() gives < 1.0.
res_mag, _, _ = mlab._spectral_helper(
    x, NFFT=512, Fs=1.0, window=window, noverlap=0, mode='magnitude',
    sides='onesided')
dc_mag = res_mag[0, 0]
assert np.isclose(dc_mag, 1.0), (
    f"magnitude DC bin = {dc_mag}, expected 1.0 "
    f"(window.sum()={window.sum()}, abs.sum()={np.abs(window).sum()})")

# (b) psd mode, scale_by_freq=False (line ~430): |FFT|^2 / divisor**2.
#     un-normalized |FFT|^2 at DC == window.sum()**2, so correct divisor
#     window.sum() gives bin-0 == 1.0; bug np.abs(window).sum() gives < 1.0.
res_psd, _, _ = mlab._spectral_helper(
    x, NFFT=512, Fs=1.0, window=window, noverlap=0, mode='psd',
    scale_by_freq=False, sides='onesided')
dc_psd = res_psd[0, 0]
assert np.isclose(dc_psd, 1.0), (
    f"psd(scale_by_freq=False) DC bin = {dc_psd}, expected 1.0 "
    f"(window.sum()**2={window.sum()**2}, abs.sum()**2={np.abs(window).sum()**2})")

print("ASSERT-OK")
