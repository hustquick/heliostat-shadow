"""Download pinned research layout and calendar-year satellite DNI; keep provenance."""

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]


def main():
    config = json.loads((ROOT/'data/gemasolar_config.json').read_text())
    revision = config['source_revision']
    base = f'https://raw.githubusercontent.com/LiuZengqiang/CSPHeliostatFieldLayout/{revision}/'
    files = {'fluxspt_layout.csv':'Gemasolar_FluxSPT/layout.csv', 'map_layout.csv':'Gemasolar/layout.csv',
             'parameters.xlsx':'Gemasolar/parameters.xlsx', 'source_readme.md':'readme.md',
             'LICENSE':'LICENSE', 'fluxspt_readme.txt':'RelatedTools/FluxSPT/readme.txt'}
    receipts = []
    def download(url, destination, params=None):
        response = requests.get(url, params=params, timeout=60)
        response.raise_for_status()
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(response.content)
        receipts.append(dict(path=str(destination.relative_to(ROOT)), url=response.url,
                             retrieved_at_utc=datetime.now(timezone.utc).isoformat(),
                             sha256=hashlib.sha256(response.content).hexdigest(), bytes=len(response.content)))
        print(destination.relative_to(ROOT), len(response.content), flush=True)
    for name, part in files.items():
        download(base+part, ROOT/'data/raw/gemasolar'/name)
    params = dict(lat=config['latitude'], lon=config['longitude'], startyear=config['year'], endyear=config['year'],
                  raddatabase='PVGIS-SARAH3', trackingtype=2, components=1, usehorizon=1,
                  outputformat='json', pvcalculation=0)
    pvgis_url = 'https://re.jrc.ec.europa.eu/api/v5_3/seriescalc'
    download(pvgis_url,
             ROOT/f'data/raw/radiation/pvgis_sarah3_{config["year"]}.json', params)
    request_url = requests.Request('GET', pvgis_url, params=params).prepare().url
    (ROOT/'data/raw/radiation/request.json').write_text(json.dumps(
        {'url': request_url, 'parameters': params}, indent=2)+'\n')
    (ROOT/'data/source_manifest.json').write_text(json.dumps(receipts, indent=2)+'\n')


if __name__ == '__main__':
    main()
