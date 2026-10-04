#!/usr/bin/env python3
"""Run the catalogue-wide greedy field optimizer.

Kept as the general entry point. ``optimize_ps10_greedy.py`` remains a
compatible filename for the original PS10 experiment and now uses this same
implementation.
"""

try:
    from scripts.optimize_ps10_greedy import main
except ModuleNotFoundError:
    from optimize_ps10_greedy import main


if __name__ == '__main__':
    main()
