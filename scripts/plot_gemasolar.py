"""Export source-backed scientific figures from completed calculation outputs."""

from pathlib import Path

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

ROOT=Path(__file__).resolve().parents[1]


def main():
    out=ROOT/'reports/gemasolar_2023'
    hourly=pd.read_csv(out/'hourly_summary.csv')
    daily=pd.read_csv(out/'daily_summary.csv')
    layout=pd.read_csv(ROOT/'data/processed/gemasolar_layout.csv')
    radiation=pd.read_csv(ROOT/'data/processed/gemasolar_dni_2023.csv',parse_dates=['time_utc'])
    plt.rcParams.update({'font.family':'DejaVu Sans','font.size':11,'axes.spines.top':False,'axes.spines.right':False})
    # Select a real calculated morning, stated explicitly on the plot.
    candidates=hourly[(hourly.status=='calculated')&(hourly.dni_w_m2>100)&(hourly.elevation_deg>=8)]
    sample=candidates.loc[candidates.elevation_deg.idxmin()]
    detail=pd.read_csv(out/'per_mirror.csv.gz')
    snapshot=detail[detail.time_utc==sample.time_utc].merge(layout,on='mirror_id',validate='one_to_one')
    if len(snapshot)!=2650:raise ValueError('Incomplete figure snapshot')
    fig,ax=plt.subplots(figsize=(9,8),layout='constrained')
    points=ax.scatter(snapshot.x,snapshot.y,c=100*(1-snapshot.eta_joint),cmap='YlOrBr',vmin=0,vmax=100,s=9,edgecolors='#777777',linewidths=.1)
    ax.plot(0,0,'k+',markersize=12,label='Receiver axis')
    ax.set(aspect='equal',xlabel='East x (m)',ylabel='North y (m)',title='Gemasolar public research layout: 2,650 heliostats\n'+sample.time_local+' | joint geometric loss')
    ax.legend(loc='lower right');ax.grid(alpha=.15)
    fig.colorbar(points,ax=ax,label='Mirror area lost to shadow OR blocking (%)',shrink=.75)
    fig.text(.02,.006,'Map-extracted layout; flat terrain and assumed geometry. Not an as-built survey.',fontsize=9)
    fig.savefig(out/'field_joint_loss.png',dpi=160);plt.close(fig)
    fig,axes=plt.subplots(2,2,figsize=(12,7),sharex=True,sharey=True,layout='constrained')
    for ax,(day,group) in zip(axes.flat,hourly.groupby('date_local')):
        # Each day has one UTC offset; pandas scalar parser preserves it.
        hours=np.array([pd.Timestamp(t).hour+pd.Timestamp(t).minute/60 for t in group.time_local])
        ax.plot(hours,group.incident_normal_mw,color='#BA7A36',ls=':',label='DNI x reflective area reference')
        ax.plot(hours,group.incident_cosine_mw,color='#78818B',ls='--',label='After cosine')
        ax.plot(hours,group.geometric_usable_mw,color='#27659B',lw=2,label='After joint shadow/blocking')
        ax.plot(hours,group.optical_upper_bound_mw,color='#247B50',lw=2,label='At receiver: full optical chain')
        ax.plot(hours,group.receiver_absorbed_mw,color='#7A3E8E',lw=2,label='After receiver absorptivity')
        ax.set(title=day,xlim=(0,24),ylim=(0,330),xticks=[0,6,12,18,24],xlabel='Local civil hour',ylabel='Model light power (MW)')
        ax.grid(alpha=.2)
    axes.flat[0].legend(fontsize=8,loc='upper left')
    fig.suptitle('Historical DNI + geometry + mirror/receiver optical chain | assumed clear-air transmission',fontsize=12)
    fig.savefig(out/'seasonal_power.png',dpi=160);plt.close(fig)
    fig,ax=plt.subplots(figsize=(10,5),layout='constrained')
    bottom=np.zeros(len(daily))
    segments=[('receiver_net_thermal','After receiver thermal-efficiency scenario','#247B50'),
              ('receiver_thermal_loss','Receiver thermal loss scenario','#4A8B60'),
              ('receiver_absorption_loss','Receiver absorption loss','#9A7BB5'),
              ('interception_loss','Receiver interception loss','#5AA6B8'),
              ('atmospheric_after_reflection_loss','Atmospheric path loss','#78B58C'),
              ('cleanliness_loss','Mirror cleanliness loss','#D6A249'),
              ('reflection_loss','Mirror reflection loss','#C56A45'),
              ('joint_loss','Joint shadow/blocking loss','#D39846'),
              ('cosine_loss','Cosine loss','#8993A1')]
    for key,label,color in segments:
        values=100*daily[f'{key}_mwh_estimate']/daily.incident_normal_mwh_estimate
        ax.bar(daily.date_local,values,bottom=bottom,label=label,color=color)
        for x,value,base in zip(range(len(daily)),values,bottom):
            ax.text(x,base+value/2,f'{value:.1f}%',ha='center',va='center',fontsize=9)
        bottom+=values
    ax.set(ylim=(0,100),ylabel='Share of DNI x reflective area reference (%)',
           title='Daily energy budget | all segments use the same denominator')
    ax.legend(loc='upper center',bbox_to_anchor=(.5,-.10),ncol=2,fontsize=9)
    fig.savefig(out/'loss_budget.png',dpi=160);plt.close(fig)
    monthly=radiation.set_index('time_utc').dni_w_m2.resample('MS').sum()/1000
    fig,ax=plt.subplots(figsize=(10,4),layout='constrained')
    ax.bar(np.arange(1,13),monthly,color='#27659B')
    ax.set(xticks=np.arange(1,13),xlabel='UTC calendar month in 2023',ylabel='DNI irradiation estimate (kWh/m²)',
           title='PVGIS-SARAH3 historical DNI at Gemasolar | hourly-sample integration',ylim=(0,float(monthly.max())*1.18))
    ax.grid(axis='y',alpha=.2)
    for month,value in enumerate(monthly,1):ax.text(month,value+3,f'{value:.0f}',ha='center',fontsize=9)
    fig.savefig(out/'monthly_dni.png',dpi=160);plt.close(fig)
    print('Created field_joint_loss.png, seasonal_power.png, monthly_dni.png, loss_budget.png')


if __name__=='__main__':main()
