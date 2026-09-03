#include <chrono>
#include <iomanip>
#include <iostream>
#include <memory>
#include <string>

#include <rmf_fleet_adapter/agv/parse_graph.hpp>

#include <rmf_traffic/Profile.hpp>
#include <rmf_traffic/agv/LaneClosure.hpp>
#include <rmf_traffic/agv/Planner.hpp>
#include <rmf_traffic/agv/RouteValidator.hpp>
#include <rmf_traffic/agv/VehicleTraits.hpp>
#include <rmf_traffic/geometry/Circle.hpp>
#include <rmf_traffic/geometry/ConvexShape.hpp>
#include <rmf_traffic/schedule/Database.hpp>
#include <rmf_traffic/schedule/Participant.hpp>
#include <rmf_traffic/schedule/ParticipantDescription.hpp>


namespace
{

bool is_service_name(const std::string& name)
{
  return
    name.rfind("shelf_", 0) == 0 ||
    name.rfind("dropoff_", 0) == 0;
}


void configure_waiting_points(
  rmf_traffic::agv::Graph& graph)
{
  // This matches our old MAPF model:
  // robots may wait on ordinary traversable cells.
  //
  // Shelf/dropoff cells remain service endpoints only.
  for (std::size_t i = 0; i < graph.num_waypoints(); ++i)
  {
    auto& waypoint = graph.get_waypoint(i);

    if (!is_service_name(waypoint.name_or_index()))
      waypoint.set_holding_point(true);
  }
}


rmf_traffic::agv::LaneClosure close_service_lanes(
  const rmf_traffic::agv::Graph& graph)
{
  rmf_traffic::agv::LaneClosure closures;

  for (const auto& [name, waypoint_index] : graph.keys())
  {
    if (!is_service_name(name))
      continue;

    for (const auto lane : graph.lanes_from(waypoint_index))
      closures.close(lane);

    for (const auto lane : graph.lanes_into(waypoint_index))
      closures.close(lane);
  }

  return closures;
}


double seconds_from(
  rmf_traffic::Time origin,
  rmf_traffic::Time time)
{
  return std::chrono::duration<double>(
    time - origin).count();
}


void print_plan(
  const std::string& robot,
  const rmf_traffic::agv::Plan& plan,
  const rmf_traffic::agv::Graph& graph,
  const rmf_traffic::Time origin)
{
  const auto& waypoints = plan.get_waypoints();

  std::cout
    << "\n"
    << robot
    << " PLAN\n"
    << "----------------------------------------------\n";

  for (std::size_t i = 0; i < waypoints.size(); ++i)
  {
    const auto& waypoint = waypoints[i];

    std::string graph_name = "<off-graph>";

    if (waypoint.graph_index())
    {
      graph_name =
        graph.get_waypoint(
          *waypoint.graph_index()).name_or_index();
    }

    const auto& p = waypoint.position();

    std::cout
      << std::setw(3) << i
      << "  t="
      << std::fixed
      << std::setprecision(3)
      << std::setw(8)
      << seconds_from(origin, waypoint.time())
      << " s"
      << "  " << std::setw(14) << graph_name
      << "  xy=("
      << p.x()
      << ", "
      << p.y()
      << ")\n";
  }

  if (!waypoints.empty())
  {
    const double duration =
      seconds_from(
        waypoints.front().time(),
        waypoints.back().time());

    std::cout
      << "duration: "
      << duration
      << " s\n";
  }
}


bool has_schedule_conflict(
  const rmf_traffic::schedule::Participant& participant,
  const rmf_traffic::schedule::Database& database,
  const rmf_traffic::Profile& profile)
{
  auto validator =
    rmf_traffic::agv::ScheduleRouteValidator::make(
      database,
      participant.id(),
      profile);

  for (const auto& route : participant.itinerary())
  {
    if (validator->find_conflict(route))
      return true;
  }

  return false;
}

} // namespace


int main(int argc, char* argv[])
{
  if (argc != 6)
  {
    std::cerr
      << "Usage:\n"
      << "  two_robot_check <graph.yaml> "
      << "<A_start> <A_goal> "
      << "<B_start> <B_goal>\n";

    return 1;
  }

  const std::string graph_file = argv[1];
  const std::string a_start_name = argv[2];
  const std::string a_goal_name = argv[3];
  const std::string b_start_name = argv[4];
  const std::string b_goal_name = argv[5];

  constexpr double robot_radius_m = 0.30;
  constexpr double speed_mps = 1.5;
  constexpr double pi = 3.14159265358979323846;

  const auto footprint =
    rmf_traffic::geometry::make_final_convex<
      rmf_traffic::geometry::Circle>(
        robot_radius_m);

  const rmf_traffic::Profile profile(footprint);

  // RMF full planning requires positive acceleration limits.
  // These are temporary traffic-test values.
  //
  // They do NOT replace the project's scientific constant-speed
  // execution model.
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

  configure_waiting_points(graph);

  const auto* a_start = graph.find_waypoint(a_start_name);
  const auto* a_goal = graph.find_waypoint(a_goal_name);
  const auto* b_start = graph.find_waypoint(b_start_name);
  const auto* b_goal = graph.find_waypoint(b_goal_name);

  if (!a_start || !a_goal || !b_start || !b_goal)
  {
    std::cerr << "One or more waypoint names are invalid.\n";
    return 2;
  }

  auto planner_config =
    rmf_traffic::agv::Planner::Configuration(
      graph,
      traits);

  planner_config.lane_closures(
    close_service_lanes(graph));

  rmf_traffic::agv::Planner planner(
    std::move(planner_config),
    rmf_traffic::agv::Planner::Options(nullptr));

  // In-process traffic schedule.
  auto database =
    std::make_shared<
      rmf_traffic::schedule::Database>();

  using Description =
    rmf_traffic::schedule::ParticipantDescription;

  auto robot_a =
    rmf_traffic::schedule::make_participant(
      Description(
        "robot_A",
        "rnd_fleet",
        Description::Rx::Responsive,
        profile),
      database);

  auto robot_b =
    rmf_traffic::schedule::make_participant(
      Description(
        "robot_B",
        "rnd_fleet",
        Description::Rx::Responsive,
        profile),
      database);

  const rmf_traffic::Time t0{};

  std::cout
    << "\nRMF TWO-ROBOT TRAFFIC CHECK\n"
    << "==============================================\n"
    << "A: "
    << a_start_name
    << " -> "
    << a_goal_name
    << "\n"
    << "B: "
    << b_start_name
    << " -> "
    << b_goal_name
    << "\n";

  // ----------------------------------------------------------
  // Robot A
  // ----------------------------------------------------------

  const auto a_wall_start =
    std::chrono::steady_clock::now();

  const auto a_result =
    planner.plan(
      rmf_traffic::agv::Planner::Start(
        t0,
        a_start->index(),
        0.0),
      rmf_traffic::agv::Planner::Goal(
        a_goal->index()));

  const auto a_wall_end =
    std::chrono::steady_clock::now();

  if (!a_result.success())
  {
    std::cerr << "Robot A planning failed.\n";
    return 3;
  }

  const double a_wall_ms =
    std::chrono::duration<double, std::milli>(
      a_wall_end - a_wall_start).count();

  const auto a_plan_id =
    robot_a.assign_plan_id();

  if (!robot_a.set(
      a_plan_id,
      a_result->get_itinerary()))
  {
    std::cerr
      << "Failed to write Robot A itinerary "
      << "to RMF schedule.\n";

    return 4;
  }

  // ----------------------------------------------------------
  // Robot B
  //
  // Robot A is now already inside the schedule.
  // B must therefore plan around A.
  // ----------------------------------------------------------

  auto b_validator =
    rmf_traffic::agv::ScheduleRouteValidator::make(
      *database,
      robot_b.id(),
      profile);

  rmf_traffic::agv::Planner::Options b_options(
    std::move(b_validator));

  const auto b_wall_start =
    std::chrono::steady_clock::now();

  const auto b_result =
    planner.plan(
      rmf_traffic::agv::Planner::Start(
        t0,
        b_start->index(),
        pi),
      rmf_traffic::agv::Planner::Goal(
        b_goal->index()),
      std::move(b_options));

  const auto b_wall_end =
    std::chrono::steady_clock::now();

  if (!b_result.success())
  {
    std::cerr
      << "Robot B planning failed.\n"
      << "blocked participants: ";

    for (const auto blocker : b_result.blockers())
      std::cerr << blocker << " ";

    std::cerr << "\n";

    return 5;
  }

  const double b_wall_ms =
    std::chrono::duration<double, std::milli>(
      b_wall_end - b_wall_start).count();

  const auto b_plan_id =
    robot_b.assign_plan_id();

  if (!robot_b.set(
      b_plan_id,
      b_result->get_itinerary()))
  {
    std::cerr
      << "Failed to write Robot B itinerary "
      << "to RMF schedule.\n";

    return 6;
  }

  // ----------------------------------------------------------
  // Independent final conflict validation.
  // ----------------------------------------------------------

  const bool a_conflict =
    has_schedule_conflict(
      robot_a,
      *database,
      profile);

  const bool b_conflict =
    has_schedule_conflict(
      robot_b,
      *database,
      profile);

  print_plan(
    "ROBOT A",
    *a_result,
    graph,
    t0);

  print_plan(
    "ROBOT B",
    *b_result,
    graph,
    t0);

  std::cout
    << "\nPLANNING PERFORMANCE\n"
    << "----------------------------------------------\n"
    << std::fixed
    << std::setprecision(3)
    << "Robot A planning : "
    << a_wall_ms
    << " ms\n"
    << "Robot B planning : "
    << b_wall_ms
    << " ms\n";

  std::cout
    << "\nFINAL SCHEDULE VALIDATION\n"
    << "----------------------------------------------\n"
    << "Robot A conflict : "
    << (a_conflict ? "YES" : "NO")
    << "\n"
    << "Robot B conflict : "
    << (b_conflict ? "YES" : "NO")
    << "\n";

  if (a_conflict || b_conflict)
  {
    std::cout
      << "\nstatus           : FAILED\n";

    return 7;
  }

  std::cout
    << "\nstatus           : OK\n";

  return 0;
}
