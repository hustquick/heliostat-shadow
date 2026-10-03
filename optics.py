"""Mirror-to-aim-point atmospheric transmission under a clear-air assumption."""

import numpy as np

ATMOSPHERIC_MODEL = 'clear_air_40km'
ATMOSPHERIC_SOURCE = 'https://doi.org/10.1016/j.solener.2011.12.007'


def atmospheric_transmittance(distance_m, *, model=ATMOSPHERIC_MODEL):
    """Return transmission for positive slant distances in metres.

    Noone, Torrilhon & Mitsos (2012), section 2.6, equation 11:
    polynomial at <= 1000 m, exponential beyond 1000 m. The empirical
    approximation assumes about 40 km visibility; it does NOT reconstruct
    historical aerosol, humidity or visibility from DNI. The polynomial's
    intercept is 0.99321 (not 1); do not extrapolate it to zero-length paths
    or silently renormalize it. There is a small fitted branch discontinuity.
    """
    if model != ATMOSPHERIC_MODEL:
        raise ValueError(f'Unsupported atmospheric model: {model}')
    distance = np.asarray(distance_m, dtype=float)
    if np.any(~np.isfinite(distance)) or np.any(distance <= 0):
        raise ValueError('Slant distances must be finite and positive, in metres')
    # Cap only the unused polynomial branch to avoid overflow at long ranges.
    near = np.minimum(distance, 1000.)
    result = np.where(distance <= 1000.,
                      0.99321 - 0.0001176 * near + 1.97e-8 * near**2,
                      np.exp(-0.0001106 * distance))
    return float(result) if result.ndim == 0 else result
