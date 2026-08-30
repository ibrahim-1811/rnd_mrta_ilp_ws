// Copyright 2026 Mohammad Ibrahim Memon
//
// Licensed under the Apache License, Version 2.0 (the "License");
// you may not use this file except in compliance with the License.
// You may obtain a copy of the License at
//
//     http://www.apache.org/licenses/LICENSE-2.0
//
// Unless required by applicable law or agreed to in writing, software
// distributed under the License is distributed on an "AS IS" BASIS,
// WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
// See the License for the specific language governing permissions and
// limitations under the License.

#ifndef GAZEBO_FLEET_POSE_TRANSPORT__FRAME_PARSER_HPP_
#define GAZEBO_FLEET_POSE_TRANSPORT__FRAME_PARSER_HPP_

#include <cstddef>
#include <string>
#include <vector>

namespace gazebo_fleet_pose_transport
{

constexpr std::size_t kMaximumFrameLineBytes = 1024U * 1024U;
constexpr std::size_t kMaximumPosesPerFrame = 10000U;

struct Pose
{
  std::string name;
  double x{0.0};
  double y{0.0};
  double z{0.0};
  double qx{0.0};
  double qy{0.0};
  double qz{0.0};
  double qw{1.0};
};

struct Frame
{
  std::string id;
  std::vector<Pose> poses;
};

enum class LineKind
{
  kIgnored,
  kFrame,
  kQuit,
  kError,
};

struct ParseResult
{
  LineKind kind{LineKind::kError};
  Frame frame;
  std::string error;
};

/// Parse one complete line from the helper's stdin protocol.
ParseResult ParseLine(const std::string & line);

}  // namespace gazebo_fleet_pose_transport

#endif  // GAZEBO_FLEET_POSE_TRANSPORT__FRAME_PARSER_HPP_
