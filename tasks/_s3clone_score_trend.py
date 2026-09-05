"""Plot s3-clone official-verifier score trend across trials (diagnostic_leaked)."""
from __future__ import annotations

import datetime as dt
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import font_manager

# Chinese font on Windows
for f in ("Microsoft YaHei", "SimHei"):
    if any(f.lower() == ff.name.lower() for ff in font_manager.fontManager.ttflist):
        plt.rcParams["font.family"] = f
        break
plt.rcParams["axes.unicode_minus"] = False

# Trial, date, gates_passed/22, tests_passed/146
trials = [
    ("s3-clone_02", dt.date(2026, 8, 16), 16, 129),
    ("s3-clone_07", dt.date(2026, 9, 1), 0, 14),
    ("s3-clone_08", dt.date(2026, 9, 4), 5, 69),
    ("s3-clone_10", dt.date(2026, 9, 5), 10, 102),
]

dates = [t[1] for t in trials]
gates = [t[2] for t in trials]
tests = [t[3] for t in trials]
labels = [f"{t[0]}\n{t[1].month}-{t[1].day:02d}" for t in trials]

fig, ax1 = plt.subplots(figsize=(9.5, 5.6), dpi=150)

ax1.plot(dates, gates, "o-", color="#2563eb", lw=2.2, ms=8, label="门级 gates_passed / 22")
ax1.set_ylim(-1, 23)
ax1.set_ylabel("门级（22 官方 pytest 门）", color="#2563eb")
ax1.tick_params(axis="y", labelcolor="#2563eb")
ax1.set_xticks(dates)
ax1.set_xticklabels(labels, rotation=12, ha="right")
ax1.grid(True, alpha=0.3, linestyle="--")

for d, g in zip(dates, gates):
    ax1.annotate(f"{g}/22", (d, g), textcoords="offset points", xytext=(0, 11),
                 ha="center", color="#2563eb", fontweight="bold")

ax2 = ax1.twinx()
ax2.plot(dates, tests, "s--", color="#dc2626", lw=1.8, ms=7, label="用例级 passed / 146")
ax2.set_ylim(-5, 155)
ax2.set_ylabel("用例级（146 pytest 用例）", color="#dc2626")
ax2.tick_params(axis="y", labelcolor="#dc2626")

for d, v in zip(dates, tests):
    ax2.annotate(f"{v}", (d, v), textcoords="offset points", xytext=(0, -16),
                 ha="center", color="#dc2626")

ax1.axhline(22, color="#16a34a", lw=1, alpha=0.6)
ax1.text(dates[0], 22.25, "reward=1 需要 22/22 门全绿（二进制）", color="#16a34a", fontsize=9)

lines1, labels1 = ax1.get_legend_handles_labels()
lines2, labels2 = ax2.get_legend_handles_labels()
ax1.legend(lines1 + lines2, labels1 + labels2, loc="center left", fontsize=9)

ax1.set_title("s3-clone 官方 verifier 正确性成绩趋势（NAS slim 复跑，全部 diagnostic_leaked；reward 均为 0）",
              fontsize=11.5)
fig.text(0.99, 0.01,
         "口径：官方 tests/test.sh，--network none，slim 无 CUA key（CUA hard-fail 属预期）；"
         "partial_score=门禁比例，非官方 50/50",
         ha="right", fontsize=7.5, color="#666")

fig.tight_layout()
out = r"D:\PC_AI\Project\HiveWeave\tasks\_s3clone_score_trend.png"
fig.savefig(out, bbox_inches="tight")
print("saved", out)
