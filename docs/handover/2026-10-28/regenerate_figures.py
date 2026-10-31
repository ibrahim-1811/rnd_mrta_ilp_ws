#!/usr/bin/env python3
"""Recreate the freeze README charts from included CSV evidence; no simulation."""
from pathlib import Path
import csv
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np
base=Path(__file__).resolve().parent
figdir=base.parents[1]/'figures'
figdir.mkdir(exist_ok=True)
inputs={
 'pilot_summary':base/'pilot_summary.csv',
 'pilot_paired':base/'pilot_paired.csv',
 'm1_signal':base/'m1_signal.csv',
 'legacy_fleet_sweep':base/'legacy_fleet_sweep.csv',
}
rows=list(csv.DictReader(inputs['pilot_summary'].open())); paired=list(csv.DictReader(inputs['pilot_paired'].open()))
strategies=['nearest_astar','milp','earliest_finish','hungarian','multi_criteria','auction','nearest_manhattan','random']
labels={'nearest_astar':'Nearest A*','milp':'MILP','earliest_finish':'Earliest Finish','hungarian':'Hungarian','multi_criteria':'Multi Criteria','auction':'Auction','nearest_manhattan':'Nearest Manhattan','random':'Random'}
plt.rcParams.update({'font.family':'DejaVu Sans','font.size':10,'axes.spines.top':False,'axes.spines.right':False,'axes.titleweight':'bold'})
fig,axs=plt.subplots(1,2,figsize=(13.5,5.4),gridspec_kw={'width_ratios':[1.1,1]})
for i,s in enumerate(strategies):
 vals=[float(r['order_makespan_s']) for r in rows if r['strategy']==s]
 mean=np.mean(vals);color='#2563eb' if s=='nearest_astar' else '#607890'
 axs[0].plot([min(vals),max(vals)],[i,i],color=color,alpha=.6,lw=3)
 axs[0].scatter(vals,[i]*len(vals),s=28,color=color,alpha=.75,zorder=3)
 axs[0].scatter([mean],[i],marker='D',s=53,color='#142d47',zorder=4)
 axs[0].text(465,i,f'{mean:.2f}',va='center',fontsize=9)
 if s=='nearest_astar':axs[1].scatter([0],[i],marker='D',color='#2563eb')
 else:
  r=next(r for r in paired if r['strategy']==s and r['metric']=='order_makespan_s')
  val=float(r['mean_difference']);lo=float(r['ci_low']);hi=float(r['ci_high'])
  axs[1].errorbar([val],[i],xerr=[[val-lo],[hi-val]],fmt='D',capsize=3,color='#607890')
for ax in axs:
 ax.set_yticks(range(8),[labels[s] for s in strategies]);ax.invert_yaxis();ax.grid(axis='x',alpha=.18);ax.set_axisbelow(True)
axs[0].set_xlim(350,490);axs[0].set_xlabel('Order makespan (simulated seconds; lower is better)');axs[0].set_title('Individual seeds and means (n = 3)\nLine = observed min–max, diamond = mean',fontsize=11)
axs[1].axvline(0,color='#94a3b8',ls='--');axs[1].set_xlabel('Strategy minus Nearest A* (simulated seconds)');axs[1].set_title('Paired differences with 95% t intervals\nIntervals are unadjusted for multiple comparisons',fontsize=11)
fig.suptitle('W100 · 50 robots · 200 orders · seeds 1–3 · q = 0 · eight conventional strategies',fontsize=13,fontweight='bold',y=1.01)
fig.text(.5,-.02,'Exploratory pre-M0 pilot: 24/24 completed, 0 reservation violations, no charging sessions. Three seeds do not establish a general winner.',ha='center',fontsize=9,color='#475569')
fig.tight_layout();fig.savefig(figdir/'handover_pilot_r50.png',dpi=170,bbox_inches='tight');plt.close(fig)
rows=list(csv.DictReader(inputs['m1_signal'].open()));rows.sort(key=lambda r:(int(r['robots']),float(r['quantum_s'])))
x=np.arange(len(rows));labs=[r['robots']+'r / q'+str(int(float(r['quantum_s']))) for r in rows]
fig,axs=plt.subplots(1,2,figsize=(13,4.9))
w=.35
axs[0].bar(x-w/2,[float(r['residual_p90_s']) for r in rows],width=w,color='#7c3aed',label='90th percentile realized residual')
axs[0].bar(x+w/2,[float(r['margin_median_s']) for r in rows],width=w,color='#059669',label='Median static decision margin')
axs[0].set_ylabel('Simulated seconds');axs[0].set_title('Waiting can exceed small static margins');axs[0].legend(fontsize=8)
vals=[100*float(r['exceeds_margin_share']) for r in rows]
axs[1].bar(x,vals,color=['#93c5fd','#2563eb']*3,width=.65)
for i,v in enumerate(vals):axs[1].text(i,v+1.3,f'{v:.1f}%',ha='center',fontsize=9)
axs[1].set_ylim(0,70);axs[1].set_ylabel('Share of comparable selected cases (%)');axs[1].set_title('Residual greater than static margin\nDoes not establish an alternative would win',fontsize=11)
for ax in axs:ax.set_xticks(x,labs,rotation=20);ax.grid(axis='y',alpha=.16);ax.set_axisbelow(True)
fig.suptitle('M1 stored diagnostic pilot · Nearest A* · seed 101 · 300 orders per condition',fontsize=13,fontweight='bold',y=1.02)
fig.text(.5,-.03,'Six historical diagnostic outputs from dirty research source; analyzer has since changed and its feature-report test currently fails. No learned policy is evaluated.',ha='center',fontsize=9,color='#475569')
fig.tight_layout();fig.savefig(figdir/'handover_m1_diagnostics.png',dpi=170,bbox_inches='tight');plt.close(fig)
rows=list(csv.DictReader(inputs['legacy_fleet_sweep'].open()));fig,axs=plt.subplots(1,2,figsize=(12.5,4.7))
for strategy,color in [('nearest_astar','#2563eb'),('nearest_manhattan','#d97706'),('random','#059669')]:
 subset=[r for r in rows if r['strategy']==strategy]
 fleets=sorted({int(r['robots']) for r in subset})
 for ax,key in zip(axs,['makespan_s','charge_sessions']):
  values=[[float(r[key]) for r in subset if int(r['robots'])==fleet] for fleet in fleets]
  means=[float(np.mean(v)) for v in values]
  ax.plot(fleets,means,marker='o',color=color,label=labels[strategy])
  ax.fill_between(fleets,[min(v) for v in values],[max(v) for v in values],alpha=.13,color=color)
for ax in axs:ax.set_xlabel('Fleet size');ax.set_xticks([30,40,50]);ax.grid(alpha=.17);ax.set_axisbelow(True)
axs[0].set_ylabel('Fleet settled time (simulated seconds)');axs[0].set_title('Legacy schema: recovery included');axs[0].legend()
axs[1].set_ylabel('Charging sessions');axs[1].set_title('Long workloads exercise charging')
fig.suptitle('Historical W100 sweep · 2,000 reference orders · 30 / 40 / 50 robots',fontsize=13,fontweight='bold',y=1.02)
fig.text(.5,-.03,'Random: mean and range of allocation RNG seeds 1–3 on one workload. Old manifest seed labels differ; not a current paired study.',ha='center',fontsize=9,color='#475569')
fig.tight_layout();fig.savefig(figdir/'handover_legacy_fleet_sweep.png',dpi=170,bbox_inches='tight');plt.close(fig)
print('Wrote three evidence-derived charts to',figdir)
