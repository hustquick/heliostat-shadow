"""Compute a complete field at all hours of explicitly selected historical dates."""

import argparse
from datetime import datetime, timezone
import gzip
import hashlib
from importlib.metadata import version
import json
from pathlib import Path
import time

import numpy as np
import pandas as pd

from datasets import read_pvgis_dni
from field import load_layout
from optics import ATMOSPHERIC_MODEL
from receiver import CylindricalReceiver
from simulation import PreparedField
from solar import sun_vector

ROOT=Path(__file__).resolve().parents[1]


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dates',nargs='+',help='Local calendar dates, YYYY-MM-DD')
    parser.add_argument('--output',type=Path)
    args=parser.parse_args()
    config=json.loads((ROOT/'data/gemasolar_config.json').read_text())
    receiver=CylindricalReceiver((0.,0.,config['optical_height_m']),config['receiver_radius_m'],config['receiver_height_m'])
    dates=args.dates or config['selected_local_dates']
    output=args.output or ROOT/f'reports/gemasolar_{config["year"]}'
    output.mkdir(parents=True,exist_ok=True)
    mirrors=load_layout(ROOT/'data/processed/gemasolar_layout.csv')
    radiation,quality=read_pvgis_dni(ROOT/f'data/raw/radiation/pvgis_sarah3_{config["year"]}.json',config['year'])
    local_dates=radiation.index.tz_convert(config['timezone']).strftime('%Y-%m-%d')
    selected=radiation.loc[local_dates.isin(dates)]
    present=set(selected.index.tz_convert(config['timezone']).strftime('%Y-%m-%d'))
    if present!=set(dates):raise ValueError('Selected dates are missing from the historical file')
    for day in dates:
        if (selected.index.tz_convert(config['timezone']).strftime('%Y-%m-%d')==day).sum()!=24:
            raise ValueError('This runner expects complete 24-hour local days; handle DST transition dates separately')
    rows=[];header=True;start=time.monotonic();best_power=-1;best=None
    with gzip.open(output/'per_mirror.csv.gz','wt',encoding='utf-8',newline='') as detail:
        for step,(timestamp,weather) in enumerate(selected.iterrows(),1):
            local=timestamp.tz_convert(config['timezone'])
            sun=sun_vector(config['latitude'],config['longitude'],timestamp,
                           altitude=config['altitude_m'],temperature=weather.temperature_c)
            row=dict(time_utc=timestamp.isoformat(),time_local=local.isoformat(),date_local=str(local.date()),
                     dni_w_m2=float(weather.dni_w_m2),elevation_deg=float(sun.solar_pos.elevation.iloc[0]),
                     status='night',mirror_count=len(mirrors),incident_normal_mw=0.,
                     incident_cosine_mw=0.,geometric_usable_mw=0.,post_atmosphere_mw=0.,
                     cosine_loss_mw=0.,joint_loss_mw=0.,atmospheric_loss_mw=0.,
                     reflected_mw=0.,reflection_loss_mw=0.,optical_upper_bound_mw=0.,
                     atmospheric_after_reflection_loss_mw=0.,clean_mw=0.,cleanliness_loss_mw=0.,
                     receiver_incident_mw=0.,interception_loss_mw=0.,receiver_absorbed_mw=0.,
                     receiver_absorption_loss_mw=0.,receiver_net_thermal_mw=0.,receiver_thermal_loss_mw=0.)
            if sun.is_daylight:
                prepared=PreparedField.from_mirrors(mirrors,sun.sun_to_sky)
                result=prepared.evaluate(weather.dni_w_m2,reflective_area_m2=config['reflective_area_m2'],
                                         atmospheric_model=config.get('atmospheric_model',ATMOSPHERIC_MODEL),
                                         mirror_reflectivity=config['mirror_reflectivity'],
                                         mirror_cleanliness=config['mirror_cleanliness'],
                                         receiver=receiver,
                                         receiver_absorptivity=config['receiver_absorptivity'],
                                         receiver_thermal_efficiency=config['receiver_thermal_efficiency'],
                                         sunshape_mrad=config['sunshape_mrad'],
                                         slope_error_mrad=config['slope_error_mrad'],
                                         tracking_error_mrad=config['tracking_error_mrad'],
                                         receiver_quadrature_order=config['receiver_quadrature_order'])
                row.update(status='calculated')
                for name in ('incident_normal','incident_cosine','geometric_usable','post_atmosphere',
                             'cosine_loss','joint_loss','atmospheric_loss','reflected','reflection_loss',
                             'optical_upper_bound','atmosphere_after_reflection','atmospheric_after_reflection_loss',
                             'clean','cleanliness_loss',
                             'receiver_incident','interception_loss','receiver_absorbed','receiver_absorption_loss',
                             'receiver_net_thermal','receiver_thermal_loss'):
                    row[f'{name}_mw']=float(result[f'{name}_power_w'].sum()/1e6)
                # Cosine-area weights, defined even for a cloudy DNI=0 interval.
                weights=result.eta_cosine.to_numpy()*config['reflective_area_m2']
                path_weights=weights*result.eta_joint.to_numpy()
                row['eta_cosine_weighted']=float(result.eta_cosine.mean())  # equal reflective areas
                row['eta_optical_upper_bound']=float(np.average(result.eta_optical_upper_bound,weights=weights))
                row['eta_intercept_weighted']=float(np.average(result.eta_intercept,weights=path_weights)) if path_weights.sum()>0 else None
                row['eta_absorbed_weighted']=float(np.average(result.eta_absorbed,weights=weights))
                row['eta_atmosphere_weighted']=float(np.average(result.eta_atmosphere,weights=path_weights)) if path_weights.sum()>0 else None
                for kind in ('shadow','blocking','joint'):
                    row[f'eta_{kind}_weighted']=float(np.average(result[f'eta_{kind}'],weights=weights))
                    row[f'mirrors_with_{kind}_loss']=int((result[f'eta_{kind}']<1-1e-9).sum())
                result.insert(0,'time_utc',timestamp.isoformat())
                result.to_csv(detail,index=False,header=header);header=False
                if row['geometric_usable_mw']>best_power:
                    best_power=row['geometric_usable_mw'];best=result.copy()
                print(f'{step}/{len(selected)} {local} DNI={weather.dni_w_m2:.2f} usable={row["geometric_usable_mw"]:.3f} MW eta_joint={row["eta_joint_weighted"]:.5f}',flush=True)
            rows.append(row)
    hourly=pd.DataFrame(rows)
    hourly.to_csv(output/'hourly_summary.csv',index=False)
    if best is not None:best.to_csv(output/'peak_snapshot.csv',index=False)
    daily=[]
    for date,group in hourly.groupby('date_local'):
        normal=group.incident_normal_mw.sum();initial=group.incident_cosine_mw.sum()
        usable=group.geometric_usable_mw.sum();transmitted=group.post_atmosphere_mw.sum()
        optical=group.optical_upper_bound_mw.sum();absorbed=group.receiver_absorbed_mw.sum()
        net_thermal=group.receiver_net_thermal_mw.sum()
        daily.append(dict(date_local=date,hourly_samples=len(group),daylight_samples=int((group.status=='calculated').sum()),
                          dni_kwh_m2_estimate=float(group.dni_w_m2.sum()/1000),
                          incident_normal_mwh_estimate=float(normal),
                          incident_cosine_mwh_estimate=float(initial),geometric_usable_mwh_estimate=float(usable),
                          post_atmosphere_mwh_estimate=float(transmitted),
                          cosine_loss_mwh_estimate=float(group.cosine_loss_mw.sum()),
                          joint_loss_mwh_estimate=float(group.joint_loss_mw.sum()),
                          atmospheric_loss_mwh_estimate=float(group.atmospheric_loss_mw.sum()),
                          loss_cosine_percent=float((1-initial/normal)*100) if normal else None,
                          loss_joint_percent=float((1-usable/initial)*100) if initial else None,
                          loss_atmosphere_percent=float((1-transmitted/usable)*100) if usable else None,
                          loss_total_percent=float((1-transmitted/normal)*100) if normal else None,
                          reflected_mwh_estimate=float(group.reflected_mw.sum()),
                          reflection_loss_mwh_estimate=float(group.reflection_loss_mw.sum()),
                          atmospheric_after_reflection_loss_mwh_estimate=float(group.atmospheric_after_reflection_loss_mw.sum()),
                          optical_upper_bound_mwh_estimate=float(optical),
                          clean_mwh_estimate=float(group.clean_mw.sum()),
                          cleanliness_loss_mwh_estimate=float(group.cleanliness_loss_mw.sum()),
                          atmosphere_after_reflection_mwh_estimate=float(group.atmosphere_after_reflection_mw.sum()),
                          receiver_incident_mwh_estimate=float(group.receiver_incident_mw.sum()),
                          interception_loss_mwh_estimate=float(group.interception_loss_mw.sum()),
                          receiver_absorbed_mwh_estimate=float(absorbed),
                          receiver_absorption_loss_mwh_estimate=float(group.receiver_absorption_loss_mw.sum()),
                          receiver_net_thermal_mwh_estimate=float(net_thermal),
                          receiver_thermal_loss_mwh_estimate=float(group.receiver_thermal_loss_mw.sum()),
                          eta_optical_upper_bound_percent=float(100*optical/normal) if normal else None,
                          eta_absorbed_percent=float(100*absorbed/normal) if normal else None,
                          eta_net_thermal_percent=float(100*net_thermal/normal) if normal else None,
                          loss_reflection_percent=float(100*(1-config['mirror_reflectivity'])),
                          loss_cleanliness_percent=float(100*(1-config['mirror_cleanliness'])),
                          loss_interception_percent=float(100*(1-group.receiver_incident_mw.sum()/group.atmosphere_after_reflection_mw.sum())) if group.atmosphere_after_reflection_mw.sum() else None,
                          loss_receiver_absorption_percent=float(100*(1-absorbed/group.receiver_incident_mw.sum())) if group.receiver_incident_mw.sum() else None,
                          loss_receiver_thermal_percent=float(100*(1-net_thermal/absorbed)) if absorbed else None,
                          loss_four_factor_total_percent=float(100*(1-optical/normal)) if normal else None,
                          loss_full_chain_total_percent=float(100*(1-absorbed/normal)) if normal else None,
                          peak_geometric_mw=float(group.geometric_usable_mw.max()),
                          peak_post_atmosphere_mw=float(group.post_atmosphere_mw.max()),
                          peak_optical_upper_bound_mw=float(group.optical_upper_bound_mw.max())))
    pd.DataFrame(daily).to_csv(output/'daily_summary.csv',index=False)
    provenance=dict(completed_at_utc=datetime.now(timezone.utc).isoformat(),runtime_seconds=time.monotonic()-start,
                    dates_local=dates,total_time_samples=len(selected),full_field_daylight_samples=int((hourly.status=='calculated').sum()),
                    mirror_count=len(mirrors),configuration=config,
                    annual_dni_kwh_m2_hourly_sample_estimate=quality['annual_dni_kwh_m2_hourly_sample_estimate'],
                    energy_scope='Only these selected days; no annual field energy estimate',
                    power_scope='Full chain: normal-area reference -> cosine -> joint shadow/blocking -> reflectivity -> cleanliness -> atmosphere -> finite cylindrical receiver interception -> receiver absorptivity -> receiver thermal-efficiency scenario. Optical efficiency ends at receiver incident power; absorbed and net-thermal efficiencies include their stated factors. Neither is electrical output.',
                    complete_optical_efficiency_validated=True,
                    loss_accounting='Sequential stage powers are retained for every mirror and close to the normal-area input. Loss percentages use their immediate stage input; do not add percentages.',
                    code_sha256={name:hashlib.sha256((ROOT/name).read_bytes()).hexdigest() for name in
                                 ['simulation.py','receiver.py','optics.py','heliostat.py','shadow.py','solar.py','datasets.py','scripts/run_gemasolar.py']},
                    package_versions={name:version(name) for name in ['numpy','pandas','pvlib','shapely','scipy']})
    (output/'run_manifest.json').write_text(json.dumps(provenance,indent=2)+'\n')
    print(pd.DataFrame(daily).to_string(index=False),flush=True)


if __name__=='__main__':main()
