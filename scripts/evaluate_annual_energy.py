"""Evaluate a catalogue field and optional candidate using identical annual inputs."""
import argparse
import json
import tempfile
import time
from pathlib import Path
from field import load_layout
from viewer.workspace import ViewerWorkspace
from scripts.annual_energy import weather_samples, evaluate_energy
from scripts.optimize_ps10_greedy import receiver_for


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--plant',default='ps10')
    parser.add_argument('--candidate',type=Path)
    parser.add_argument('--stratified',action='store_true',help='Approximate month/hour bins instead of full hourly year')
    parser.add_argument('--output',type=Path,default=Path('reports/annual_energy'))
    args=parser.parse_args()
    root=Path(__file__).resolve().parents[1]
    start=time.perf_counter()
    with tempfile.TemporaryDirectory(prefix='heliostat-annual-') as cache:
        workspace=ViewerWorkspace(root,user_root=cache)
        workspace.select(args.plant)
        model=workspace.active
        samples,provenance=weather_samples(model,stratified=args.stratified)
        receiver=receiver_for(model.config)
        print(f'{args.plant}: {len(samples)} samples; {provenance["dni_source"]}',flush=True)
        baseline=evaluate_energy(model.mirrors,samples,model.config,receiver)
        candidate=None
        if args.candidate:
            mirrors=load_layout(args.candidate)
            before={m.mirror_id:(m.width,m.height) for m in model.mirrors}
            after={m.mirror_id:(m.width,m.height) for m in mirrors}
            if before != after:
                raise ValueError('Candidate must preserve mirror IDs and dimensions for a fair comparison')
            candidate=evaluate_energy(mirrors,samples,model.config,receiver)
        report=dict(plant_id=args.plant,provenance=provenance,baseline=baseline,candidate=candidate,
            elapsed_seconds=time.perf_counter()-start,model_scope='Receiver incident optical energy, not electricity; catalogue geometry is reconstructed.')
        if candidate is not None:
            report['relative_gain']=candidate['receiver_incident_kwh']/baseline['receiver_incident_kwh']-1 if baseline['receiver_incident_kwh'] else None
        args.output.mkdir(parents=True,exist_ok=True)
        (args.output/'annual_energy.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
        for name,result in [('baseline',baseline),('candidate',candidate)]:
            if result is not None:
                import pandas as pd
                pd.DataFrame(result['samples']).to_csv(args.output/f'{name}_samples.csv',index=False)
        print(json.dumps({k:v for k,v in report.items() if k not in ('baseline','candidate')},ensure_ascii=False),flush=True)
        print(f'baseline receiver incident energy: {baseline["receiver_incident_kwh"]:.3f} kWh',flush=True)

if __name__=='__main__':
    main()
