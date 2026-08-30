# msg/RobotStateArray.msg
#
# Authoritative fleet state topic: /mrta/robot_states
#
# Each RobotState carries its own robot_id, so consumers select the
# robot they care about. This stays the shape as the fleet grows.

RobotState[] robots
