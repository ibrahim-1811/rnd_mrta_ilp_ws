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

#include <gz/msgs/boolean.pb.h>
#include <gz/msgs/pose_v.pb.h>
#include <gz/transport/Node.hh>

#include <algorithm>
#include <atomic>
#include <charconv>
#include <chrono>
#include <condition_variable>
#include <cstddef>
#include <cstdlib>
#include <functional>
#include <iostream>
#include <memory>
#include <mutex>
#include <string>
#include <system_error>

namespace gazebo_fleet_pose_transport
{
namespace
{

constexpr std::size_t kDefaultMaximumPendingRequests = 256U;
constexpr std::size_t kDefaultDrainTimeoutMs = 2000U;

struct Options
{
  std::string world;
  bool acknowledgements{false};
  std::size_t maximum_pending_requests{kDefaultMaximumPendingRequests};
  std::size_t drain_timeout_ms{kDefaultDrainTimeoutMs};
};

void Usage(std::ostream & output, const char * executable)
{
  output << "Usage: " << executable << " --world WORLD [OPTIONS]\n"
         << "\n"
         << "Read complete fleet pose frames from stdin and asynchronously send each\n"
         << "frame as gz.msgs.Pose_V to /world/WORLD/set_pose_vector.\n"
         << "\n"
         << "Options:\n"
         << "  --ack                    Print READY and asynchronous ACK/NACK lines.\n"
         << "  --max-pending COUNT      Reject frames above this pending-request bound\n"
         << "                           (default: 256).\n"
         << "  --drain-timeout-ms MS    Maximum wait at EOF or QUIT (default: 2000).\n"
         << "  -h, --help               Show this help.\n";
}

bool ParseSize(const std::string & token, std::size_t & value)
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

bool ValidWorldName(const std::string & world)
{
  if (world.empty() || world.size() > 255U || world == "." || world == "..") {
    return false;
  }

  return std::all_of(
    world.begin(), world.end(),
    [](const unsigned char character) {
      return (character >= 'a' && character <= 'z') ||
             (character >= 'A' && character <= 'Z') ||
             (character >= '0' && character <= '9') ||
             character == '_' || character == '-' || character == '.';
    });
}

enum class OptionParseResult
{
  kRun,
  kHelp,
  kError,
};

OptionParseResult ParseOptions(
  const int argc, char ** argv, Options & options, std::string & error)
{
  for (int index = 1; index < argc; ++index) {
    const std::string argument(argv[index]);
    if (argument == "-h" || argument == "--help") {
      return OptionParseResult::kHelp;
    }
    if (argument == "--ack") {
      options.acknowledgements = true;
      continue;
    }
    if (argument == "--world") {
      if (++index >= argc) {
        error = "--world requires a value";
        return OptionParseResult::kError;
      }
      options.world = argv[index];
      continue;
    }
    if (argument == "--max-pending") {
      if (++index >= argc ||
        !ParseSize(argv[index], options.maximum_pending_requests) ||
        options.maximum_pending_requests == 0U)
      {
        error = "--max-pending requires a positive decimal integer";
        return OptionParseResult::kError;
      }
      continue;
    }
    if (argument == "--drain-timeout-ms") {
      if (++index >= argc || !ParseSize(argv[index], options.drain_timeout_ms)) {
        error = "--drain-timeout-ms requires a non-negative decimal integer";
        return OptionParseResult::kError;
      }
      continue;
    }

    error = "unknown argument: " + argument;
    return OptionParseResult::kError;
  }

  if (!ValidWorldName(options.world)) {
    error = "--world must contain only letters, digits, '.', '_' or '-'";
    return OptionParseResult::kError;
  }
  return OptionParseResult::kRun;
}

class Reporter
{
public:
  explicit Reporter(const bool acknowledgements)
  : acknowledgements_(acknowledgements)
  {
  }

  void Ready(const std::string & service)
  {
    if (!acknowledgements_) {
      return;
    }
    std::lock_guard<std::mutex> lock(mutex_);
    std::cout << "READY " << service << std::endl;
  }

  void Acknowledge(const std::string & frame_id)
  {
    if (!acknowledgements_) {
      return;
    }
    std::lock_guard<std::mutex> lock(mutex_);
    std::cout << "ACK " << frame_id << std::endl;
  }

  void Reject(const std::string & frame_id, const std::string & reason)
  {
    std::lock_guard<std::mutex> lock(mutex_);
    if (acknowledgements_) {
      std::cout << "NACK " << frame_id << ' ' << reason << std::endl;
    }
    std::cerr << "gazebo_fleet_pose_transport: frame " << frame_id << ": " <<
      reason << std::endl;
  }

  void ProtocolError(const std::size_t line_number, const std::string & reason)
  {
    std::lock_guard<std::mutex> lock(mutex_);
    if (acknowledgements_) {
      std::cout << "ERROR " << line_number << " protocol_error" << std::endl;
    }
    std::cerr << "gazebo_fleet_pose_transport: stdin line " << line_number <<
      ": " << reason << std::endl;
  }

  void RuntimeError(const std::string & reason)
  {
    std::lock_guard<std::mutex> lock(mutex_);
    std::cerr << "gazebo_fleet_pose_transport: " << reason << std::endl;
  }

private:
  bool acknowledgements_;
  std::mutex mutex_;
};

struct RequestState
{
  explicit RequestState(std::shared_ptr<Reporter> reporter_in)
  : reporter(std::move(reporter_in))
  {
  }

  std::mutex mutex;
  std::condition_variable condition;
  std::size_t pending{0U};
  bool delivery_error{false};
  std::shared_ptr<Reporter> reporter;
};

class FleetPoseClient
{
public:
  FleetPoseClient(
    std::string service, const std::size_t maximum_pending_requests,
    std::shared_ptr<Reporter> reporter)
  : service_(std::move(service)),
    maximum_pending_requests_(maximum_pending_requests),
    state_(std::make_shared<RequestState>(std::move(reporter)))
  {
  }

  bool Send(const Frame & frame)
  {
    bool queue_full = false;
    {
      std::lock_guard<std::mutex> lock(state_->mutex);
      if (state_->pending >= maximum_pending_requests_) {
        state_->delivery_error = true;
        queue_full = true;
      } else {
        ++state_->pending;
      }
    }
    if (queue_full) {
      state_->reporter->Reject(frame.id, "pending_queue_full");
      return false;
    }

    gz::msgs::Pose_V message;
    for (const Pose & input_pose : frame.poses) {
      gz::msgs::Pose * const pose = message.add_pose();
      pose->set_name(input_pose.name);
      pose->mutable_position()->set_x(input_pose.x);
      pose->mutable_position()->set_y(input_pose.y);
      pose->mutable_position()->set_z(input_pose.z);
      pose->mutable_orientation()->set_x(input_pose.qx);
      pose->mutable_orientation()->set_y(input_pose.qy);
      pose->mutable_orientation()->set_z(input_pose.qz);
      pose->mutable_orientation()->set_w(input_pose.qw);
    }

    const std::shared_ptr<RequestState> callback_state = state_;
    const std::string frame_id = frame.id;
    const auto completed = std::make_shared<std::atomic<bool>>(false);
    std::function<void(const gz::msgs::Boolean &, const bool)> callback =
      [callback_state, frame_id, completed](
      const gz::msgs::Boolean & reply, const bool result)
      {
        if (completed->exchange(true)) {
          return;
        }
        const bool accepted = result && reply.data();
        {
          std::lock_guard<std::mutex> lock(callback_state->mutex);
          if (callback_state->pending > 0U) {
            --callback_state->pending;
          }
          if (!accepted) {
            callback_state->delivery_error = true;
          }
        }
        callback_state->condition.notify_all();

        if (accepted) {
          callback_state->reporter->Acknowledge(frame_id);
        } else {
          callback_state->reporter->Reject(
            frame_id, result ? "service_rejected" : "transport_error");
        }
      };

    const bool scheduled =
      node_.Request<gz::msgs::Pose_V, gz::msgs::Boolean>(service_, message, callback);
    if (!scheduled) {
      if (!completed->exchange(true)) {
        {
          std::lock_guard<std::mutex> lock(state_->mutex);
          if (state_->pending > 0U) {
            --state_->pending;
          }
          state_->delivery_error = true;
        }
        state_->condition.notify_all();
        state_->reporter->Reject(frame.id, "request_not_scheduled");
      }
    }
    return scheduled;
  }

  bool Drain(const std::chrono::milliseconds timeout)
  {
    std::unique_lock<std::mutex> lock(state_->mutex);
    return state_->condition.wait_for(lock, timeout, [this]() {return state_->pending == 0U;});
  }

  std::size_t Pending() const
  {
    std::lock_guard<std::mutex> lock(state_->mutex);
    return state_->pending;
  }

  bool HadDeliveryError() const
  {
    std::lock_guard<std::mutex> lock(state_->mutex);
    return state_->delivery_error;
  }

private:
  std::string service_;
  std::size_t maximum_pending_requests_;
  gz::transport::Node node_;
  std::shared_ptr<RequestState> state_;
};

}  // namespace
}  // namespace gazebo_fleet_pose_transport

int main(int argc, char ** argv)
{
  using gazebo_fleet_pose_transport::FleetPoseClient;
  using gazebo_fleet_pose_transport::LineKind;
  using gazebo_fleet_pose_transport::OptionParseResult;
  using gazebo_fleet_pose_transport::Options;
  using gazebo_fleet_pose_transport::ParseLine;
  using gazebo_fleet_pose_transport::Reporter;

  Options options;
  std::string option_error;
  const OptionParseResult option_result =
    gazebo_fleet_pose_transport::ParseOptions(argc, argv, options, option_error);
  if (option_result == OptionParseResult::kHelp) {
    gazebo_fleet_pose_transport::Usage(std::cout, argv[0]);
    return EXIT_SUCCESS;
  }
  if (option_result == OptionParseResult::kError) {
    std::cerr << "gazebo_fleet_pose_transport: " << option_error << "\n\n";
    gazebo_fleet_pose_transport::Usage(std::cerr, argv[0]);
    return 2;
  }

  const std::string service = "/world/" + options.world + "/set_pose_vector";
  const auto reporter = std::make_shared<Reporter>(options.acknowledgements);
  FleetPoseClient client(service, options.maximum_pending_requests, reporter);
  reporter->Ready(service);

  bool protocol_error = false;
  std::size_t line_number = 0U;
  std::string line;
  while (std::getline(std::cin, line)) {
    ++line_number;
    const auto parsed = ParseLine(line);
    if (parsed.kind == LineKind::kIgnored) {
      continue;
    }
    if (parsed.kind == LineKind::kQuit) {
      break;
    }
    if (parsed.kind == LineKind::kError) {
      protocol_error = true;
      reporter->ProtocolError(line_number, parsed.error);
      continue;
    }
    client.Send(parsed.frame);
  }

  if (!std::cin.eof() && std::cin.fail()) {
    protocol_error = true;
    reporter->RuntimeError("failed while reading stdin");
  }

  const auto drain_timeout = std::chrono::milliseconds(options.drain_timeout_ms);
  if (!client.Drain(drain_timeout)) {
    reporter->RuntimeError(
      "timed out draining " + std::to_string(client.Pending()) + " pending request(s)");
    return 4;
  }
  if (client.HadDeliveryError()) {
    return 4;
  }
  if (protocol_error) {
    return 3;
  }
  return EXIT_SUCCESS;
}
