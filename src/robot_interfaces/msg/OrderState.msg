# msg/OrderState.msg
#
# Lifecycle state of one customer order, owned by shared_memory_node.
#
# Distinct from the legacy Order.msg, which is the int32-id / Pose
# based representation used by the random order generator.
#
# status is one of:
#   PENDING ASSIGNED IN_PROGRESS COMPLETED FAILED

string order_id

string dropoff_id

string status
string assigned_robot

# Line items, parallel arrays in CSV row order.
string[] item_ids
string[] shelf_ids
int32[] quantities

# Simulation times; -1.0 until the event happens.
float64 start_time
float64 completion_time
