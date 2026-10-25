# msg/FleetStatus.msg
#
# Fleet-wide status of a run: /mrta/fleet_status
#
# The counts a person watching a run wants at a glance, so the terminal
# can stay short. Reporting only: nothing reads this back into the
# simulation. sim_time is simulated time, never wall-clock time.

float64 sim_time
string strategy

# Orders. pending + assigned + active + completed + failed = total.
int32 orders_total
int32 orders_pending
int32 orders_assigned
int32 orders_active
int32 orders_completed
int32 orders_failed

# Robots.
int32 robots_total
int32 robots_busy
int32 robots_available
int32 robots_returning
int32 robots_parked
# Anywhere in the charging workflow: asked, driving, charging, waiting.
int32 robots_in_charging
# Of those: on a charger, SOC rising.
int32 robots_charging
# Of those: on a holding cell or a Stage Zone, waiting for a charger.
int32 robots_waiting_for_charger

# Battery, over the whole fleet, in percent.
float64 soc_min
float64 soc_mean

# Chargers and run counters.
int32 chargers_total
int32 chargers_free
int32 charge_sessions
int32 promotions
int32 saturation_events

# Who is charging, and who is waiting for a charger.
string[] charging_robots
string[] waiting_robots
