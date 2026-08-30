# Gazebo fleet pose transport

`gazebo_fleet_pose_transport` is a persistent, visualization-only client for
Gazebo Harmonic's batched pose service. It reads complete fleet frames from
standard input and sends one asynchronous `gz.msgs.Pose_V` request per frame
to:

```text
/world/<world>/set_pose_vector
```

The Gazebo reply type is `gz.msgs.Boolean`. The helper owns no simulation
clock, robot state, trajectory, or reservation state.

## Start it

After building and sourcing the workspace:

```bash
ros2 run gazebo_fleet_pose_transport gazebo_fleet_pose_transport \
  --world warehouse
```

The process stays alive and reads standard input until EOF or `QUIT`.

## Input protocol

The protocol is UTF-8-compatible, whitespace-delimited ASCII, with exactly one
command per line. A complete fleet frame is:

```text
FRAME <frame_id> <pose_count> <name> <x> <y> <z> <qx> <qy> <qz> <qw> [pose ...]
```

There must be exactly `pose_count` pose records. Each record has one entity
name and seven finite decimal pose values. Entity names and frame IDs may not
contain whitespace. Entity names must be unique within a frame, and each
quaternion must be nonzero. For fixed upright robots, send `0 0 0 1` as the
quaternion.

Example with two robots:

```text
FRAME 120 2 robot_001 4.25 7.0 0.155 0 0 0 1 robot_002 9.5 3.75 0.155 0 0 0 1
```

Blank lines and lines whose first non-whitespace character is `#` are ignored.
`QUIT` stops input processing, drains outstanding requests for up to the drain
timeout, and exits. EOF has the same drain behavior.

Malformed lines are reported on standard error and skipped, so one bad visual
frame cannot terminate a mission. A frame is also rejected if the bounded
pending-request limit is reached.

## Optional acknowledgements

By default successful frames produce no output, avoiding stdout backpressure
in a long playback. Pass `--ack` to enable a machine-readable stdout stream:

```text
READY /world/warehouse/set_pose_vector
ACK 120
NACK 121 service_rejected
ERROR 4 protocol_error
```

`READY` means the helper is ready to parse input; service discovery remains
Gazebo Transport's responsibility. Because requests are nonblocking, ACKs may
arrive in a different order from input frames. Detailed errors always go to
standard error.

Other options are:

```text
--max-pending COUNT       default 256
--drain-timeout-ms MS     default 2000
```

Exit status is `0` after a clean drain, `2` for invalid command-line options,
`3` if any input line was malformed, and `4` for a request failure, service
rejection, full pending queue, or drain timeout.
