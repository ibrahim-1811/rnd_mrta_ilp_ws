cmake_minimum_required(VERSION 3.8)
project(gazebo_fleet_pose_transport)

if(CMAKE_COMPILER_IS_GNUCXX OR CMAKE_CXX_COMPILER_ID MATCHES "Clang")
  add_compile_options(-Wall -Wextra -Wpedantic)
endif()

find_package(ament_cmake REQUIRED)
find_package(gz_msgs_vendor REQUIRED)
find_package(gz_transport_vendor REQUIRED)
find_package(gz-msgs10 REQUIRED)
find_package(gz-transport13 REQUIRED)

add_library(frame_parser STATIC
  src/frame_parser.cpp
)
target_compile_features(frame_parser PUBLIC cxx_std_17)
target_include_directories(frame_parser PUBLIC
  ${CMAKE_CURRENT_SOURCE_DIR}/src
)

add_executable(gazebo_fleet_pose_transport
  src/gazebo_fleet_pose_transport.cpp
)
target_compile_features(gazebo_fleet_pose_transport PRIVATE cxx_std_17)
target_link_libraries(gazebo_fleet_pose_transport PRIVATE
  frame_parser
  gz-msgs10::gz-msgs10
  gz-transport13::gz-transport13
)

install(TARGETS gazebo_fleet_pose_transport
  DESTINATION lib/${PROJECT_NAME}
)
install(FILES README.md
  DESTINATION share/${PROJECT_NAME}
)

if(BUILD_TESTING)
  find_package(ament_cmake_gtest REQUIRED)

  ament_add_gtest(test_frame_parser
    test/test_frame_parser.cpp
  )
  target_link_libraries(test_frame_parser frame_parser)
endif()

ament_package()
