% Popper v4.4.0 bias syntax check (no spaces in head_pred/body_pred per gen2.py:51 regex)
head_pred(assign,3).
body_pred(high_load_regime,1).
body_pred(tight_soc_robot,2).
body_pred(best_cost_for_robot,3).
body_pred(best_cost_for_order,3).
body_pred(cheaper_for_robot,4).
body_pred(empty_unused_pred,3).

type(assign,(decision,robot,order)).
type(high_load_regime,(decision,)).
type(tight_soc_robot,(decision,robot)).
type(best_cost_for_robot,(decision,robot,order)).
type(best_cost_for_order,(decision,robot,order)).
type(cheaper_for_robot,(decision,robot,order,order)).
type(empty_unused_pred,(decision,robot,order)).

direction(assign,(in,in,in)).
direction(high_load_regime,(in,)).
direction(tight_soc_robot,(in,in)).
direction(best_cost_for_robot,(in,in,in)).
direction(best_cost_for_order,(in,in,in)).
direction(cheaper_for_robot,(in,in,in,out)).
direction(empty_unused_pred,(in,in,in)).

max_vars(4).
max_body(3).
max_clauses(2).

% Custom Clingo integrity constraint: every clause must contain at least one (D,R,O) relational literal
has_pair_rel(Rule):- body_literal(Rule,best_cost_for_robot,3,(0,1,2)).
has_pair_rel(Rule):- body_literal(Rule,best_cost_for_order,3,(0,1,2)).
has_pair_rel(Rule):- body_literal(Rule,cheaper_for_robot,4,(0,1,2,_)).
:- clause(Rule), not has_pair_rel(Rule).
