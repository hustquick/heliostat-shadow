"""External finite-candidate relocation benchmark; never modifies an APP or catalogue."""
from __future__ import annotations
import argparse
from dataclasses import replace
import json
from pathlib import Path
import tempfile
import time
import numpy as np
from scipy.spatial import ConvexHull
from scripts.annual_energy import weather_samples, evaluate_energy
from scripts.optimize_ps10_greedy import receiver_for, retarget, save_layout, fast_screen_scores
from field import load_layout
from viewer.workspace import ViewerWorkspace


def compress_samples(samples, count):
    """Deterministic weighted farthest-point sky medoids, conserving DNI integral.

    This is a search approximation, not a validated annual quadrature rule.
    """
    if count >= len(samples):
        return samples
    suns = np.array([s.sun for s in samples])
    weights = np.array([s.dni*s.duration_hours for s in samples])
    selected = [int(np.argmax(weights))]
    distance = np.full(len(samples), np.inf)
    while len(selected) < count:
        distance = np.minimum(distance, np.sum((suns-suns[selected[-1]])**2, axis=1))
        priority=distance*weights
        priority[selected]=-1
        selected.append(int(np.argmax(priority)))
    labels = np.argmin(np.sum((suns[:,None]-suns[selected][None,:])**2, axis=2), axis=1)
    result = []
    for k, index in enumerate(selected):
        members = np.flatnonzero(labels == k)
        if not len(members):
            continue
        hours = sum(samples[j].duration_hours for j in members)
        result.append(replace(samples[index], dni=float(weights[members].sum()/hours), duration_hours=hours))
    return result


def coupling_graph(mirrors, samples):
    """Weighted potential ray-tube overlap, NOT actual shading/blocking losses.

    Spheres circumscribe mirrors. All pairs are considered; no nearest-k cutoff.
    For proposal generation only, with per-ray overlap decreasing with separation.
    """
    centres = np.array([m.centre for m in mirrors])
    delta = centres[:,None,:]-centres[None,:,:]
    radii = np.array([np.hypot(m.width,m.height)/2 for m in mirrors])
    reach = radii[:,None]+radii[None,:]
    graph = np.zeros((len(mirrors),len(mirrors)))
    total = sum(s.dni*s.duration_hours for s in samples)
    for sample in samples:
        reflected = np.array([np.array(m.aim_point)-np.array(m.centre) for m in mirrors])
        reflected /= np.linalg.norm(reflected,axis=1)[:,None]
        for rays in (np.broadcast_to(sample.sun, reflected.shape), reflected):
            along = np.einsum('ijk,ik->ij',delta,rays)
            perpendicular = np.sqrt(np.maximum(0,np.sum(delta**2,axis=2)-along**2))
            # Beyond the receiver aim plane there is no blocking candidate.
            valid = np.ones_like(along,dtype=bool)
            if rays is reflected:
                distance = np.array([np.linalg.norm(np.subtract(m.aim_point,m.centre)) for m in mirrors])
                valid = (along <= reach) & (-along <= distance[:,None]+reach)
            potential = np.maximum(0,1-perpendicular/reach)*valid
            graph += sample.dni*sample.duration_hours*potential/total
    graph = np.maximum(graph,graph.T)
    np.fill_diagonal(graph,0)
    return graph


def pair_regions(graph, limit):
    """Strongest disjoint pairs. Rebuilt each round; may cross spatial sectors."""
    i,j = np.triu_indices(len(graph),1)
    order = np.argsort(-graph[i,j],kind='stable')
    used=set(); pairs=[]
    for k in order:
        a,b=int(i[k]),int(j[k])
        if graph[a,b]<=0 or len(pairs)>=limit:
            break
        if a not in used and b not in used:
            pairs.append((a,b)); used.update((a,b))
    return pairs


def unit(v):
    return v/np.linalg.norm(v) if np.linalg.norm(v)>1e-10 else np.array([1.,0.])


def pair_changes(xy, i, j, step):
    axes = [np.array([1.,0.]),np.array([-1.,0.]),np.array([0.,1.]),np.array([0.,-1.])]
    for direction in axes:
        yield "pair_translate",{i:xy[i]+step*direction,j:xy[j]+step*direction}
    separation=unit(xy[i]-xy[j])
    tangent=np.array([-separation[1],separation[0]])
    for direction in (separation,-separation,tangent,-tangent):
        yield "pair_relative",{i:xy[i]+step*direction,j:xy[j]-step*direction}


def feasible_regions(mirrors,graph,steps,receiver,hull,margin,limit):
    """Strong disjoint pairs with at least one legal simultaneous displacement."""
    xy=np.array([m.centre[:2] for m in mirrors])
    i,j=np.triu_indices(len(graph),1)
    order=np.argsort(-graph[i,j],kind="stable")
    used=set(); pairs=[]; checked=0
    for k in order:
        a,b=int(i[k]),int(j[k])
        if graph[a,b]<=0 or len(pairs)>=limit: break
        if a in used or b in used: continue
        checked+=1
        if any(legal_trial(mirrors,changes,receiver,hull,margin) is not None
               for step in steps for _,changes in pair_changes(xy,a,b,step)):
            pairs.append((a,b)); used.update((a,b))
    return pairs,checked


def proposals(mirrors, samples, receiver, graph, mode, step, pair_limit, regions=None):
    xy = np.array([m.centre[:2] for m in mirrors])
    axes = [np.array([1.,0.]),np.array([-1.,0.]),np.array([0.,1.]),np.array([0.,-1.])]
    for i,m in enumerate(mirrors):
        directions=list(axes)
        if mode != 'grid':
            # Finite-difference gradient of cosine/transmission is a proposal only.
            offsets=np.array(axes)*.25
            scores=fast_screen_scores(xy[i]+offsets,m,samples,receiver)
            gradient=unit(np.array([scores[0]-scores[1],scores[2]-scores[3]]))
            j=int(np.argmax(graph[i]))
            separation=unit(xy[i]-xy[j]) if graph[i,j]>0 else unit(xy[i]-receiver.centre[:2])
            directions.extend((gradient,-gradient,separation,-separation))
        seen=set()
        for direction in directions:
            point=xy[i]+step*direction
            key=tuple(np.round(point,8))
            if key in seen: continue
            seen.add(key)
            yield 'single', {i:point}
    if mode == 'cooperative':
        for i,j in (pair_regions(graph,pair_limit) if regions is None else regions):
            yield from pair_changes(xy,i,j,step)


def legal_trial(mirrors, changes, receiver, hull, margin):
    xy=np.array([m.centre[:2] for m in mirrors])
    for i,point in changes.items():
        if np.any(hull[:,:2]@point+hull[:,2]>1e-7): return None
        if np.linalg.norm(point-np.array(receiver.centre[:2])) <= receiver.radius+np.hypot(mirrors[i].width,mirrors[i].height)/2+margin:
            return None
        xy[i]=point
    radii=np.array([np.hypot(m.width,m.height)/2 for m in mirrors])
    for i in changes:
        distance=np.linalg.norm(xy-xy[i],axis=1)
        required=radii+radii[i]+margin
        distance[i]=np.inf
        if np.any(distance<required-1e-9): return None
    result=list(mirrors)
    for i,point in changes.items(): result[i]=retarget(mirrors[i],point,receiver)
    return result


def search(initial,samples,config,receiver,mode,steps,rounds,pair_limit,margin,threshold,output,score=None):
    score = score or (lambda field: evaluate_energy(field,samples,config,receiver)['receiver_incident_kwh'])
    hull=ConvexHull(np.array([m.centre[:2] for m in initial])).equations
    current=list(initial); value=score(current); baseline=value
    history=[]; total_evaluations=1; started=time.perf_counter()
    stop='round_budget'
    for round_index in range(rounds):
        graph=coupling_graph(current,samples)
        pairs,region_pairs_checked=(feasible_regions(current,graph,steps,receiver,hull,margin,pair_limit)
                                    if mode == "cooperative" else ([],0))
        counts={}; best_by_kind={}
        print(f"{mode} round {round_index+1}: {len(pairs)} movable pairs",flush=True)
        best=value; winner=None; legal=0; proposed=0
        for step in steps:
            for kind,changes in proposals(current,samples,receiver,graph,mode,step,pair_limit,regions=pairs):
                proposed+=1
                trial=legal_trial(current,changes,receiver,hull,margin)
                if trial is None: continue
                candidate=score(trial); legal+=1; total_evaluations+=1
                counts[kind]=counts.get(kind,0)+1
                best_by_kind[kind]=max(best_by_kind.get(kind,float("-inf")),candidate-value)
                if candidate>best:
                    best=candidate; winner=(trial,kind,changes,step)
                if legal%250==0:
                    print(f'{mode} round {round_index+1}: evaluated {legal}; best gain {best-value:.3f} kWh',flush=True)
        record=dict(round=round_index+1,proposed=proposed,evaluated=legal,before_kwh=value,after_kwh=best,
                    evaluated_by_kind=counts,best_gain_kwh_by_kind=best_by_kind,region_pairs_checked=region_pairs_checked,
                    pairs=[dict(ids=[current[i].mirror_id,current[j].mirror_id],potential=float(graph[i,j])) for i,j in pairs])
        if winner is None or best-value<=threshold:
            record["after_kwh"]=value
            stop='no_improving_defined_candidate'; history.append(record); break
        current,kind,changes,step=winner
        record.update(kind=kind,step_m=step,moves=[dict(mirror_id=current[i].mirror_id,xy=list(p)) for i,p in changes.items()])
        history.append(record); value=best
        print(f'{mode}: accepted {kind}; search gain {(value/baseline-1)*100:.5f}%',flush=True)
    output.mkdir(parents=True,exist_ok=True)
    save_layout(output/'search_layout.csv',current)
    return current,dict(mode=mode,baseline_search_kwh=baseline,final_search_kwh=value,
        elapsed_search_seconds=time.perf_counter()-started,evaluations=total_evaluations,history=history,stop_reason=stop,
        candidate_argmax=True,continuous_global_optimum_claim=False,pair_limit=pair_limit,
        boundary='initial mirror-centre convex hull; not a surveyed site boundary',margin_m=margin)


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--plant',default='ps10'); p.add_argument('--layout',type=Path)
    p.add_argument('--modes',nargs='+',choices=['grid','physics','cooperative'],default=['grid','physics','cooperative'])
    p.add_argument('--samples',type=int,default=16,help='Approximate sky medoids for search; 288 keeps all month/hour samples')
    p.add_argument('--steps',nargs='+',type=float,default=[2.]); p.add_argument('--rounds',type=int,default=1)
    p.add_argument('--pair-limit',type=int,default=16); p.add_argument('--margin',type=float,default=1.)
    p.add_argument('--threshold-kwh',type=float,default=.001)
    p.add_argument('--full-year',action='store_true',help='Required for annual acceptance; otherwise exports unvalidated candidates only')
    p.add_argument('--output',type=Path,default=Path('reports/external_field_search'))
    args=p.parse_args()
    if args.samples<1 or args.rounds<1 or args.pair_limit<1 or not np.isfinite(args.steps).all() or min(args.steps)<=0 or not np.isfinite([args.margin,args.threshold_kwh]).all() or min(args.margin,args.threshold_kwh)<0:
        p.error('Invalid search budget, step, margin or threshold')
    root=Path(__file__).resolve().parents[1]
    args.output.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='field-search-') as cache:
        workspace=ViewerWorkspace(root,user_root=cache); workspace.select(args.plant); model=workspace.active
        initial=load_layout(args.layout) if args.layout else list(model.mirrors)
        if len(initial)<3: raise ValueError('At least three non-collinear mirrors required')
        reference={m.mirror_id:(m.width,m.height) for m in model.mirrors}
        if args.layout and reference!={m.mirror_id:(m.width,m.height) for m in initial}:
            raise ValueError('Custom layout must preserve catalogue mirror IDs and dimensions')
        receiver=receiver_for(model.config)
        stratified,provenance=weather_samples(model,stratified=True)
        samples=compress_samples(stratified,args.samples)
        full,full_provenance=weather_samples(model)
        print(f"{args.plant}: {len(initial)} mirrors; {len(samples)} search directions; {len(full)} hourly validation samples",flush=True)
        if args.full_year: print("Evaluating full-year baseline...",flush=True)
        baseline_full=evaluate_energy(initial,full,model.config,receiver) if args.full_year else None
        reports=[]
        for mode in args.modes:
            out=args.output/mode
            candidate,report=search(initial,samples,model.config,receiver,mode,args.steps,args.rounds,args.pair_limit,args.margin,args.threshold_kwh,out)
            if args.full_year:
                print(f"{mode}: validating full-year candidate...",flush=True)
                evaluated=evaluate_energy(candidate,full,model.config,receiver)
                a=baseline_full['receiver_incident_kwh']; b=evaluated['receiver_incident_kwh']
                accepted=b>a+args.threshold_kwh
                report.update(annual_baseline_kwh=a,annual_candidate_kwh=b,annual_relative_gain=b/a-1,
                    annual_accepted=accepted,annual_engine=evaluated['engine'])
                save_layout(out/'validated_layout.csv',candidate if accepted else initial)
            else: report['annual_accepted']=None
            (out/'summary.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
            reports.append(report)
        result=dict(plant=args.plant,mirrors=len(initial),search_samples=len(samples),search_provenance=provenance,
                    full_provenance=full_provenance,steps_m=args.steps,reports=reports,
                    sampling_scope='DNI integral conserved; optical approximation error must be checked on full year',
                    model_scope='Receiver incident optical energy, not electricity; reconstructed geometry; no APP integration')
        (args.output/'comparison.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
        print(json.dumps({r['mode']:{k:v for k,v in r.items() if k not in ('history',)} for r in reports},ensure_ascii=False),flush=True)

if __name__=='__main__': main()
