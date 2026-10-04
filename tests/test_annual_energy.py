from types import SimpleNamespace
from dataclasses import replace
import numpy as np
import pandas as pd
import pytest
from scripts.annual_energy import Sample, weather_samples, evaluate_energy
from scripts.optimize_ps10_greedy import delivered_power, fast_screen_scores
from heliostat import Heliostat
from receiver import CylindricalReceiver


def model(year=2023):
    index = pd.date_range(f'{year}-01-01', f'{year+1}-01-01', freq='h', tz='UTC', inclusive='left')
    return SimpleNamespace(config=dict(latitude=37.5, longitude=-5.3, altitude_m=150, timezone='Europe/Madrid'),
        clear_sky=False, weather=pd.DataFrame({'dni_w_m2':np.linspace(10, 800, len(index))}, index=index))


@pytest.mark.parametrize('year,hours', [(2023,8760),(2024,8784)])
def test_complete_year_and_stratified_dni_conservation(year, hours):
    m = model(year)
    full, provenance = weather_samples(m)
    approximate, compressed = weather_samples(m, stratified=True)
    assert provenance['coverage_hours'] == hours
    assert len(approximate) <= 288 < len(full)
    assert sum(s.duration_hours for s in approximate) == len(full)
    assert sum(s.dni*s.duration_hours for s in approximate) == pytest.approx(sum(s.dni for s in full))
    assert compressed['is_tmy'] is False
    assert all(s.sun[2]>0 and np.linalg.norm(s.sun)==pytest.approx(1) for s in full)


@pytest.mark.parametrize('corrupt', ['gap','duplicate','nan','negative'])
def test_bad_weather_is_not_silently_zero_filled(corrupt):
    m=model()
    if corrupt=='gap': m.weather=m.weather.iloc[1:]
    elif corrupt=='duplicate': m.weather=pd.concat([m.weather,m.weather.iloc[-1:]])
    elif corrupt=='nan': m.weather.iloc[10,0]=np.nan
    else: m.weather.iloc[10,0]=-1
    with pytest.raises(ValueError): weather_samples(m)


def fixture():
    mirror=Heliostat('M1',(0,0,0),2,2,(4,0,20))
    config=dict(reflective_area_m2=3., atmospheric_model='clear_air_40km',mirror_reflectivity=.9,
        mirror_cleanliness=.95,receiver_absorptivity=.95,receiver_thermal_efficiency=.9,
        sunshape_mrad=2.51,slope_error_mrad=2.6,tracking_error_mrad=2.1,receiver_quadrature_order=16,
        towers=[dict(id='tower-1',receiver=dict(centre=[5,0,20],radius=1,height=4))])
    return [mirror],config,CylindricalReceiver((5,0,20),1,4)


def test_energy_duration_units_and_reference(monkeypatch):
    mirrors,c,receiver=fixture()
    one=Sample(pd.Timestamp('2023-03-21T12:00Z'),np.array([0.,0.,1.]),800.)
    value,eta=delivered_power(mirrors,[one],c,receiver)
    energy=evaluate_energy(mirrors,[replace(one,duration_hours=3)],c,receiver)
    assert energy['receiver_incident_kwh']==pytest.approx(value*3/1000)
    assert energy['incident_normal_kwh']==pytest.approx(7.2)
    assert energy['energy_weighted_optical_efficiency']==pytest.approx(eta)
    monkeypatch.setenv('HELIOSTAT_RUST_CORE','0')
    reference=evaluate_energy(mirrors,[replace(one,duration_hours=3)],c,receiver)
    assert reference['receiver_incident_kwh']==pytest.approx(energy['receiver_incident_kwh'],rel=1e-8)


def test_screen_uses_duration_weights():
    mirrors,c,r=fixture()
    s=Sample(pd.Timestamp('2023-03-21T12:00Z'),np.array([0.,0.,1.]),800.)
    points=np.array([[10.,20.],[20.,30.]])
    assert fast_screen_scores(points,mirrors[0],[replace(s,duration_hours=4)],r)==pytest.approx(4*fast_screen_scores(points,mirrors[0],[s],r))


def test_shared_optimizer_accepts_only_best_full_field_gain():
    from rust_core import optimize_energy_step
    from scripts.optimize_ps10_greedy import retarget
    mirrors,c,receiver=fixture()
    s=Sample(pd.Timestamp('2023-03-21T12:00Z'),np.array([0.,0.,1.]),800.)
    candidates=[(0,np.array([10.,20.]),0.),(0,np.array([20.,30.]),0.)]
    initial,_=delivered_power(mirrors,[s],c,receiver)
    powers=[delivered_power([retarget(mirrors[0],xy,receiver)],[s],c,receiver)[0] for _,xy,_ in candidates]
    decision=optimize_energy_step(mirrors,[s],c,candidates,0.)
    assert decision is not None, 'Shared optimizer extension must be rebuilt'
    best=max(powers)
    assert decision['accepted']==(best>initial)
    if decision['accepted']:
        assert decision['final_wh']==pytest.approx(best)
        assert decision['gain_wh']==pytest.approx(best-initial)
    blocked=optimize_energy_step(mirrors,[s],c,candidates,1e9)
    assert blocked['accepted'] is False
    assert blocked['final_wh']==pytest.approx(initial)
    assert blocked['convergence_claim'] is False


def test_night_reference_matches_shared(monkeypatch):
    mirrors,c,receiver=fixture()
    s=Sample(pd.Timestamp('2023-03-21T00:00Z'),np.array([0.,0.,-1.]),800.)
    shared=evaluate_energy(mirrors,[s],c,receiver)
    monkeypatch.setenv('HELIOSTAT_RUST_CORE','0')
    reference=evaluate_energy(mirrors,[s],c,receiver)
    assert shared['receiver_incident_kwh']==reference['receiver_incident_kwh']==0
    assert shared['incident_normal_kwh']==reference['incident_normal_kwh']==0


def test_pvgis_fixed_observation_offset_keeps_actual_solar_time():
    m=model()
    m.weather.index += pd.Timedelta(minutes=10)
    samples,provenance=weather_samples(m)
    assert provenance['coverage_hours']==8760
    assert provenance['timestamp_offset_minutes']==10
    assert all(s.timestamp.minute==10 for s in samples)
    m.weather.index=m.weather.index.where(np.arange(len(m.weather))!=10,m.weather.index+pd.Timedelta(minutes=1))
    with pytest.raises(ValueError): weather_samples(m)
