"""Refresh offline hourly ERA5-Land temperatures for catalog plant locations."""
import concurrent.futures
import http.client
import json
import time
from pathlib import Path
import urllib.parse
import urllib.request

ROOT = Path(__file__).resolve().parents[1]

def fetch(plant):
    params = dict(latitude=plant['latitude'], longitude=plant['longitude'],
                  start_date='2025-01-01', end_date='2025-12-31',
                  hourly='temperature_2m', models='era5_land', timezone='UTC')
    url = 'https://archive-api.open-meteo.com/v1/archive?' + urllib.parse.urlencode(params)
    for attempt in range(4):
        try:
            with urllib.request.urlopen(url, timeout=60) as response:
                data = json.load(response)
            break
        except (OSError, ValueError, http.client.IncompleteRead):
            if attempt == 3:
                raise
            time.sleep(2 ** attempt)
    values = data['hourly']['temperature_2m']
    if len(values) != 8760 or any(v is None or not -90 < float(v) < 65 for v in values):
        raise ValueError(f"Invalid temperature coverage: {plant['id']}")
    print(plant['id'], len(values), min(values), max(values), flush=True)
    return dict(plant_id=plant['id'], latitude=plant['latitude'], longitude=plant['longitude'],
                year=2025, temperature_series_c=values, request_url=url,
                grid_latitude=data['latitude'], grid_longitude=data['longitude'])

if __name__ == '__main__':
    catalog = json.loads((ROOT/'data/power_tower_catalog.json').read_text())
    archive_path = ROOT/'data/temperature_archive.json'
    existing = {p['plant_id']: p for p in json.loads(archive_path.read_text())['plants']} if archive_path.exists() else {}
    cache = ROOT/'build/temperature-cache'
    cache.mkdir(parents=True, exist_ok=True)
    def load_or_fetch(plant):
        cached_path = cache/(plant['id']+'.json')
        candidates = [existing.get(plant['id'])]
        if cached_path.exists():
            candidates.append(json.loads(cached_path.read_text()))
        for entry in candidates:
            if entry and entry['latitude'] == plant['latitude'] and entry['longitude'] == plant['longitude'] and entry['year'] == 2025 and len(entry['temperature_series_c']) == 8760:
                return entry
        entry = fetch(plant)
        cached_path.write_text(json.dumps(entry, separators=(',', ':'))+'\n')
        return entry
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
        entries = list(pool.map(load_or_fetch, catalog))
    result = dict(source='Open-Meteo / ERA5-Land', source_kind='reanalysis',
                  documentation_url='https://open-meteo.com/en/docs/historical-weather-api',
                  license='CC BY 4.0', start_utc='2025-01-01T00:00:00+00:00',
                  step_seconds=3600, units='degree Celsius', plants=entries)
    temporary_path = archive_path.with_suffix('.json.tmp')
    temporary_path.write_text(json.dumps(result, separators=(',', ':'))+'\n')
    temporary_path.replace(archive_path)
