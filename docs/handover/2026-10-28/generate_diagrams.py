#!/usr/bin/env python3
"""Build handover architecture SVGs and an XMind overview from audited facts."""
from pathlib import Path
from zipfile import ZipFile, ZIP_DEFLATED
import hashlib,json,subprocess
BASE=Path(__file__).resolve().parent
ROOT=BASE.parents[2] if BASE.name=='2026-10-28' else Path('/home/ibrahim/rnd_mrta_ilp_ws')
OUT=ROOT/'docs/figures';OUT.mkdir(parents=True,exist_ok=True)
common='''graph [fontname="DejaVu Sans",fontsize=18,bgcolor="white",pad="0.25",nodesep="0.30",ranksep="0.48",splines=polyline];
node [shape=box,style="rounded,filled",fillcolor="#edf5fc",color="#83aac5",fontcolor="#183044",fontname="DejaVu Sans",fontsize=15,margin="0.18,0.12",penwidth=1.3];
edge [color="#54758c",fontcolor="#3e5d73",fontname="DejaVu Sans",fontsize=11,arrowsize=0.75];
'''
diagrams={
'handover_architecture':('Architecture — runtime, observation and separate research branch', '''digraph architecture {
rankdir=TB;
'''+common+'''
subgraph cluster_ops {label="OPERATIONAL WORKTREE\\nrnd_mrta_ilp_ws · traffic/rmf-integration"; color="#9bbad0";style="rounded";fontsize=16;
inputs [label="Inputs\nW100.xlsx · warehouse.yaml · orders.csv",fillcolor="#eef8f2",color="#91b6a0"];
gui [label="Dashboard · React / Vite\nOperations · History · Experiments"];
api [label="Local API · FastAPI\npreview · validate · supervise jobs"];
cli [label="Headless CLI / observed wrapper\nrun_workload · run_experiment"];
ros [label="ROS 2 entry point\nwarehouse_sim.launch.py"];
core [label="Shared models and allocation\nmrta_core + task_management\ndirected A* · energy mask · 8 strategies"];
engine [label="Scientific executor and engine\ntraffic · motion · service · charging\nsimulation_env",penwidth=2.2,fillcolor="#dcecf8"];
data [label="Scientific artifacts\ndecisions · outcomes · metrics · run.json",fillcolor="#eef8f2",color="#91b6a0"];
poses [label="Gazebo pose mirror\nvisualization; no second allocator",fillcolor="#f1f4f7"];
reports [label="Inspection and reports\npaired comparisons · graphs · offline HTML"];
{rank=same;gui;ros;}
inputs -> core;
gui -> api -> cli -> core -> engine;
ros -> core;
engine -> data -> reports;
data -> gui [label="read history",constraint=false];
engine -> poses [label="poses"];
engine -> api [label="sampled observation",style=dotted,constraint=false];
}
subgraph cluster_research {label="SEPARATE RESEARCH WORKTREE\\nrnd_mrta_ilp_research · research/outcome-aware-ilp";color="#c5b58f";style="rounded";fontsize=16;
m0 [label="M0 · committed\nidentity · failure kinds · seed purpose"];
m1 [label="M1 · local diagnostics\noutcome signals · features · rollouts",fillcolor="#fff7e6",color="#bba879"];
learn [label="M2 onward · planned\npredicates → Popper → rules → explanations",style="rounded,dashed,filled",fillcolor="#f5f5f5",color="#9ca8b1"];
m0 -> m1 -> learn;
}
reports -> m0 [style=invis];
m0 -> api [label="GUI compatibility work pending",style=dashed,color="#ac8b4a",constraint=false];
}
'''),
'handover_allocation_flow':('Flowchart — one decision, shared execution and evidence', '''digraph allocation {
rankdir=TB;
'''+common+'''
start [label="Decision trigger\nfree robot / dispatch quantum reached",shape=oval];
window [label="Offer FIFO candidate window\nW = fleet size by default"];
features [label="Score every robot–order pair\nC_static · estimated finish · energy requirement"];
mask [label="Any reachable and\nenergy-eligible pair?",shape=diamond,fillcolor="#f8f2e5"];
extend [label="Extend FIFO prefix when possible\notherwise defer / shared charging workflow"];
choose [label="Selected allocation strategy\nRandom · Manhattan · A* · Earliest Finish\nMulti Criteria · Auction · Hungarian · MILP"];
decision [label="Capture decision-time record\ncandidate features + selected flag\nBEFORE mission execution",fillcolor="#eef8f2",color="#91b6a0"];
check [label="Validate returned ownership\none order per robot; eligible pairs only"];
execute [label="Shared mission execution\nlegal route → traffic → shelf service\n→ dropoff → final retreat"];
outcome [label="Capture realized outcome at retirement\ntravel · waiting · service · energy\njoin to the original deciding decision",fillcolor="#eef8f2",color="#91b6a0"];
next [label="Still pending / active work?",shape=diamond,fillcolor="#f8f2e5"];
settle [label="Settle fleet / finish recovery\nwrite successful artifacts and metrics",shape=oval];
start -> window -> features -> mask;
mask -> extend [label="No"];extend -> window [label="later decision",style=dashed];
mask -> choose [label="Yes"];choose -> decision -> check -> execute -> outcome -> next;
next -> start [label="Yes",style=dashed];next -> settle [label="No"];
}
'''),
'handover_battery_flow':('Flowchart — fixed battery and charger lifecycle', '''digraph battery {
rankdir=TB;
'''+common+'''
available [label="AVAILABLE\nrobot physically clear of service terminals",shape=oval];
eligible [label="Order energy + charger leg\n+ reserve <= current SOC?",shape=diamond,fillcolor="#f8f2e5"];
busy [label="BUSY\nexecute assigned order and retreat"];
need [label="Charging due / needed\nSOC <= 10% or no feasible work\nbusy robot finishes current order"];
free [label="Free charger?",shape=diamond,fillcolor="#f8f2e5"];
reserve [label="Reserve charger immediately\ntravel to dedicated non-transit cell"];
charge [label="CHARGING\nnormal target 100% · +1% per simulated s",fillcolor="#eef8f2",color="#91b6a0"];
full [label="Full / preempted\nphysically leave charger first"];
rejoin [label="Energy-eligible to rejoin?",shape=diamond,fillcolor="#f8f2e5"];
hold [label="HOLDING / queue\nshared promotion discipline"];
preempt [label="Saturation policy\nmay preempt highest-SOC charger occupant"];
available -> eligible;eligible -> busy [label="Yes"];eligible -> need [label="No"];
busy -> available [label="still eligible"];busy -> need [label="due / needed"];
need -> free;free -> reserve [label="Yes"];reserve -> charge -> full -> rejoin;
free -> hold [label="No"];hold -> reserve [label="charger frees / promotion",style=dashed];
hold -> preempt [style=dotted];preempt -> full [label="preempt occupant",style=dotted];
rejoin -> available [label="Yes"];rejoin -> hold [label="No"];
}
'''),
'handover_research_flow':('Research pipeline — decision-time inputs and future outcomes stay separate', '''digraph research {
rankdir=TB;
'''+common+'''
subgraph cluster_now {label="AVAILABLE AT DECISION TIME · no future outcome features";style="rounded";color="#9bbad0";
state [label="State S_t\npositions · SOC · FIFO window\nissued plans and known configuration"];
feat [label="Candidate features\nlegal route · static time · energy mask\nM1 congestion sidecars are diagnostic"];
action [label="Teacher decision D_t\nselected eligible pair(s)"];
state -> feat -> action;
}
execution [label="Shared infrastructure executes\ntraffic waiting and charging emerge later"];
realized [label="Realized outcomes\nmission time · waiting · distance · energy",fillcolor="#eef8f2",color="#91b6a0"];
m1 [label="M1 diagnostics · partial\nresiduals · features · cloned alternatives\ncurrent feature-report blocker remains",fillcolor="#fff7e6",color="#bba879"];
m2 [label="M2 · planned data layer\nrun-scoped IDs · predicates · bias · sampling",style="rounded,dashed,filled",fillcolor="#f5f5f5"];
labels [label="Supervision design\nsingle teacher → multiple teachers\n→ outcome-evaluated labels / weights",style="rounded,dashed,filled",fillcolor="#f5f5f5"];
learn [label="M3–M6 · planned Popper learning\nlearn symbolic allocation rules",style="rounded,dashed,filled",fillcolor="#f5f5f5"];
eval [label="M7–M9 · planned\nrule explanations · held-out evaluation · report",style="rounded,dashed,filled",fillcolor="#f5f5f5"];
action -> execution -> realized -> m1;
feat -> m2 [style=dashed,label="future learner inputs"];
m1 -> labels [style=dashed,label="freeze valid supervision"];
m2 -> learn [style=dashed];labels -> learn [style=dashed];learn -> eval [style=dashed];
}
''')}
for name,(title,source) in diagrams.items():
 dot=OUT/(name+'.dot');dot.write_text(source)
 subprocess.run(['dot','-Tsvg',str(dot),'-o',str(OUT/(name+'.svg'))],check=True)
 # Add accessible metadata without changing Graphviz geometry.
 p=OUT/(name+'.svg');svg=p.read_text();svg=svg.replace('<svg ', '<svg role="img" aria-label="'+title.replace('&','&amp;')+'" ',1);p.write_text(svg)

def topic(title,children=None,note=None):
 obj={'id':hashlib.sha256((title+str(children)+str(note)).encode()).hexdigest()[:24],'class':'topic','title':title}
 if children:obj['children']={'attached':children}
 if note:obj['notes']={'plain':{'content':note}}
 return obj
T=topic
strategies=[
 T('random','' or None,'Canonical eligible-edge list; seeded shuffle; greedy matching. No uniform matching guarantee.'),
 T('nearest_manhattan',note='Greedy Manhattan shelf-chain ranking; A* route and battery mask still govern execution.'),
 T('nearest_astar',note='Greedy C_static; ties FIFO then robot_id.'),
 T('earliest_finish',note='Greedy T = C_static / speed + configured shelf/dropoff service. No future waiting.'),
 T('multi_criteria',note='0.5 N(T) + 0.2 N(FIFO) + 0.2 N(-energy margin) + 0.1 N(charger distance). Fixed heuristic weights.'),
 T('auction',note='FIFO sequential single-item auction; cheapest currently-free eligible robot bids C_static.'),
 T('hungarian',note='Maximum cardinality, then minimum summed C_static; SciPy linear_sum_assignment.'),
 T('milp',note='Maximum cardinality; minimize latest estimated finish, then summed finish; SciPy / HiGHS. Different from inductive logic programming.')]
sheets=[]
def sheet(title,children,note=None):
 root=topic(title,children,note);root['structureClass']='org.xmind.ui.map.unbalanced'
 sheets.append({'id':hashlib.sha256(title.encode()).hexdigest()[:24],'class':'sheet','title':title,'rootTopic':root})
sheet('System architecture',[
 T('Two Git worktrees',[
  T('GUI / operational workspace',note='/home/ibrahim/rnd_mrta_ilp_ws\ntraffic/rmf-integration\nAudited HEAD ae832f5\nFull dashboard, supported ROS/Gazebo demo, pre-M0 science.'),
  T('Research workspace',note='/home/ibrahim/rnd_mrta_ilp_research\nresearch/outcome-aware-ilp\nAudited HEAD 163c6a4\nM0 committed, local M1 diagnostics.'),
  T('Integration pending',note='M0 changes strategy identity, build_manifest, quantum/window/purpose, comparisons. GUI observer pin requires fresh parity.')]),
 T('Inputs',[
  T('W100.xlsx',note='src/simulation_env/maps/W100.xlsx\n40x54, 1m cells, 100 staging, 552 shelves, 36 dropoffs, 24 chargers, 12 holding.'),
  T('warehouse.yaml',note='src/simulation_env/config/warehouse.yaml\nDefault100 robots, 1.5m/s, q0, window=fleet_size, 1s service, battery fixed.'),
  T('orders.csv',note='src/simulation_env/config/orders.csv\n2,000 logical reference orders, generation seed42. Multiple line items per order.')]),
 T('Entry points',[
  T('Headless CLI',note='src/simulation_env/scripts/run_workload.py'),
  T('Campaign CLI',note='src/simulation_env/scripts/run_experiment.py'),
  T('ROS / Gazebo',note='src/simulation_env/launch/warehouse_sim.launch.py\norders:=true is required.'),
  T('Dashboard',note='src/dashboard_GUI/scripts/launch_control_center.py start\n127.0.0.1:5173 UI;127.0.0.1:8000 API')]),
 T('Scientific packages',[
  T('mrta_core',note='Map/config/order models, directed A*, battery energy requirement.'),
  T('task_management',note='features.py and allocation_strategy.py; eight conventional policies.'),
  T('simulation_env',note='workload_executor.py, fleet_engine.py; traffic/charging/recovery; datasets/metrics.'),
  T('robot_interfaces',note='ROS message definitions.'),
  T('gazebo_fleet_pose_transport',note='Persistent C++ batched pose mirror.'),
  T('rmf_grid_traffic',note='Static RMF graph/route checks and local demos; no native production fleet adapter.')]),
 T('Dashboard layers',[
  T('React frontend',note='Operations, Run history, Experiments, System, sampled recording playback.'),
  T('FastAPI backend',note='Adapters, preview/validation, process/job supervision, observation, storage, reports.'),
  T('Offline reports',note='Immutable HTML/PNG report folders; separate from dashboard screens.')])],
 'Audited28October2026. Implemented apparatus; symbolic learner pending. Source of truth: docs/PROJECT_HANDOVER.md.')
sheet('Allocation and execution',[
 T('Decision trigger',note='Free robots and configured dispatch quantum. q0 continuous; q3 primary research batching.'),
 T('FIFO candidate window',note='W=fleet_size by default; extend when no eligible pair exists in prefix.'),
 T('Shared candidate features',[
  T('Directed C_static',note='Exhaustive shelf permutations; legal entry/retreat; distance in metres.'),
  T('Estimated finish T',note='C_static/speed + shelf visits*service + dropoff service.'),
  T('Hard energy mask',note='Order travel + known service + directed charger leg + 10% reserve <= SOC; no future waiting.')]),
 T('Eight conventional strategies',strategies),
 T('Capture decision rows',note='One row per scored pair; selected flag; before execution.'),
 T('Check assignment contract',note='At most one robot per order and one order per robot; only eligible offered pairs.'),
 T('Execute shared mission',[
  T('Travel through one-way lanes'),T('Local traffic / reservations / rotation right-of-way'),
  T('Shelf service'),T('Dropoff fulfillment'),T('Final retreat; robot becomes available')]),
 T('Record outcome at retirement',note='Join run_id + original decision_id + robot_id + order_id. M0 fixes deferred-decision linkage.'),
 T('Recovery and final artifacts',note='Per-robot recovery can overlap other deliveries. Separate order_makespan from fleet settled time.')])
sheet('Battery, data and experiments',[
 T('Fixed Battery V1',[
  T('SOC rates',note='Initial100%; travel1%/20m;service1%/60s;idle/wait1%/300s;charge+1%/s.'),
  T('Charge due / needed',note='Due<=10%; active order finishes first; no assignment bypasses energy mask.'),
  T('Charger workflow',note='Reserve→travel→charge→physically leave→rejoin; holding/promotion/preemption fixed.'),
  T('Remaining scope',note='B6 rest incomplete; G6 partial recovery charging deferred.')]),
 T('Per-run artifacts',[
  T('run.json',note='Input hashes, effective settings, Git commit/dirty state, run identity.'),
  T('metrics.json',note='Schema2 separates order/recovery; legacy schema1 lacks order makespan.'),
  T('decisions + outcomes',note='Parquet in Pixi; CSV fallback in ROS without PyArrow.'),
  T('bk.pl / exs.pl',note='Raw teacher-choice facts; no learned rule or full multi-run learner dataset.'),
  T('failure.json',note='ResearchM0 classified execution failures; use fresh output directories.'),
  T('run.log / console.log',note='Campaign child run.log; standalone console.log only when redirected/teed.')]),
 T('Output roots',[
  T('CLI runs/',note='Ignored scientific output.'),
  T('GUI var/runs/singles/'),T('GUI var/runs/experiments/'),
  T('GUI var/jobs/',note='process.log, optional telemetry.ndjson and ownership/gate evidence.'),
  T('GUI var/reports/'),T('GUI jobs.sqlite / index.sqlite / app-index'),
  T('Colcon log/ and external ~/.ros/log/')]),
 T('Saved GUI pilot',note='50robots ×200orders ×8strategies ×seeds1–3=24/24; q0 pre-M0; no charging. A* mean ordermakespan393.22s; exploratory.'),
 T('Historical fleet sweep',note='30/40/50robots;3A*+3Manhattan+9Random=15;one reference workload;schema1/provenance limits.'),
 T('Seed roles and pairing',note='Training101–110;validation111–115;evaluation1–30;sealed final201–230. Shared workload perseed; strict grid/settings/identity checks. Holm/Wilcoxon pending.')])
sheet('ILP research and demo commands',[
 T('ILP versus MILP',note='Inductive logic programming learns rules. Mixed-integer linear programming optimizes assignments. They are different methods.'),
 T('M0 committed',note='Identity, classified failures, seed controls, deferred decision linkage.'),
 T('M1 partial diagnostics',note='Residual analysis, decision-time congestion sidecars, cloned alternatives. Current feature reporter has route_cells merge failure. Existing pilot predates fixes.'),
 T('M2 planned',note='Run-scoped atoms, learner predicates, bias, sampling, offline teacher relabeling.'),
 T('M3–M6 planned',[
  T('Reproduce Nearest A* with Popper'),T('Single-Teacher ILP'),T('Multi-Teacher ILP'),
  T('Outcome-Aware ILP',note='Freeze objective/labels first; evaluate alternatives; never label unexecuted candidates as observed bad outcomes.')]),
 T('M7–M9 planned',note='Rule explanations, held-out policy evaluation, final report; target20December2026.'),
 T('No leakage boundary',note='State_t→Decision_t→Outcome. Future waits/energy/charging may label training examples, never become decision-time input.'),
 T('Toolchain',note='Popperv4.4.0 commit860cd0de;SWI10.0.0;Clingo5.8.2;Janus1.5.3;Python3.12. One learner problem per process.'),
 T('Demo commands',[
  T('Headless',note='cd /home/ibrahim/rnd_mrta_ilp_ws\nexport PYTHONPATH=src/mrta_core:src/simulation_env:src/task_management\n.pixi/envs/default/bin/python src/simulation_env/scripts/run_workload.py --robots 6 --orders 12 --strategy nearest_astar --quiet --progress 0.'),
  T('Gazebo',note='source /opt/ros/jazzy/setup.bash\nsource install/setup.bash\nros2 launch simulation_env warehouse_sim.launch.py orders:=true robots:=10 order_limit:=50 strategy:=nearest_astar'),
  T('Dashboard',note='python3 src/dashboard_GUI/scripts/launch_control_center.py check\npython3 src/dashboard_GUI/scripts/launch_control_center.py start\nCtrl+C stops owned services.'),
  T('Full walkthrough',note='docs/PROJECT_HANDOVER.md sections4–8; original audit evidence and exact report paths included.')])])
# Ensure every topic id is unique, even if a title repeats across sheets.
def assign_ids(node,path):
 node['id']=hashlib.sha256(path.encode()).hexdigest()[:24]
 for i,child in enumerate(node.get('children',{}).get('attached',[])):assign_ids(child,path+'/'+str(i))
for i,s in enumerate(sheets):assign_ids(s['rootTopic'],str(i))
metadata={'creator':{'name':'MRTA handover documentation','version':'2026-10-28'}}
manifest={'file-entries':{'content.json':{},'metadata.json':{}}}
xmind=ROOT/'docs/PROJECT_HANDOVER.xmind'
with ZipFile(xmind,'w',ZIP_DEFLATED) as z:
 for n,obj in [('content.json',sheets),('metadata.json',metadata),('manifest.json',manifest)]:z.writestr(n,json.dumps(obj,ensure_ascii=False,indent=2))
outline=['# Handover XMind outline','', 'Editable native file: `docs/PROJECT_HANDOVER.xmind`. Four sheets reflect the audited architecture, flow, data and planned learning work.','']
def lines(node,depth):
 output=['  '*depth+'- '+node['title']]
 note=node.get('notes',{}).get('plain',{}).get('content')
 if note:output += ['  '*(depth+1)+note.replace('\n',' · ')]
 for child in node.get('children',{}).get('attached',[]):output+=lines(child,depth+1)
 return output
for s in sheets:outline+=['## '+s['title'],'']+lines(s['rootTopic'],0)+['']
(BASE/'XMIND_OUTLINE.md').write_text('\n'.join(outline)+'\n')
print('Generated',len(diagrams),'SVG/DOT diagrams and',len(sheets),'XMind sheets at',ROOT)
