# msg/RobotState.msg
#
# Deterministic execution state of one grid robot.
#
# sim_time is scientific simulation time owned by GridRobot. It is
# never wall-clock time and never derived from Gazebo pose feedback;
# Gazebo is visualization only.
#
# status is one of:
#   IDLE MOVING PICKING DROPPING CHARGING WAITING FAILED
#
# current_order is the order the robot is working on, empty when none.

string robot_id

float64 sim_time

int32 grid_x
int32 grid_y

float64 world_x
float64 world_y

string status

string current_target
string current_order

float64 battery_percent
float64 speed_mps
float64 distance_m

# Where the robot is in the charging workflow, empty when it is not in
# it: REQUEST TO_CHARGER CHARGING LEAVING_CHARGER TO_HOLDING HOLDING
# TO_STAGE_FALLBACK STAGE_FALLBACK ...
string charge_state
