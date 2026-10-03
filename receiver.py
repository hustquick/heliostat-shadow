"""Finite cylindrical receiver interception of an equivalent Gaussian beam.

The blur uses literature angular errors. Surface integration and its Jacobian
are derived here, rather than claiming a reproduction of UNIZAR/SolTRACE.
The geometrical mirror rectangles used for shading remain unchanged.
"""

from dataclasses import dataclass
from functools import lru_cache

import numpy as np
from numpy.polynomial.legendre import leggauss


@dataclass(frozen=True)
class CylindricalReceiver:
    centre: tuple[float, float, float]
    radius: float
    height: float

    def __post_init__(self):
        if np.shape(self.centre) != (3,) or not np.isfinite(self.centre).all():
            raise ValueError('Receiver centre must be a finite 3-vector')
        if not np.isfinite([self.radius,self.height]).all() or min(self.radius,self.height)<=0:
            raise ValueError('Receiver radius and height must be finite and positive')


def effective_angular_sigma(cosine, *, sun_mrad=2.51, slope_mrad=2.6, tracking_mrad=2.1):
    """Circular Gaussian sigma in rad; Sánchez-González et al., Eq. (1).

    The cited printed equation is sigma² = sun² + 2(1+cos(theta))*slope²
    + tracking². Inputs are per-distribution standard deviations, not FWHM.
    Errors affect interception, not an extra multiplicative efficiency.
    """
    cosine=np.asarray(cosine,dtype=float)
    errors=np.asarray([sun_mrad,slope_mrad,tracking_mrad],dtype=float)
    if not np.isfinite(cosine).all() or np.any((cosine<0)|(cosine>1+1e-12)):
        raise ValueError('Incidence cosine must lie in [0,1]')
    if not np.isfinite(errors).all() or np.any(errors<0) or not np.any(errors>0):
        raise ValueError('Angular error sigmas must be nonnegative and not all zero')
    return np.sqrt(sun_mrad**2+2*(1+np.minimum(cosine,1))*slope_mrad**2+tracking_mrad**2)*1e-3


@lru_cache(maxsize=8)
def _nodes(order):
    if not isinstance(order,int) or order<8:
        raise ValueError('Quadrature order must be an integer >= 8')
    return leggauss(order)


def cylinder_interception(centres, aim_points, sigma_rad, receiver, *, order=64):
    """Integrate Gaussian ray-direction density over the visible cylinder wall.

    q=X-origin, w=central ray, (u,v) its transverse frame. Slope coordinates
    are a=q.u/(q.w), b=q.v/(q.w), with independent N(0,sigma²) densities.
    d(a,b)/dS = |n.q|/(q.w)^3. Only the near, externally visible side wall
    counts, so rays hitting a nonabsorbing top/bottom or missing the wall spill.
    A converged quadrature is required; use audit_optics to compare resolutions.
    Focus is represented by an equivalent angular beam emitted from the mirror
    centre. Facet canting/aberrations and correlation with partial masks are not
    individually ray-traced.
    """
    centres=np.asarray(centres,dtype=float);aims=np.asarray(aim_points,dtype=float)
    if centres.ndim!=2 or centres.shape[1]!=3 or aims.shape!=centres.shape or not np.isfinite([centres,aims]).all():
        raise ValueError('Centres and aim points must be matching finite (N,3) arrays')
    sigmas=np.broadcast_to(np.asarray(sigma_rad,dtype=float),(len(centres),))
    if not np.isfinite(sigmas).all() or np.any(sigmas<=0):
        raise ValueError('Gaussian sigmas must be finite and positive')
    delta=centres-np.asarray(receiver.centre)
    radial=np.linalg.norm(delta[:,:2],axis=1)
    if np.any(radial<=receiver.radius):
        raise ValueError('Mirror centres must be outside the receiver cylinder')
    w=aims-centres;distances=np.linalg.norm(w,axis=1)
    if np.any(distances<=0):raise ValueError('Aim point cannot coincide with mirror centre')
    w/=distances[:,None]
    # A horizontal transverse axis is stable for this external-cylinder model.
    horizontal=np.linalg.norm(w[:,:2],axis=1)
    if np.any(horizontal<=1e-12):raise ValueError('Central rays must have a horizontal component')
    u=np.column_stack((-w[:,1]/horizontal,w[:,0]/horizontal,np.zeros(len(w))))
    v=np.cross(w,u)
    nodes,weights=_nodes(order)
    z=receiver.centre[2]+nodes*receiver.height/2
    answer=np.empty(len(centres))
    for start in range(0,len(centres),64):
        stop=min(start+64,len(centres));sl=slice(start,stop)
        half=np.arccos(receiver.radius/radial[sl])
        phi=np.arctan2(delta[sl,1],delta[sl,0])[:,None]+half[:,None]*nodes
        nx=np.cos(phi);ny=np.sin(phi)
        qx=receiver.centre[0]+receiver.radius*nx-centres[sl,0,None]
        qy=receiver.centre[1]+receiver.radius*ny-centres[sl,1,None]
        qz=z[None,:]-centres[sl,2,None]
        def dot(vec):
            return (qx*vec[:,0,None]+qy*vec[:,1,None])[:,:,None]+(qz*vec[:,2,None])[:,None,:]
        forward=dot(w[sl]);safe=np.maximum(forward,1e-15)
        a=dot(u[sl])/safe;b=dot(v[sl])/safe
        sigma=sigmas[sl,None,None]
        density=np.exp(-(a*a+b*b)/(2*sigma*sigma))/(2*np.pi*sigma*sigma)
        inward=-(nx*qx+ny*qy)
        jacobian=inward[:,:,None]/safe**3
        integrand=np.where(forward>0,density*jacobian,0.)
        integral=np.einsum('ijk,j,k->i',integrand,weights,weights)
        answer[sl]=integral*receiver.radius*half*receiver.height/2
    if np.any(~np.isfinite(answer)) or np.any(answer>1+1e-6) or np.any(answer<0):
        raise ArithmeticError('Receiver integration failed; increase quadrature order')
    return np.clip(answer,0,1)
