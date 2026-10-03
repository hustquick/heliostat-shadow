"""Prepared field equivalence, mount convention and joint-mask ray validation."""

import numpy as np
import pytest

from blocking import blocking_efficiency
from heliostat import Heliostat, mirror_normal, mirror_vertices, reflection_direction
from shadow import shadow_efficiency
from simulation import PreparedField


def test_joint_overlap_is_not_product():
    mirrors = [Heliostat('target',(0,0,0),2,2,(0,0,20)),
               Heliostat('other',(1,0,3),2,2,(1,0,20))]
    result = PreparedField.from_mirrors(mirrors,[0,0,1]).target_efficiencies(0)
    assert result['eta_shadow'] == pytest.approx(0.5)
    assert result['eta_blocking'] == pytest.approx(0.5)
    assert result['eta_joint'] == pytest.approx(0.5)
    field = PreparedField.from_mirrors(mirrors,[0,0,1]).evaluate(800)
    assert field.iloc[0].geometric_usable_power_w == pytest.approx(1600)


def test_projection_diagnostics_reuse_joint_geometry():
    mirrors = [Heliostat('target',(0,0,0),2,2,(0,0,20)),
               Heliostat('other',(1,0,3),2,2,(1,0,20))]
    field = PreparedField.from_mirrors(mirrors, [0,0,1])
    diagnostic = field.target_projection_diagnostics(0)
    assert diagnostic['target_polygon'].area > 0
    assert diagnostic['shadow']['candidate_ids'] == ['other']
    assert diagnostic['blocking']['candidate_ids'] == ['other']
    assert diagnostic['shadow']['occluder_ids'] == ['other']
    assert diagnostic['blocking']['occluder_ids'] == ['other']
    result = field.target_efficiencies(0)
    assert diagnostic['target_polygon'].difference(
        diagnostic['shadow']['union_polygon'].union(diagnostic['blocking']['union_polygon'])
    ).area / diagnostic['target_polygon'].area == pytest.approx(result['eta_joint'])


def test_blocking_candidate_ids_are_strictly_between_target_and_receiver():
    mirrors = [
        Heliostat('target', (0, 0, 0), 2, 2, (0, 0, 10)),
        Heliostat('behind', (0, 0, -3), 2, 2, (10, 0, 20)),
        Heliostat('between', (0, 0, 4), 2, 2, (10, 0, 20)),
        Heliostat('beyond', (0, 0, 12), 2, 2, (10, 0, 20)),
    ]
    diagnostic = PreparedField.from_mirrors(mirrors, [0, 0, 1]).target_projection_diagnostics(0)
    assert diagnostic['blocking']['candidate_ids'] == ['between']
    assert diagnostic['blocking']['occluder_ids'] == ['between']


def test_depth_filter_keeps_a_tilted_mirror_straddling_target_plane():
    target = Heliostat('target', (0, 0, 0), 2, 2, (0, 0, 10))
    reflected = np.array([np.sqrt(3) / 2, 0, -0.5])
    centre = np.array([0., 0., 0.])
    straddling = Heliostat('straddling', tuple(centre), 2, 2,
                           tuple(centre + 20 * reflected))
    diagnostic = PreparedField.from_mirrors(
        [target, straddling], [0, 0, 1]).target_projection_diagnostics(0)
    assert diagnostic['blocking']['candidate_ids'] == ['straddling']
    assert diagnostic['blocking']['occluder_ids'] == ['straddling']


def test_azimuth_elevation_width_axis_remains_horizontal():
    obj=Heliostat('test',(0,0,0),4,3,(10,3,20),mount_type='azimuth_elevation')
    normal=mirror_normal([0.2,0.3,1],reflection_direction(obj))
    vertices=mirror_vertices(obj,normal)
    width=vertices[1]-vertices[0]
    assert width[2] == pytest.approx(0,abs=1e-12)
    assert np.dot(width,normal) == pytest.approx(0,abs=1e-12)


def test_prepared_reference_and_no_filter_agree():
    rng=np.random.default_rng(52)
    mirrors=[Heliostat(str(i),tuple(rng.uniform([-5,-5,0],[5,5,5])),4,3,(0,0,30),
                       mount_type='azimuth_elevation') for i in range(12)]
    sun=np.array([0.5,-0.2,0.8]);sun/=np.linalg.norm(sun)
    field=PreparedField.from_mirrors(mirrors,sun)
    for i,mirror in enumerate(mirrors):
        result=field.target_efficiencies(i)
        all_candidates=field.target_efficiencies(i,conservative_filter=False)
        assert result['eta_shadow'] == pytest.approx(shadow_efficiency(mirror,mirrors,sun),abs=1e-8)
        assert result['eta_blocking'] == pytest.approx(blocking_efficiency(mirror,mirrors,sun),abs=1e-8)
        for key in ('eta_shadow','eta_blocking','eta_joint'):
            assert result[key] == pytest.approx(all_candidates[key],abs=1e-8)
        assert max(0,result['eta_shadow']+result['eta_blocking']-1)-1e-10 <= result['eta_joint']
        assert result['eta_joint'] <= min(result['eta_shadow'],result['eta_blocking'])+1e-10
    # Independent direct ray/rectangle tests for the joint mask on mirror 0.
    corners=field.vertices[0]
    q=(np.arange(180)+0.5)/180
    a,b=np.meshgrid(q,q)
    starts=corners[0]+a.ravel()[:,None]*(corners[1]-corners[0])+b.ravel()[:,None]*(corners[3]-corners[0])
    covered=np.zeros(len(starts),dtype=bool)
    for blocking,direction in [(False,sun),(True,field.reflected[0])]:
        for vertices in field.vertices[1:]:
            u,v=vertices[1]-vertices[0],vertices[3]-vertices[0]
            normal=np.cross(u,v);denominator=direction@normal
            if abs(denominator)<1e-12:continue
            t=(vertices[0]-starts)@normal/denominator
            hit=starts+t[:,None]*direction
            cu=(hit-vertices[0])@u/(u@u);cv=(hit-vertices[0])@v/(v@v)
            valid=(t>1e-9)&(cu>=0)&(cu<=1)&(cv>=0)&(cv<=1)
            if blocking:valid&=(hit-field.centres[0])@direction<field.receiver_distances[0]-1e-9
            covered|=valid
    assert field.target_efficiencies(0)['eta_joint'] == pytest.approx(1-covered.mean(),abs=0.004)


def test_flat_field_spatial_index_matches_unfiltered_geometry():
    rng = np.random.default_rng(91)
    mirrors = [Heliostat(str(i), tuple([*rng.uniform(-80, 80, 2), 0.]), 5, 4,
                          (0, 0, 90), mount_type='azimuth_elevation')
               for i in range(120)]
    sun = np.array([.35, -.25, .9]); sun /= np.linalg.norm(sun)
    field = PreparedField.from_mirrors(mirrors, sun)
    assert field.flat_xy_tree is not None
    for i in (0, 17, 63, 119):
        indexed = field.target_efficiencies(i)
        unfiltered = field.target_efficiencies(i, conservative_filter=False)
        for key in ('eta_shadow', 'eta_blocking', 'eta_joint'):
            assert indexed[key] == pytest.approx(unfiltered[key], abs=1e-9)


@pytest.mark.parametrize('dni',[-1,np.nan,np.inf])
def test_invalid_dni(dni):
    with pytest.raises(ValueError):
        PreparedField.from_mirrors([Heliostat('a',(0,0,0),2,2,(0,0,20))],[0,0,1]).evaluate(dni)


@pytest.mark.parametrize("timestamp,index", [
    ("2023-12-21 16:10:00+00:00",639),
    ("2023-06-21 05:10:00+00:00",1708),
    ("2023-09-21 07:10:00+00:00",630),
])
def test_real_layout_nearly_coincident_edges(timestamp,index):
    # These exact cases previously violated eta_joint <= min(eta_s, eta_b)
    # by up to 0.64 despite GEOS reporting each polygon as valid.
    from pathlib import Path
    import pandas as pd
    from field import load_layout
    from solar import sun_vector
    root=Path(__file__).resolve().parents[1]
    weather=pd.read_csv(root/'data/processed/gemasolar_dni_2023.csv').set_index('time_utc')
    sun=sun_vector(37.562,-5.33,timestamp,altitude=170,
                   temperature=weather.loc[timestamp,'temperature_c'])
    mirrors=load_layout(root/'data/processed/gemasolar_layout.csv')
    field=PreparedField.from_mirrors(mirrors,sun.sun_to_sky)
    result=field.target_efficiencies(index)
    assert result['eta_joint'] <= min(result['eta_shadow'],result['eta_blocking'])+1e-9
    assert result['eta_shadow'] == pytest.approx(shadow_efficiency(mirrors[index],mirrors,sun.sun_to_sky),abs=1e-8)
    if result['blocking_occluders']==0:
        assert result['eta_joint']==pytest.approx(result['eta_shadow'],abs=1e-9)
