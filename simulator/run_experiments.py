"""
Runs the comparison between a CPU driven policy and a load index driven
policy across three demand patterns.

Scenario 1  gradual ramp on a WebRTC playback tier
Scenario 2  flash crowd on the same tier
Scenario 3  transcoding workload on a GPU tier

Outputs a summary table, a CSV time series per run, and a chart per scenario.
"""

import csv
import os

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

from measured_data import (
    WEBRTC_PLAYBACK_CAPACITY,
    RTMP_ABR_GPU,
    playback_cpu_curve,
)
from simulator import NodeProfile, Policy, ClusterConfig, simulate

OUT = os.path.join(os.path.dirname(__file__), "results")
os.makedirs(OUT, exist_ok=True)


# ---------------------------------------------------------------------------
# Node profiles built from the measured data
# ---------------------------------------------------------------------------

# WebRTC playback, c5.xlarge, 1080p. Measured capacity 105 viewers at 70-75%
# CPU. The CPU shape between idle and capacity is the straight line, which is
# the conservative choice: it makes CPU rise sooner than it really does and so
# flatters the CPU policy.
PLAYBACK = NodeProfile(
    name="c5.xlarge WebRTC playback 1080p",
    instance_type="c5.xlarge",
    workload="webrtc_playback_1080p",
    capacity=WEBRTC_PLAYBACK_CAPACITY["c5.xlarge"]["1080p"],
    cpu_curve=playback_cpu_curve("c5.xlarge", "1080p", convex=False),
    unit="viewers",
)

# RTMP ingest with one 1080p rendition on g5.4xlarge.
# Measured: 12 streams, CPU 15-20%, GPU encoder 70-75%.
_g_streams, _g_cpu, _g_gpu = RTMP_ABR_GPU[("g5.4xlarge", 1)]
TRANSCODE_GPU = NodeProfile(
    name="g5.4xlarge RTMP with 1080p transcode",
    instance_type="g5.4xlarge",
    workload="rtmp_abr_1080p",
    capacity=_g_streams,
    cpu_curve=[(0, 0.0), (_g_streams, _g_cpu)],
    gpu_curve=[(0, 0.0), (_g_streams, _g_gpu)],
    unit="streams",
    cpu_saturates=True,
)


# ---------------------------------------------------------------------------
# Policies
# ---------------------------------------------------------------------------

CPU_POLICY = Policy(
    name="CPU target 60%",
    metric="cpu",
    target=60.0,
)

INDEX_POLICY_MEAN = Policy(
    name="Index 0.70, per node mean",
    metric="index",
    target=0.70,
    aggregate="mean_per_node",
    safety_metric="cpu",
    safety_threshold=75.0,
)

INDEX_POLICY = Policy(
    name="Index 0.70, fleet total",
    metric="index",
    target=0.70,
    aggregate="fleet_total",
    safety_metric="cpu",
    safety_threshold=75.0,
)


# ---------------------------------------------------------------------------
# Demand patterns
# ---------------------------------------------------------------------------

def gradual_ramp(peak=1200, ramp_min=60, hold_min=30, fall_min=30, base=50):
    """A scheduled event filling up steadily, holding, then emptying."""
    ramp, hold, fall = ramp_min * 60, hold_min * 60, fall_min * 60

    def f(t):
        if t < ramp:
            return base + (peak - base) * t / ramp
        if t < ramp + hold:
            return peak
        if t < ramp + hold + fall:
            return peak - (peak - base) * (t - ramp - hold) / fall
        return base
    return f


def flash_crowd(peak=1200, base=60, start_min=20, rise_min=3,
                hold_min=40, fall_min=25):
    """
    The pattern that actually hurts: a quiet period, then a surge when a
    popular stream goes live or gets shared.
    """
    start, rise = start_min * 60, rise_min * 60
    hold, fall = hold_min * 60, fall_min * 60

    def f(t):
        if t < start:
            return base
        if t < start + rise:
            return base + (peak - base) * (t - start) / rise
        if t < start + rise + hold:
            return peak
        if t < start + rise + hold + fall:
            return peak - (peak - base) * (t - start - rise - hold) / fall
        return base
    return f


def transcode_ramp(peak=60, ramp_min=40, hold_min=30, fall_min=20, base=2):
    """Broadcasters joining a transcoded event, in streams rather than viewers."""
    ramp, hold, fall = ramp_min * 60, hold_min * 60, fall_min * 60

    def f(t):
        if t < ramp:
            return base + (peak - base) * t / ramp
        if t < ramp + hold:
            return peak
        if t < ramp + hold + fall:
            return peak - (peak - base) * (t - ramp - hold) / fall
        return base
    return f


# ---------------------------------------------------------------------------
# Running and reporting
# ---------------------------------------------------------------------------

SCENARIOS = [
    ("s1_gradual_ramp", "Gradual ramp, WebRTC playback",
     PLAYBACK, gradual_ramp(), 150 * 60, 1),
    ("s2_flash_crowd", "Flash crowd, WebRTC playback",
     PLAYBACK, flash_crowd(), 120 * 60, 1),
    ("s3_transcode_gpu", "Transcoding workload, GPU tier",
     TRANSCODE_GPU, transcode_ramp(), 110 * 60, 1),
]


def write_csv(res, path):
    with open(path, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["time_s", "demand", "served", "nodes_inservice",
                    "nodes_total", "mean_cpu", "mean_index", "mean_gpu",
                    "max_index"])
        for i in range(len(res.times)):
            w.writerow([res.times[i], round(res.demand[i], 1),
                        round(res.served[i], 1), res.nodes_inservice[i],
                        res.nodes_total[i], round(res.mean_cpu[i], 2),
                        round(res.mean_index[i], 3), round(res.mean_gpu[i], 2),
                        round(res.max_index[i], 3)])


def chart(scenario_key, title, res_cpu, res_idx, profile):
    mins = [t / 60.0 for t in res_cpu.times]
    fig, axes = plt.subplots(3, 1, figsize=(10, 9), sharex=True)

    ax = axes[0]
    cap_cpu = [n * profile.capacity for n in res_cpu.nodes_inservice]
    cap_idx = [n * profile.capacity for n in res_idx.nodes_inservice]
    ax.plot(mins, res_cpu.demand, color="#16212B", lw=1.8, label="demand")
    ax.step(mins, cap_cpu, where="post", color="#C55A11", lw=1.4, ls="--",
            label="fleet capacity, CPU policy")
    ax.step(mins, cap_idx, where="post", color="#1F4E79", lw=1.4, ls=":",
            label="fleet capacity, index policy")
    ax.fill_between(mins, res_cpu.demand, cap_cpu,
                    where=[d > c for d, c in zip(res_cpu.demand, cap_cpu)],
                    color="#C55A11", alpha=0.12, step="post")
    ax.set_ylabel(f"concurrent {profile.unit}")
    ax.set_title(title)
    ax.legend(fontsize=8)
    ax.grid(alpha=0.25)

    ax = axes[1]
    ax.step(mins, res_cpu.nodes_inservice, where="post", color="#C55A11",
            lw=1.6, label="CPU policy")
    ax.step(mins, res_idx.nodes_inservice, where="post", color="#1F4E79",
            lw=1.6, label="index policy")
    ax.set_ylabel("nodes in service")
    ax.legend(fontsize=8)
    ax.grid(alpha=0.25)

    ax = axes[2]
    ax.plot(mins, res_cpu.max_index, color="#C55A11", lw=1.4,
            label="worst node index, CPU policy")
    ax.plot(mins, res_idx.max_index, color="#1F4E79", lw=1.4,
            label="worst node index, index policy")
    ax.axhline(1.0, color="#777", lw=1.0, ls="--")
    ax.text(mins[-1], 1.02, "tested capacity", ha="right", fontsize=8,
            color="#555")
    ax.set_ylabel("load index")
    ax.set_xlabel("minutes")
    ax.legend(fontsize=8)
    ax.grid(alpha=0.25)

    fig.tight_layout()
    fig.savefig(os.path.join(OUT, f"{scenario_key}.png"), dpi=150)
    plt.close(fig)


def main():
    rows = []
    for key, title, profile, demand, duration, start in SCENARIOS:
        results = {}
        for pol in (CPU_POLICY, INDEX_POLICY_MEAN, INDEX_POLICY):
            cfg = ClusterConfig(profile=profile, policy=pol)
            res = simulate(cfg, demand, duration, start_nodes=start)
            results[pol.name] = res
            tag = pol.name.replace(",", "").replace(" ", "_").replace("%", "pct").replace(".", "").lower()
            write_csv(res, os.path.join(OUT, f"{key}_{tag}.csv"))
            s = res.summary()
            s["scenario"] = title
            rows.append(s)

        chart(key, title, results[CPU_POLICY.name], results[INDEX_POLICY.name], profile)

    # print the summary table
    print()
    print("=" * 116)
    print(f"{'scenario':<30} {'policy':<24} {'nodes-min':>10} "
          f"{'cost $':>8} {'shortfall':>10} {'under-min':>10} "
          f"{'overload':>10} {'peak idx':>9}")
    print("=" * 116)
    last = None
    for r in rows:
        if r["scenario"] != last:
            print("-" * 116)
            last = r["scenario"]
        over_key = [k for k in r if k.startswith("overload_")][0]
        short_key = [k for k in r if k.startswith("shortfall_")][0]
        print(f"{r['scenario']:<30} {r['policy']:<24} "
              f"{r['instance_minutes']:>10.1f} {r['cost_usd']:>8.3f} "
              f"{r[short_key]:>10.1f} {r['underprovisioned_min']:>10.1f} "
              f"{r[over_key]:>10.1f} {r['peak_index']:>9.2f}")
    print("=" * 116)
    print()
    print("overload column is load carried beyond the tested capacity of a node,")
    print("in viewer-minutes for scenarios 1 and 2 and stream-minutes for scenario 3.")
    print()


if __name__ == "__main__":
    main()
