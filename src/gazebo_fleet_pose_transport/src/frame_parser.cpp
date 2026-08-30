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

#include "frame_parser.hpp"

#include <charconv>
#include <cmath>
#include <locale>
#include <sstream>
#include <string>
#include <system_error>
#include <unordered_set>
#include <utility>
#include <vector>

namespace gazebo_fleet_pose_transport
{
namespace
{

constexpr std::size_t kPoseFieldCount = 8U;
constexpr std::size_t kMaximumIdentifierBytes = 128U;
constexpr std::size_t kMaximumEntityNameBytes = 256U;
constexpr double kMinimumQuaternionNormSquared = 1.0e-24;

ParseResult Error(std::string message)
{
  ParseResult result;
  result.kind = LineKind::kError;
  result.error = std::move(message);
  return result;
}

bool ParseCount(const std::string & token, std::size_t & value)
{
  if (token.empty()) {
    return false;
  }

  std::size_t parsed = 0U;
  const char * const begin = token.data();
  const char * const end = begin + token.size();
  const auto conversion = std::from_chars(begin, end, parsed);
  if (conversion.ec != std::errc{} || conversion.ptr != end) {
    return false;
  }

  value = parsed;
  return true;
}

bool ParseFiniteDouble(const std::string & token, double & value)
{
  std::istringstream input(token);
  input.imbue(std::locale::classic());

  double parsed = 0.0;
  char trailing = '\0';
  if (!(input >> parsed) || (input >> trailing) || !std::isfinite(parsed)) {
    return false;
  }

  value = parsed;
  return true;
}

std::vector<std::string> Tokens(const std::string & line)
{
  std::istringstream input(line);
  input.imbue(std::locale::classic());

  std::vector<std::string> tokens;
  std::string token;
  while (input >> token) {
    tokens.push_back(std::move(token));
  }
  return tokens;
}

}  // namespace

ParseResult ParseLine(const std::string & line)
{
  if (line.size() > kMaximumFrameLineBytes) {
    return Error("line exceeds the 1 MiB protocol limit");
  }

  const auto first = line.find_first_not_of(" \t\r\n");
  if (first == std::string::npos || line[first] == '#') {
    ParseResult result;
    result.kind = LineKind::kIgnored;
    return result;
  }

  const std::vector<std::string> tokens = Tokens(line);
  if (tokens.empty()) {
    ParseResult result;
    result.kind = LineKind::kIgnored;
    return result;
  }

  if (tokens.front() == "QUIT") {
    if (tokens.size() != 1U) {
      return Error("QUIT does not accept arguments");
    }
    ParseResult result;
    result.kind = LineKind::kQuit;
    return result;
  }

  if (tokens.front() != "FRAME") {
    return Error("expected FRAME or QUIT");
  }
  if (tokens.size() < 3U) {
    return Error("FRAME requires an id and pose count");
  }
  if (tokens[1].size() > kMaximumIdentifierBytes) {
    return Error("frame id exceeds 128 bytes");
  }

  std::size_t pose_count = 0U;
  if (!ParseCount(tokens[2], pose_count)) {
    return Error("pose count must be a non-negative decimal integer");
  }
  if (pose_count == 0U) {
    return Error("a FRAME must contain at least one pose");
  }
  if (pose_count > kMaximumPosesPerFrame) {
    return Error("pose count exceeds the 10000-pose protocol limit");
  }

  const std::size_t expected_tokens = 3U + pose_count * kPoseFieldCount;
  if (tokens.size() != expected_tokens) {
    std::ostringstream message;
    message << "pose count says " << pose_count << " but the line contains " <<
      (tokens.size() > 3U ? (tokens.size() - 3U) / kPoseFieldCount : 0U) <<
      " complete pose records";
    return Error(message.str());
  }

  ParseResult result;
  result.kind = LineKind::kFrame;
  result.frame.id = tokens[1];
  result.frame.poses.reserve(pose_count);

  std::unordered_set<std::string> entity_names;
  entity_names.reserve(pose_count);

  for (std::size_t pose_index = 0U; pose_index < pose_count; ++pose_index) {
    const std::size_t offset = 3U + pose_index * kPoseFieldCount;
    Pose pose;
    pose.name = tokens[offset];

    if (pose.name.size() > kMaximumEntityNameBytes) {
      return Error("entity name exceeds 256 bytes");
    }
    if (!entity_names.insert(pose.name).second) {
      return Error("duplicate entity name in one FRAME: " + pose.name);
    }

    double * const fields[] = {
      &pose.x, &pose.y, &pose.z, &pose.qx, &pose.qy, &pose.qz, &pose.qw,
    };
    for (std::size_t field_index = 0U; field_index < 7U; ++field_index) {
      if (!ParseFiniteDouble(tokens[offset + 1U + field_index], *fields[field_index])) {
        std::ostringstream message;
        message << "pose " << pose_index << " has an invalid or non-finite numeric field";
        return Error(message.str());
      }
    }

    const double quaternion_norm_squared =
      pose.qx * pose.qx + pose.qy * pose.qy + pose.qz * pose.qz + pose.qw * pose.qw;
    if (!std::isfinite(quaternion_norm_squared) ||
      quaternion_norm_squared < kMinimumQuaternionNormSquared)
    {
      std::ostringstream message;
      message << "pose " << pose_index << " has a zero or invalid quaternion";
      return Error(message.str());
    }

    result.frame.poses.push_back(std::move(pose));
  }

  return result;
}

}  // namespace gazebo_fleet_pose_transport
