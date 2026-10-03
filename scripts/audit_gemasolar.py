"""Real-layout regression checks and declared geometry sensitivity scenarios."""

from dataclasses import replace
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd

from blocking import blocking_efficiency
from field import load_layout
from shadow import shadow_efficiency
from simulation import PreparedField
from solar import sun_vector

ROOT=Path(__file__).resolve().parents[1]


def main():
    config=json.loads((ROOT/'data/gemasolar_config.json').read_text())
    for item in json.loads((ROOT/'data/source_manifest.json').read_text()):
        actual=hashlib.sha256((ROOT/item['path']).read_bytes()).hexdigest()
        if actual!=item['sha256']:raise ValueError(f"Source hash mismatch: {item['path']}")
    mirrors=load_layout(ROOT/'data/processed/gemasolar_layout.csv')
    stamp='2023-03-21T08:10:00Z'
    sun=sun_vector(config['latitude'],config['longitude'],stamp,altitude=config['altitude_m'])
    prepared=PreparedField.from_mirrors(mirrors,sun.sun_to_sky)
    checks=[]
    for i in [0,200,600,1000,1500,2000,2649]:
        fast=prepared.target_efficiencies(i)
        full=prepared.target_efficiencies(i,conservative_filter=False)
        scalar_s=shadow_efficiency(mirrors[i],mirrors,sun.sun_to_sky)
        scalar_b=blocking_efficiency(mirrors[i],mirrors,sun.sun_to_sky)
        err=max(abs(fast['eta_shadow']-scalar_s),abs(fast['eta_blocking']-scalar_b),
                *(abs(fast[key]-full[key]) for key in ['eta_shadow','eta_blocking','eta_joint']))
        if err>1e-8:raise AssertionError(f'Mirror {i}: {err}')
        checks.append(dict(mirror_id=mirrors[i].mirror_id,max_absolute_error=err))
    report=dict(timestamp_utc=stamp,source_hashes_match=True,checks=checks,
                scalar_and_unfiltered_max_absolute_error=max(c['max_absolute_error'] for c in checks),
                note='Prepared path versus original scalar API and unfiltered candidates for seven full-field targets; independent sampled rays are also covered by unit tests.')
    (ROOT/'reports/geometry_audit.json').write_text(json.dumps(report,indent=2)+'\n')
    # Isolate geometry assumptions using identical DNI=800, NOT historical output.
    cases={'baseline_140m_12.305x9.752':mirrors,
           'optical_height_116m':[replace(m,aim_point=(m.aim_point[0],m.aim_point[1],116.)) for m in mirrors],
           'envelope_11.5x10.4':[replace(m,width=11.5,height=10.4) for m in mirrors]}
    rows=[]
    for date in ['2023-06-21T12:10:00Z','2023-12-21T12:10:00Z']:
        sun=sun_vector(config['latitude'],config['longitude'],date,altitude=config['altitude_m'])
        for label,layout in cases.items():
            result=PreparedField.from_mirrors(layout,sun.sun_to_sky).evaluate(800,reflective_area_m2=config['reflective_area_m2'])
            incident=result.incident_cosine_power_w.sum();usable=result.geometric_usable_power_w.sum()
            rows.append(dict(time_utc=date,scenario=label,dni_w_m2=800,geometric_usable_mw=usable/1e6,
                             eta_joint_weighted=usable/incident,
                             interpretation='geometry sensitivity at FIXED artificial DNI, not historical power'))
    pd.DataFrame(rows).to_csv(ROOT/'reports/geometry_sensitivity.csv',index=False)
    print(json.dumps(report,indent=2));print(pd.DataFrame(rows).to_string(index=False))


if __name__=='__main__':main()
