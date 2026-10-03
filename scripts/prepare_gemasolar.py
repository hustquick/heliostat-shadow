"""Normalize the pinned inputs and save a compact, reproducible quality receipt."""

import hashlib
import json
from pathlib import Path

import pandas as pd
from scipy.spatial import cKDTree

from datasets import convert_gemasolar_layout, read_pvgis_dni

ROOT = Path(__file__).resolve().parents[1]


def main():
    config = json.loads((ROOT/'data/gemasolar_config.json').read_text())
    layout, layout_quality = convert_gemasolar_layout(ROOT/'data/raw/gemasolar/map_layout.csv', config)
    distances, _ = cKDTree(layout[['x','y']]).query(layout[['x','y']], k=2)
    layout_quality['minimum_centre_separation_m'] = float(distances[:,1].min())
    alternative = pd.read_csv(ROOT/'data/raw/gemasolar/fluxspt_layout.csv')
    layout_quality['alternative_fluxspt_rows'] = len(alternative)
    layout_quality['alternative_issue'] = '2649 rows, one less than the 2650 reported plant count; not padded or used as the baseline'
    data, radiation_quality = read_pvgis_dni(ROOT/f'data/raw/radiation/pvgis_sarah3_{config["year"]}.json', config['year'])
    output = ROOT/'data/processed'
    output.mkdir(exist_ok=True, parents=True)
    layout.to_csv(output/'gemasolar_layout.csv', index=False)
    data.insert(0, 'time_local', data.index.tz_convert(config['timezone']).astype(str))
    data.to_csv(output/f'gemasolar_dni_{config["year"]}.csv')
    receipt = dict(layout=layout_quality, radiation=radiation_quality,
                   input_sha256={str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest()
                                 for p in [ROOT/'data/raw/gemasolar/map_layout.csv',
                                           ROOT/f'data/raw/radiation/pvgis_sarah3_{config["year"]}.json',
                                           ROOT/'data/gemasolar_config.json']})
    (ROOT/'reports/data_quality.json').write_text(json.dumps(receipt, indent=2)+'\n')
    print(json.dumps(receipt, indent=2))


if __name__ == '__main__':
    main()
