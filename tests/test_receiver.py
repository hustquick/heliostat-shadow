"""Finite receiver interception and complete optical-chain checks."""

import numpy as np
import pytest

from heliostat import Heliostat
from receiver import CylindricalReceiver, cylinder_interception, effective_angular_sigma
from simulation import PreparedField


def test_receiver_interception_is_bounded_and_quadrature_converges():
    centres=np.array([[40.,0.,0.],[500.,0.,0.]])
    aims=np.array([[0.,0.,140.],[0.,0.,140.]])
    receiver=CylindricalReceiver((0.,0.,140.),4.,10.5)
    sigma=effective_angular_sigma(np.array([.8,.8]))
    low=cylinder_interception(centres,aims,sigma,receiver,order=32)
    high=cylinder_interception(centres,aims,sigma,receiver,order=64)
    assert np.all((low>=0)&(low<=1))
    np.testing.assert_allclose(low,high,atol=1e-8)


@pytest.mark.parametrize('value',[-1.,1.1,np.nan])
def test_receiver_parameter_bounds(value):
    m=[Heliostat('a',(0,0,0),2,2,(0,0,20))]
    field=PreparedField.from_mirrors(m,[0,0,1])
    with pytest.raises(ValueError):field.evaluate(800,mirror_cleanliness=value)
    with pytest.raises(ValueError):field.evaluate(800,receiver_absorptivity=value)


def test_complete_chain_has_reflectivity_cleanliness_intercept_and_absorption():
    mirrors=[Heliostat('a',(40,0,0),2,2,(0,0,140))]
    receiver=CylindricalReceiver((0,0,140),4,10.5)
    result=PreparedField.from_mirrors(mirrors,[0,0,1]).evaluate(
        800,reflective_area_m2=3.5,mirror_reflectivity=.88,mirror_cleanliness=.95,
        receiver=receiver,receiver_absorptivity=.93,receiver_thermal_efficiency=.8916).iloc[0]
    assert 0 < result.eta_intercept < 1
    assert result.eta_optical_upper_bound == pytest.approx(
        result.eta_cosine*result.eta_joint*result.eta_atmosphere*.88*.95*result.eta_intercept)
    assert result.eta_absorbed == pytest.approx(result.eta_optical_upper_bound*.93)
    assert result.eta_net_thermal == pytest.approx(result.eta_absorbed*.8916)
    chain=(result.reflection_loss_power_w+result.cleanliness_loss_power_w+
           result.atmospheric_after_reflection_loss_power_w+result.interception_loss_power_w+
           result.receiver_absorption_loss_power_w+result.receiver_thermal_loss_power_w+
           result.receiver_net_thermal_power_w)
    assert chain == pytest.approx(result.geometric_usable_power_w,abs=1e-8)
