#include <iostream>
#include <string>

#include <rmf_fleet_adapter/agv/parse_graph.hpp>

#include <rmf_traffic/Profile.hpp>
#include <rmf_traffic/agv/VehicleTraits.hpp>
#include <rmf_traffic/geometry/Circle.hpp>
#include <rmf_traffic/geometry/ConvexShape.hpp>


int main(int argc, char* argv[])
{
  if (argc != 2)
  {
    std::cerr
      << "Usage: graph_check <warehouse_nav_graph.yaml>\n";
    return 1;
  }

  const std::string graph_file = argv[1];

  // Robot geometry matches the warehouse sphere model.
  constexpr double robot_radius_m = 0.30;

  const auto footprint =
    rmf_traffic::geometry::make_final_convex<
      rmf_traffic::geometry::Circle>(
        robot_radius_m);

  const rmf_traffic::Profile profile(footprint);

  // Positive accelerations are required by RMF VehicleTraits.
  // These values are only needed to construct traits for graph parsing.
  // They are NOT yet part of our scientific motion model.
  rmf_traffic::agv::VehicleTraits traits(
    rmf_traffic::agv::VehicleTraits::Limits(
      1.5,   // linear velocity m/s
      1.0),  // temporary parser acceleration
    rmf_traffic::agv::VehicleTraits::Limits(
      1.0,   // angular velocity rad/s
      1.0),  // temporary parser angular acceleration
    profile);

  // Sphere robots do not require heading-constrained navigation.
  traits.set_holonomic(
    rmf_traffic::agv::VehicleTraits::Holonomic());

  try
  {
    const auto graph =
      rmf_fleet_adapter::agv::parse_graph(
        graph_file,
        traits);

    std::cout << "RMF GRAPH CHECK\n";
    std::cout << "============================\n";
    std::cout
      << "waypoints      : "
      << graph.num_waypoints()
      << "\n";

    std::cout
      << "directed lanes : "
      << graph.num_lanes()
      << "\n";

    std::cout
      << "named waypoints: "
      << graph.keys().size()
      << "\n";

    std::cout << "status         : OK\n";
  }
  catch (const std::exception& e)
  {
    std::cerr
      << "RMF graph parse failed: "
      << e.what()
      << "\n";

    return 2;
  }

  return 0;
}
