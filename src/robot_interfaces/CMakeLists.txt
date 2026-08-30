cmake_minimum_required(VERSION 3.8)
project(robot_interfaces)

if(CMAKE_COMPILER_IS_GNUCXX OR CMAKE_CXX_COMPILER_ID MATCHES "Clang")
  add_compile_options(-Wall -Wextra -Wpedantic)
endif()

find_package(ament_cmake REQUIRED)
find_package(geometry_msgs REQUIRED)
find_package(rosidl_default_generators REQUIRED)

rosidl_generate_interfaces(${PROJECT_NAME}
  "msg/Order.msg"
  "msg/Product.msg"
  "msg/Task.msg"
  "msg/Logs.msg"
  "msg/GoalStartMsg.msg"
  "msg/GoalReachMsg.msg"
  "msg/RobotStatus.msg"
  "msg/RobotState.msg"
  "msg/RobotStateArray.msg"
  "msg/OrderState.msg"
  "msg/OrderStateArray.msg"
  "msg/FleetStatus.msg"
  "msg/ShelfStatus.msg"
  "srv/GetRobotFleetStatus.srv"
  "srv/GetShelfList.srv"
  "srv/GetRobotStatus.srv"
  "srv/ShelfQuery.srv"
  "srv/InventoryUpdate.srv"
  "srv/TaskList.srv"
  "srv/GetPose.srv"
  "srv/GetOrderStates.srv"
  "srv/GetRobotStates.srv"
  "srv/UpdateOrderState.srv"
  "action/TaskList.action"
  "action/ProcessOrder.action"
  DEPENDENCIES geometry_msgs
)

if(BUILD_TESTING)
  find_package(ament_lint_auto REQUIRED)
  set(ament_cmake_copyright_FOUND TRUE)
  set(ament_cmake_cpplint_FOUND TRUE)
  ament_lint_auto_find_test_dependencies()
endif()

ament_package()
