"""
Sensitivity of the comparison to the two assumed parameters.

The simulator takes its capacity numbers from measured load tests, but two
inputs are assumptions rather than measurements:

  mean viewer session length   how fast a new node fills through churn
  instance boot time           how long capacity takes to arrive

Neither has been measured yet. If the conclusion only holds at one particular
value of either, it is not a conclusion. This sweeps both and reports whether
the ordering between the policies survives.
"""

import csv
import os

from measured_data import WEBRTC_PLAYBACK_CAPACITY, RTMP_ABR_GPU, playback_cpu_curve
from simulator import NodeProfile, Policy, ClusterConfig, simulate
from run_experiments import (
    PLAYBACK, TRANSCODE_GPU, CPU_POLICY, INDEX_POLICY,
    gradual_ramp, flash_crowd, transcode_ramp, OUT,
)

SESSION_LENGTHS_MIN = [5, 10, 20, 40, 60]
BOOT_TIMES_S = [120, 180, 300, 420, 600]

CASES = [
    ("gradual ramp", PLAYBACK, gradual_ramp(), 150 * 60),
    ("flash crowd", PLAYBACK, flash_crowd(), 120 * 60),
    ("GPU transcode", TRANSCODE_GPU, transcode_ramp(), 110 * 60),
]


def run(profile, demand, duration, policy, session_min, boot_s):
    cfg = ClusterConfig(profile=profile, policy=policy,
                        boot_time_s=boot_s,
                        mean_session_s=session_min * 60)
    return simulate(cfg, demand, duration, start_nodes=1)


def sweep(param_name, values, fixed_session=20, fixed_boot=300):
    rows = []
    for case_name, profile, demand, duration in CASES:
        for v in values:
            session = v if param_name == "session" else fixed_session
            boot = v if param_name == "boot" else fixed_boot

            r_cpu = run(profile, demand, duration, CPU_POLICY, session, boot)
            r_idx = run(profile, demand, duration, INDEX_POLICY, session, boot)

            short_cpu = r_cpu.shortfall_unit_minutes
            short_idx = r_idx.shortfall_unit_minutes
            reduction = (1 - short_idx / short_cpu) * 100 if short_cpu > 0 else 0.0

            rows.append({
                "case": case_name,
                "param": param_name,
                "value": v,
                "cost_cpu": r_cpu.cost_usd,
                "cost_idx": r_idx.cost_usd,
                "cost_ratio": (r_idx.cost_usd / r_cpu.cost_usd) if r_cpu.cost_usd else 0,
                "shortfall_cpu": short_cpu,
                "shortfall_idx": short_idx,
                "shortfall_reduction_pct": reduction,
                "under_cpu_min": r_cpu.underprovisioned_s / 60.0,
                "under_idx_min": r_idx.underprovisioned_s / 60.0,
                "cpu_scale_events": r_cpu.scale_out_events,
            })
    return rows


def report(rows, title, unit_label):
    print()
    print(title)
    print("-" * 108)
    print(f"{'case':<16}{unit_label:>10}{'cost CPU':>10}{'cost idx':>10}"
          f"{'ratio':>8}{'short CPU':>12}{'short idx':>12}"
          f"{'reduction':>11}{'CPU acts':>10}")
    print("-" * 108)
    last = None
    for r in rows:
        if r["case"] != last:
            if last is not None:
                print()
            last = r["case"]
        print(f"{r['case']:<16}{r['value']:>10}{r['cost_cpu']:>10.2f}"
              f"{r['cost_idx']:>10.2f}{r['cost_ratio']:>8.2f}"
              f"{r['shortfall_cpu']:>12.0f}{r['shortfall_idx']:>12.0f}"
              f"{r['shortfall_reduction_pct']:>10.0f}%{r['cpu_scale_events']:>10}")
    print("-" * 108)


def main():
    s_rows = sweep("session", SESSION_LENGTHS_MIN)
    b_rows = sweep("boot", BOOT_TIMES_S)

    report(s_rows, "Sensitivity to mean viewer session length", "session min")
    report(b_rows, "Sensitivity to instance boot time", "boot sec")

    with open(os.path.join(OUT, "sensitivity.csv"), "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(s_rows[0].keys()))
        w.writeheader()
        for r in s_rows + b_rows:
            w.writerow(r)

    # does the ordering hold everywhere
    flips = [r for r in s_rows + b_rows if r["shortfall_reduction_pct"] < 0]
    print()
    if flips:
        print(f"WARNING: the ordering reverses in {len(flips)} of "
              f"{len(s_rows) + len(b_rows)} runs:")
        for r in flips:
            print(f"  {r['case']}, {r['param']}={r['value']}, "
                  f"reduction {r['shortfall_reduction_pct']:.0f}%")
    else:
        print(f"The load index policy reduced capacity shortfall in all "
              f"{len(s_rows) + len(b_rows)} parameter combinations tested.")
    print()


if __name__ == "__main__":
    main()
