"""Reference transmission values, projected-area equivalence and energy closure."""

import numpy as np
import pytest
from shapely.geometry import Polygon

from heliostat import Heliostat
from optics import atmospheric_transmittance
from shadow import effective_projected_area, shadow_efficiency
from simulation import PreparedField
from transform import project_vertices, projection_basis


@pytest.mark.parametrize('distance,expected',[
    (100.,0.981647), (500.,0.939335), (1000.,0.89531),
    (2000.,0.8015563529874019),
])
def test_published_atmospheric_reference(distance,expected):
    assert atmospheric_transmittance(distance)==pytest.approx(expected,abs=1e-12,rel=0)


@pytest.mark.parametrize('distance',[0.,-1.,np.nan,np.inf])
def test_invalid_slant_distance(distance):
    with pytest.raises(ValueError):atmospheric_transmittance([10.,distance])


def test_model_must_be_explicitly_supported():
    with pytest.raises(ValueError):atmospheric_transmittance(100.,model='unknown')


@pytest.mark.parametrize('dni',[0.,800.])
@pytest.mark.parametrize('area',[None,3.])
def test_sequential_budget_and_slant_distance(dni,area):
    # Unequal envelopes check both per-mirror areas and uniform aperture mode.
    mirrors=[Heliostat('a',(0,0,0),2,2,(3,0,4)),
             Heliostat('b',(20,0,0),3,2,(20,0,100))]
    result=PreparedField.from_mirrors(mirrors,[0,0,1]).evaluate(dni,reflective_area_m2=area)
    np.testing.assert_allclose(result.receiver_distance_m,[5.,100.])
    np.testing.assert_allclose(result.incident_normal_power_w,dni*np.array([4.,6.] if area is None else [3.,3.]))
    np.testing.assert_allclose(result.post_atmosphere_power_w,result.geometric_usable_power_w*result.eta_atmosphere)
    total=result[['cosine_loss_power_w','joint_loss_power_w','atmospheric_loss_power_w','post_atmosphere_power_w']].sum(axis=1)
    np.testing.assert_allclose(total,result.incident_normal_power_w,atol=1e-10)
    assert (result.atmospheric_loss_power_w>=0).all()


def test_projected_effective_area_already_contains_cosine_once():
    sun=np.array([0.,np.sqrt(.96),.2])
    # Parallel identical mirrors: half-width shift plus 10 m towards the sun.
    centre=np.array([2.,0.,0.])+10*sun
    mirrors=[Heliostat('target',(0,0,0),4,3,(0,0,100)),
             Heliostat('shade',tuple(centre),4,3,tuple(centre+[0,0,100]))]
    prepared=PreparedField.from_mirrors(mirrors,sun)
    basis=projection_basis(sun)
    polygons=[Polygon(project_vertices(v,[0,0,0],basis)) for v in prepared.vertices]
    area=effective_projected_area(polygons[0],[polygons[1]])
    ratio=shadow_efficiency(mirrors[0],mirrors,sun)
    result=prepared.evaluate(800.).iloc[0]
    assert ratio==pytest.approx(.5)
    assert result.eta_cosine==pytest.approx(np.sqrt(.6))
    assert result.eta_blocking==pytest.approx(1.)
    assert polygons[0].area==pytest.approx(12.*np.sqrt(.6))
    assert 800.*area==pytest.approx(result.geometric_usable_power_w,abs=1e-5)
    assert 800.*area==pytest.approx(800.*12.*result.eta_cosine*ratio,abs=1e-5)


def test_rounded_catalog_envelope_tolerance():
    mirror = Heliostat('rounded', (100, 0, 0), 12.041595, 9.633276, (0, 0, 195))
    field = PreparedField.from_mirrors([mirror], [0, 0, 1])
    assert field.evaluate(800., reflective_area_m2=116.).incident_normal_power_w.iloc[0] == 92800.
    with pytest.raises(ValueError):
        field.evaluate(800., reflective_area_m2=117.)
