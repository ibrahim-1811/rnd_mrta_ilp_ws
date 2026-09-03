cmake_minimum_required(VERSION 3.8)
project(rmf_grid_traffic)

if(CMAKE_COMPILER_IS_GNUCXX OR CMAKE_CXX_COMPILER_ID MATCHES "Clang")
  add_compile_options(-Wall -Wextra -Wpedantic)
endif()

find_package(ament_cmake REQUIRED)
find_package(rmf_traffic REQUIRED)
find_package(rmf_fleet_adapter REQUIRED)

add_executable(graph_check
  src/graph_check.cpp
)

ament_target_dependencies(graph_check
  rmf_traffic
)

target_link_libraries(graph_check
  PRIVATE
  rmf_fleet_adapter::rmf_fleet_adapter
)

add_executable(route_check
  src/route_check.cpp
)

ament_target_dependencies(route_check
  rmf_traffic
)

target_link_libraries(route_check
  PRIVATE
  rmf_fleet_adapter::rmf_fleet_adapter
)

add_executable(two_robot_check
  src/two_robot_check.cpp
)

ament_target_dependencies(two_robot_check
  rmf_traffic
)

target_link_libraries(two_robot_check
  PRIVATE
  rmf_fleet_adapter::rmf_fleet_adapter
)

install(
  TARGETS
    graph_check
    route_check
    two_robot_check
  DESTINATION lib/${PROJECT_NAME}
)

install(
  DIRECTORY config
  DESTINATION share/${PROJECT_NAME}
)

install(
  PROGRAMS
    scripts/generate_nav_graph.py
    scripts/verify_nav_graph.py
  DESTINATION lib/${PROJECT_NAME}
)

ament_package()
