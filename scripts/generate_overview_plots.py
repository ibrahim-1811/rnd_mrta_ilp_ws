#!/usr/bin/env python3
"""Generate overview experiment and outcome signal plots for README.md."""

import os
os.environ["MPLCONFIGDIR"] = "/tmp/matplotlib_fig_gen"
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

# Set clean styling
plt.style.use("seaborn-v0_8-whitegrid" if "seaborn-v0_8-whitegrid" in plt.style.available else "default")
fig, axes = plt.subplots(2, 2, figsize=(14, 10), dpi=300)

# Colors
c_astar = "#2563eb"     # Blue
c_manhattan = "#f59e0b" # Amber
c_random = "#ef4444"    # Red
c_residual = "#8b5cf6"  # Purple
c_margin = "#10b981"    # Emerald

# -------------------------------------------------------------
# Panel 1: Fleet Scaling - Makespan & Waiting (W100, 300 Orders)
# -------------------------------------------------------------
ax1 = axes[0, 0]
robots = np.array([10, 20, 30, 50, 70, 100])
makespan = np.array([2860.8, 1463.4, 931.3, 616.7, 483.3, 416.7])
waiting = np.array([105.9, 219.7, 380.7, 687.3, 1910.7, 3399.3])

color = c_astar
ax1.set_xlabel("Fleet Size (Robots)", fontweight="bold")
ax1.set_ylabel("Order Makespan (s)", color=color, fontweight="bold")
l1 = ax1.plot(robots, makespan, marker="o", linewidth=2.5, color=color, label="Makespan (s)")
ax1.tick_params(axis="y", labelcolor=color)
ax1.set_title("A. Scalability & Traffic Waiting on W100", fontsize=13, fontweight="bold", pad=12)

ax1_twin = ax1.twinx()
color = c_random
ax1_twin.set_ylabel("Total Realized Waiting (s)", color=color, fontweight="bold")
l2 = ax1_twin.plot(robots, waiting, marker="s", linewidth=2.5, linestyle="--", color=color, label="Realized Waiting (s)")
ax1_twin.tick_params(axis="y", labelcolor=color)
ax1_twin.grid(False)

lines = l1 + l2
labels = [l.get_label() for l in lines]
ax1.legend(lines, labels, loc="center right", frameon=True)

# -------------------------------------------------------------
# Panel 2: Strategy Efficiency - Productive Distance (W100)
# -------------------------------------------------------------
ax2 = axes[0, 1]
fleet_subset = np.array([10, 100])
dist_astar = np.array([36368, 37416]) / 1000.0
dist_manh = np.array([36962, 39106]) / 1000.0
dist_rand = np.array([37612, 41404]) / 1000.0

x = np.arange(len(fleet_subset))
width = 0.25

rects1 = ax2.bar(x - width, dist_astar, width, label="Nearest A* (Grid-Aware)", color=c_astar)
rects2 = ax2.bar(x, dist_manh, width, label="Nearest Manhattan (Naive)", color=c_manhattan)
rects3 = ax2.bar(x + width, dist_rand, width, label="Random (Lower Bound)", color=c_random)

ax2.set_xlabel("Fleet Size (Robots)", fontweight="bold")
ax2.set_ylabel("Total Driven Distance (km)", fontweight="bold")
ax2.set_title("B. Directed Route Distance: Grid-Aware Advantage", fontsize=13, fontweight="bold", pad=12)
ax2.set_xticks(x)
ax2.set_xticklabels(["10 Robots", "100 Robots"])
ax2.set_ylim(30, 48)
ax2.legend(loc="upper left", frameon=True)

# Add percentage annotations on 100 robots
diff_manh = ((dist_manh[1] - dist_astar[1]) / dist_astar[1]) * 100
diff_rand = ((dist_rand[1] - dist_astar[1]) / dist_astar[1]) * 100
ax2.annotate(f"+{diff_manh:.1f}%", xy=(x[1], dist_manh[1]), xytext=(x[1], dist_manh[1]+1.0),
             ha="center", fontsize=9, fontweight="bold", color=c_manhattan)
ax2.annotate(f"+{diff_rand:.1f}%", xy=(x[1] + width, dist_rand[1]), xytext=(x[1] + width, dist_rand[1]+1.0),
             ha="center", fontsize=9, fontweight="bold", color=c_random)

# -------------------------------------------------------------
# Panel 3: M1 Outcome Signal - Residual vs Margin (30 vs 70 vs 100 Robots)
# -------------------------------------------------------------
ax3 = axes[1, 0]
conditions = ["30r (q=0)", "30r (q=3)", "70r (q=0)", "70r (q=3)", "100r (q=0)", "100r (q=3)"]
x_cond = np.arange(len(conditions))
p90_residual = [2.67, 5.03, 5.33, 12.00, 11.40, 96.63]
med_margin = [8.50, 9.67, 3.00, 3.33, 2.33, 1.67]

ax3.plot(x_cond, p90_residual, marker="^", linewidth=2.5, color=c_residual, label="p90 Residual (Waiting) [s]")
ax3.plot(x_cond, med_margin, marker="D", linewidth=2.5, linestyle="--", color=c_margin, label="Median Decision Margin [s]")
ax3.set_yscale("log")
ax3.set_xlabel("Operational Condition (Fleet & Dispatch Quantum)", fontweight="bold")
ax3.set_ylabel("Seconds (Log Scale)", fontweight="bold")
ax3.set_title("C. Signal Separation: Realized Waiting vs Static Margin", fontsize=13, fontweight="bold", pad=12)
ax3.set_xticks(x_cond)
ax3.set_xticklabels(conditions, rotation=25)
ax3.legend(loc="upper left", frameon=True)

# Highlight crossover
ax3.axvspan(2.5, 5.5, color="lightcoral", alpha=0.18, label="Outcome Dominates Estimate")
ax3.text(4.0, 15, "Waiting Exceeds\nStatic Margin", color="#b91c1c", fontweight="bold", ha="center")

# -------------------------------------------------------------
# Panel 4: M1 Frequency of Static Estimate Inversion
# -------------------------------------------------------------
ax4 = axes[1, 1]
exceed_share = [6.4, 8.0, 26.7, 36.9, 42.9, 58.7]
bar_colors = ["#93c5fd", "#60a5fa", "#3b82f6", "#2563eb", "#1d4ed8", "#1e40af"]

bars = ax4.bar(x_cond, exceed_share, color=bar_colors, edgecolor="#1e3a8a", width=0.55)
ax4.set_xlabel("Operational Condition", fontweight="bold")
ax4.set_ylabel("% Decisions with Residual > Margin", fontweight="bold")
ax4.set_title("D. Frequency Where Realized Outcome Inverts Static Ranking", fontsize=13, fontweight="bold", pad=12)
ax4.set_xticks(x_cond)
ax4.set_xticklabels(conditions, rotation=25)
ax4.set_ylim(0, 70)

for bar in bars:
    yval = bar.get_height()
    ax4.text(bar.get_x() + bar.get_width()/2.0, yval + 1.5, f"{yval:.1f}%", ha="center", va="bottom", fontsize=9, fontweight="bold")

plt.tight_layout()
out_dir = "/home/ibrahim/rnd_mrta_ilp_research/docs/figures"
os.makedirs(out_dir, exist_ok=True)
out_path = os.path.join(out_dir, "m1_outcome_signal_and_scaling.png")
plt.savefig(out_path, dpi=300)
print(f"Generated: {out_path}")

