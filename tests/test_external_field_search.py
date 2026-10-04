from dataclasses import replace
import numpy as np
import pandas as pd
import pytest
from heliostat import Heliostat
from receiver import CylindricalReceiver
from scipy.spatial import ConvexHull
from scripts.annual_energy import Sample
from scripts.experiment_field_search import compress_samples,coupling_graph,pair_regions,legal_trial,search,proposals


def fixture():
    points=[(-30,-30),(30,-30),(30,30),(-30,30),(-4,5),(4,5)]
    mirrors=[Heliostat(str(i),(x,y,0),1,1,(0,0,30)) for i,(x,y) in enumerate(points)]
    samples=[Sample(pd.Timestamp('2025-06-21T12:00Z'),np.array([1.,0.,1.])/np.sqrt(2),800.)]
    return mirrors,samples,CylindricalReceiver((0,0,30),1,4)


def test_compression_conserves_integral_and_time():
    _,s,_=fixture()
    samples=[replace(s[0],dni=100+i,duration_hours=i+1) for i in range(10)]
    reduced=compress_samples(samples,3)
    assert len(reduced)==1 # Identical directions collapse to one occupied bin.
    assert sum(x.dni*x.duration_hours for x in reduced)==pytest.approx(sum(x.dni*x.duration_hours for x in samples))
    assert sum(x.duration_hours for x in reduced)==sum(x.duration_hours for x in samples)


def test_graph_and_disjoint_pairs():
    mirrors,s,r=fixture()
    g=coupling_graph(mirrors,s)
    assert np.all(g>=0) and np.allclose(g,g.T) and np.allclose(np.diag(g),0)
    g=np.array([[0,5,1,0],[5,0,0,1],[1,0,0,4],[0,1,4,0.]])
    assert pair_regions(g,2)==[(0,1),(2,3)]


def test_joint_collision_and_hull_rejection():
    mirrors,_,r=fixture(); h=ConvexHull([m.centre[:2] for m in mirrors]).equations
    assert legal_trial(mirrors,{4:np.array([4.,5.]),5:np.array([-4.,5.])},r,h,1) is not None
    assert legal_trial(mirrors,{4:np.array([0.,5.]),5:np.array([0.,5.])},r,h,1) is None
    assert legal_trial(mirrors,{4:np.array([31.,5.])},r,h,1) is None
    assert legal_trial(mirrors,{4:np.array([0.,0.])},r,h,1) is None


def test_search_is_exact_argmax_over_legal_defined_candidates(tmp_path):
    mirrors,s,r=fixture()
    score=lambda field: 100+sum((i+1)*m.centre[0] for i,m in enumerate(field))
    g=coupling_graph(mirrors,s); hull=ConvexHull([m.centre[:2] for m in mirrors]).equations
    values=[score(mirrors)]
    for _,changes in proposals(mirrors,s,r,g,'physics',1,2):
        trial=legal_trial(mirrors,changes,r,hull,1)
        if trial: values.append(score(trial))
    result,report=search(mirrors,s,{},r,'physics',[1],1,2,1,.001,tmp_path,score=score)
    assert score(result)==pytest.approx(max(values))
    assert report['final_search_kwh']==pytest.approx(max(values))
    assert sum(a.centre!=b.centre for a,b in zip(mirrors,result))==1


def test_cooperative_escape_single_mirror_stagnation(monkeypatch,tmp_path):
    mirrors,s,r=fixture()
    g=np.zeros((6,6));g[4,5]=g[5,4]=1
    monkeypatch.setattr('scripts.experiment_field_search.coupling_graph',lambda *_:g)
    def score(field):
        dx=np.array([field[i].centre[0]-mirrors[i].centre[0] for i in (4,5)])
        # Only coordinated displacement crosses this constructed local barrier.
        return 100+10*min(dx)-sum(abs(dx[0]-dx[1]) for _ in [0])
    single,one=search(mirrors,s,{},r,'grid',[1],1,2,1,.001,tmp_path/'single',score=score)
    joint,two=search(mirrors,s,{},r,'cooperative',[1],1,2,1,.001,tmp_path/'joint',score=score)
    assert single==mirrors
    assert score(joint)>score(single)
    assert two['history'][0]['kind']=='pair_translate'
    assert one['stop_reason']=='no_improving_defined_candidate'


def test_region_budget_skips_strong_but_immovable_pairs():
    from scripts.experiment_field_search import feasible_regions
    mirrors,s,r=fixture()
    mirrors[4]=replace(mirrors[4],width=10,height=10)
    mirrors[5]=replace(mirrors[5],width=10,height=10)
    graph=np.zeros((6,6));graph[4,5]=graph[5,4]=5;graph[0,1]=graph[1,0]=1
    hull=ConvexHull([m.centre[:2] for m in mirrors]).equations
    pairs,checked=feasible_regions(mirrors,graph,[1],r,hull,1,1)
    assert pairs==[(0,1)]
    assert checked==2
