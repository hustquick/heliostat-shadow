"""Historical calendar coverage and DNI plane semantics must be explicit."""

import json
import pandas as pd
import pytest

from datasets import read_pvgis_dni, convert_gemasolar_layout


@pytest.fixture
def raw():
    dates=pd.date_range('2023-01-01 00:10',periods=8760,freq='h')
    rows=[dict(time=t.strftime('%Y%m%d:%H%M'),**{'Gb(i)':100,'Gd(i)':20,'Gr(i)':1,'H_sun':30,'T2m':15,'WS10m':2,'Int':0}) for t in dates]
    return dict(inputs=dict(mounting_system={'two_axis':{}},meteo_data=dict(radiation_db='PVGIS-SARAH3',year_min=2023,year_max=2023),location={}),
                outputs={'hourly':rows},meta={'outputs':{'hourly':{'timestamp':'hourly averages','variables':{'Gb(i)':{'units':'W/m2'}}}}})


def test_normal_plane_extraction_and_full_calendar(raw,tmp_path):
    path=tmp_path/'raw.json';path.write_text(json.dumps(raw))
    data,quality=read_pvgis_dni(path,2023)
    assert len(data)==8760
    assert str(data.index.tz)=='UTC'
    assert data.index[0].minute==10
    assert quality['annual_dni_kwh_m2_hourly_sample_estimate']==876
    assert 'tracking_diffuse_w_m2' in data and 'dhi' not in data


@pytest.mark.parametrize('error',['tracking','missing','duplicate','negative','unit','nan'])
def test_invalid_radiation(raw,tmp_path,error):
    if error=='tracking':raw['inputs']['mounting_system']={'fixed':{}}
    if error=='missing':raw['outputs']['hourly'].pop()
    if error=='duplicate':raw['outputs']['hourly'][1]['time']=raw['outputs']['hourly'][0]['time']
    if error=='negative':raw['outputs']['hourly'][0]['Gb(i)']=-1
    if error=='nan':raw['outputs']['hourly'][0]['Gb(i)']=None
    if error=='unit':raw['meta']['outputs']['hourly']['variables']['Gb(i)']['units']='kWh/m2'
    path=tmp_path/'raw.json';path.write_text(json.dumps(raw))
    with pytest.raises(ValueError):read_pvgis_dni(path,2023)


def test_layout_uses_near_mantle_and_explicit_height(tmp_path):
    path=tmp_path/'layout.csv';path.write_text('id,x,y\n1,30,40\n')
    config=dict(expected_mirrors=1,receiver_radius_m=4,mirror_width_m=12.305,mirror_height_m=9.752,optical_height_m=140)
    layout,_=convert_gemasolar_layout(path,config)
    assert layout.iloc[0].aim_x==pytest.approx(2.4)
    assert layout.iloc[0].aim_y==pytest.approx(3.2)
    assert layout.iloc[0].aim_z==140
    assert layout.iloc[0].z==0
    config['expected_mirrors']=2
    with pytest.raises(ValueError):convert_gemasolar_layout(path,config)
