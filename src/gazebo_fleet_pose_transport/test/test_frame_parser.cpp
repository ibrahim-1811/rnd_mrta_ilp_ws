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

#include <gtest/gtest.h>

#include <string>

namespace gazebo_fleet_pose_transport
{
namespace
{

TEST(FrameParser, ParsesCompleteFleetFrame)
{
  const ParseResult result = ParseLine(
    "FRAME 42 2 "
    "robot_001 1.25 -2 0.155 0 0 0 1 "
    "robot_002 3 4.5 0.155 0 0 0 1");

  ASSERT_EQ(LineKind::kFrame, result.kind);
  EXPECT_EQ("42", result.frame.id);
  ASSERT_EQ(2U, result.frame.poses.size());
  EXPECT_EQ("robot_001", result.frame.poses[0].name);
  EXPECT_DOUBLE_EQ(1.25, result.frame.poses[0].x);
  EXPECT_DOUBLE_EQ(-2.0, result.frame.poses[0].y);
  EXPECT_EQ("robot_002", result.frame.poses[1].name);
  EXPECT_DOUBLE_EQ(4.5, result.frame.poses[1].y);
}

TEST(FrameParser, IgnoresWhitespaceAndCommentLines)
{
  EXPECT_EQ(LineKind::kIgnored, ParseLine("  \t ").kind);
  EXPECT_EQ(LineKind::kIgnored, ParseLine("  # a comment").kind);
}

TEST(FrameParser, ParsesQuit)
{
  EXPECT_EQ(LineKind::kQuit, ParseLine("QUIT").kind);
  EXPECT_EQ(LineKind::kError, ParseLine("QUIT now").kind);
}

TEST(FrameParser, RejectsCountMismatch)
{
  const ParseResult result = ParseLine(
    "FRAME 7 2 robot_001 1 2 3 0 0 0 1");
  EXPECT_EQ(LineKind::kError, result.kind);
  EXPECT_NE(std::string::npos, result.error.find("pose count"));
}

TEST(FrameParser, RejectsMalformedAndNonFiniteNumbers)
{
  EXPECT_EQ(
    LineKind::kError,
    ParseLine("FRAME 7 1 robot_001 1x 2 3 0 0 0 1").kind);
  EXPECT_EQ(
    LineKind::kError,
    ParseLine("FRAME 7 1 robot_001 1 2 3 0 0 0 inf").kind);
}

TEST(FrameParser, RejectsZeroQuaternion)
{
  const ParseResult result = ParseLine(
    "FRAME 7 1 robot_001 1 2 3 0 0 0 0");
  EXPECT_EQ(LineKind::kError, result.kind);
  EXPECT_NE(std::string::npos, result.error.find("quaternion"));
}

TEST(FrameParser, RejectsDuplicateEntityNames)
{
  const ParseResult result = ParseLine(
    "FRAME 7 2 "
    "robot_001 1 2 3 0 0 0 1 "
    "robot_001 2 3 4 0 0 0 1");
  EXPECT_EQ(LineKind::kError, result.kind);
  EXPECT_NE(std::string::npos, result.error.find("duplicate"));
}

TEST(FrameParser, RejectsEmptyAndExcessiveFrames)
{
  EXPECT_EQ(LineKind::kError, ParseLine("FRAME 1 0").kind);
  EXPECT_EQ(LineKind::kError, ParseLine("FRAME 1 10001").kind);
}

}  // namespace
}  // namespace gazebo_fleet_pose_transport
