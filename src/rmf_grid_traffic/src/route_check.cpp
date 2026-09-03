#include <chrono>
#include <cmath>
#include <iomanip>
#include <iostream>
#include <string>
#include <Eigen/Core>

#include <rmf_fleet_adapter/agv/parse_graph.hpp>

#include <rmf_traffic/Profile.hpp>
#include <rmf_traffic/agv/Planner.hpp>
#include <rmf_traffic/agv/LaneClosure.hpp>
#include <rmf_traffic/agv/VehicleTraits.hpp>
#include <rmf_traffic/geometry/Circle.hpp>
#include <rmf_traffic/geometry/ConvexShape.hpp>


namespace
{

bool is_service_name(const std::string& name)
{
  return
    name.rfind("shelf_", 0) == 0 ||
    name.rfind("dropoff_", 0) == 0;
}


// Close all lanes entering or leaving shelf/dropoff waypoints.
//
// The waypoints this leg actually uses as endpoints are reopened, so
// they can be entered and left again.
//
// Both ends matter. Reopening only the goal made every route that
// *starts* on a shelf unplannable: the robot was sealed onto the cell
// it was standing on and the planner reported no route, even though
// the warehouse rule explicitly allows a one-cell retreat.
//
// This preserves the warehouse rule:
//
//   shelf/dropoff = service endpoint only
//   shelf/dropoff != transit cell
//
rmf_traffic::agv::LaneClosure service_lane_closures(
  const rmf_traffic::agv::Graph& graph,
  std::size_t start_waypoint,
  std::size_t goal_waypoint)
{
  rmf_traffic::agv::LaneClosure closures;

  // First close every lane incident to every service waypoint.
  for (const auto& [name, waypoint_index] : graph.keys())
  {
    if (!is_service_name(name))
      continue;

    for (const auto lane_index : graph.lanes_from(waypoint_index))
      closures.close(lane_index);

    for (const auto lane_index : graph.lanes_into(waypoint_index))
      closures.close(lane_index);
  }

  // Then reopen just this leg's own endpoints.
  for (const auto waypoint : {start_waypoint, goal_waypoint})
  {
    for (const auto lane_index : graph.lanes_from(waypoint))
      closures.open(lane_index);

    for (const auto lane_index : graph.lanes_into(waypoint))
      closures.open(lane_index);
  }

  return closures;
}

} // namespace


int main(int argc, char* argv[])
{
  if (argc != 4)
  {
    std::cerr
      << "Usage: route_check <graph.yaml> <start_name> <goal_name>\n";

    return 1;
  }

  const std::string graph_file = argv[1];
  const std::string start_name = argv[2];
  const std::string goal_name = argv[3];

  constexpr double robot_radius_m = 0.30;
  constexpr double speed_mps = 1.5;

  const auto footprint =
    rmf_traffic::geometry::make_final_convex<
      rmf_traffic::geometry::Circle>(
        robot_radius_m);

  const rmf_traffic::Profile profile(footprint);

  rmf_traffic::agv::VehicleTraits traits(
    rmf_traffic::agv::VehicleTraits::Limits(
      speed_mps,
      1.0),
    rmf_traffic::agv::VehicleTraits::Limits(
      1.0,
      1.0),
    profile);
  auto graph =
    rmf_fleet_adapter::agv::parse_graph(
      graph_file,
      traits);

  const auto* start_wp =
    graph.find_waypoint(start_name);

  const auto* goal_wp =
    graph.find_waypoint(goal_name);

  if (!start_wp)
  {
    std::cerr
      << "Unknown start waypoint: "
      << start_name
      << "\n";

    return 2;
  }

  if (!goal_wp)
  {
    std::cerr
      << "Unknown goal waypoint: "
      << goal_name
      << "\n";

    return 3;
  }

  auto planner_config =
    rmf_traffic::agv::Planner::Configuration(
      graph,
      traits);

  planner_config.lane_closures(
    service_lane_closures(
      graph,
      start_wp->index(),
      goal_wp->index()));

  rmf_traffic::agv::Planner planner(
    std::move(planner_config),
    rmf_traffic::agv::Planner::Options(nullptr));

  rmf_traffic::agv::Planner::StartSet starts;

  // Time and orientation are ignored by quickest_path().
  starts.emplace_back(
    rmf_traffic::Time{},
    start_wp->index(),
    0.0);

  const auto wall_start =
    std::chrono::steady_clock::now();

  const auto result =
    planner.quickest_path(
      starts,
      goal_wp->index());

  const auto wall_end =
    std::chrono::steady_clock::now();

  const double wall_ms =
    std::chrono::duration<double, std::milli>(
      wall_end - wall_start).count();

  if (!result)
  {
    std::cerr
      << "No RMF route found from "
      << start_name
      << " to "
      << goal_name
      << "\n";

    return 4;
  }

  const auto& path = result->path();

  double distance_m = 0.0;

  for (std::size_t i = 1; i < path.size(); ++i)
  {
    const auto& a =
      graph.get_waypoint(path[i - 1]).get_location();

    const auto& b =
      graph.get_waypoint(path[i]).get_location();

    distance_m += (b - a).norm();
  }

  std::cout << "\nRMF STATIC ROUTE CHECK\n";
  std::cout << "========================================\n";

  std::cout
    << "start           : "
    << start_name
    << "\n";

  std::cout
    << "goal            : "
    << goal_name
    << "\n";

  std::cout
    << "path waypoints  : "
    << path.size()
    << "\n";

  std::cout
    << "moves           : "
    << (path.empty() ? 0 : path.size() - 1)
    << "\n";

  std::cout
    << std::fixed
    << std::setprecision(3);

  std::cout
    << "distance        : "
    << distance_m
    << " m\n";

  std::cout
    << "distance/speed  : "
    << distance_m / speed_mps
    << " s\n";

  std::cout
    << "RMF cost        : "
    << result->cost()
    << "\n";

  std::cout
    << "planning wall   : "
    << wall_ms
    << " ms\n";

  std::cout << "\nPATH\n";
  std::cout << "----------------------------------------\n";

  for (std::size_t i = 0; i < path.size(); ++i)
  {
    const auto& wp =
      graph.get_waypoint(path[i]);

    const auto& p =
      wp.get_location();

    std::cout
      << std::setw(3)
      << i
      << "  index="
      << std::setw(3)
      << path[i]
      << "  name="
      << wp.name_or_index()
      << "  xy=("
      << p.x()
      << ", "
      << p.y()
      << ")\n";
  }

  std::cout << "\nstatus          : OK\n";

  return 0;
}
