#!/usr/bin/env bash
# One-command headless MRTA run.
#
#   scripts/run_experiment.sh [strategy] [num_robots] [num_orders] [seed] [rate_hz] [time_scale]
#
# Examples:
#   scripts/run_experiment.sh                       # auction, 6 robots, 20 orders
#   scripts/run_experiment.sh nearest 10 30 42      # nearest, 10 robots, 30 orders, seed 42
#   scripts/run_experiment.sh score 6 20 7 5 20
#
# Output is saved under  <repo>/Mrta_experiments/<strategy>_<timestamp>/
#
# (No 'set -u' -- the ROS 2 setup scripts reference unbound variables.)

STRATEGY=${1:-auction}
NUM_ROBOTS=${2:-6}
NUM_ORDERS=${3:-20}
SEED=${4:-42}
RATE=${5:-5.0}
TIME_SCALE=${6:-20.0}

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source /opt/ros/jazzy/setup.bash
source "$REPO/install/setup.bash"

STAMP=$(date +%Y-%m-%d_%H-%M-%S)
OUT="$REPO/Mrta_experiments/${STRATEGY}_${STAMP}"
mkdir -p "$OUT"

PATTERN='mock_fleet_manager|shared_memory_node|task_manager_node|ilp_dataset_logger|metrics_logger|order_generator'
cleanup() { pkill -9 -f "$PATTERN" 2>/dev/null; }
trap cleanup EXIT
cleanup; sleep 1

echo "== launching headless stack: strategy=$STRATEGY robots=$NUM_ROBOTS =="
ros2 launch task_management headless_warehouse.launch.py \
  num_robots:="$NUM_ROBOTS" strategy:="$STRATEGY" time_scale:="$TIME_SCALE" out_dir:="$OUT" \
  > "$OUT/launch.log" 2>&1 &
sleep 8

echo "== sending $NUM_ORDERS orders (seed=$SEED, rate=${RATE}Hz) =="
ros2 run task_management order_generator --ros-args \
  -p num_orders:="$NUM_ORDERS" -p seed:="$SEED" -p rate_hz:="$RATE" \
  > "$OUT/order_gen.log" 2>&1 &

for _ in $(seq 1 90); do
  M=$(ls -t "$OUT"/metrics_*.csv 2>/dev/null | head -1)
  if [ -n "$M" ]; then
    DONE=$(tail -1 "$M" | cut -d, -f4)
    echo "  progress: completed=${DONE:-0} / $NUM_ORDERS"
    [ "${DONE:-0}" = "$NUM_ORDERS" ] && break
  fi
  sleep 1
done

echo
echo "==================== RESULT ===================="
column -s, -t "$OUT"/metrics_*.csv 2>/dev/null || cat "$OUT"/metrics_*.csv
ILP=$(ls -t "$OUT"/ilp_logs/ilp_dataset_*.csv 2>/dev/null | head -1)
[ -n "$ILP" ] && echo "ILP dataset rows: $(( $(wc -l < "$ILP") - 1 ))"
echo "saved to: $OUT"
echo "================================================"
