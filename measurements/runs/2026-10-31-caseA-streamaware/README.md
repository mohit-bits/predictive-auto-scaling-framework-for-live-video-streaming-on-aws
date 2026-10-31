# Case A, StreamAware mode, 31 October 2026

WebRTC viewer ramp on the edge tier, scaled by the load index policy: target
tracking at 0.70 on the fleet index, SUM of per edge `LoadIndex` divided by
`GroupDesiredCapacity`. Compare with `../2026-10-31-caseA-cpu/`.

## Conditions

Identical to the CPU run, on a stack recreated from scratch so the edge
booted fresh as it did for that run: commit `868ccfb`,
`ScalingMode=StreamAware`, `DetailedMonitoring=true`, edge maximum 3,
c5.xlarge origin and edge, the same `test1080p_2500k.mp4` source (video
2505 kbps), the same viewer schedule through the ALB. Times UTC.

The run ended at the end of the hold: the gradual ramp down was replaced by
stopping all viewers at once at 08:52:43, at the operator's request. Scale in
is out of scope, so nothing compared here depends on the ramp down.

## Result

| Time | Viewers | Event |
|---|---|---|
| 08:01:28 | 0 | baseline starts |
| 08:21 | 80 | first minute of fleet index above 0.70 (0.762) |
| 08:24:08 | 90 | target tracking alarm, scale out to 2 (fleet index 0.762, 0.762, 0.857) |
| 08:25:31 | 100 | edge-2 healthy; edge-1 at 100 viewers, index 0.95 |
| 08:32:28 to 08:52:28 | 140 | hold: edge-1 100 viewers, edge-2 40; fleet index 0.667; no further scaling |

- Scale out came 1 min 43 s earlier relative to the start of the ramp than
  in the CPU run, with 90 viewers connected instead of about 100.
- The group held at 2 edges through the hold. The target tracking, CPU
  safety and scale in alarms all stayed OK.
- Edge-1 peaked at 100 viewers, index 0.952: never above tested capacity,
  no overload.
- 57.2 edge instance-minutes from baseline to the end of the hold, against
  70.8 for the CPU run over the same 51 minutes (19 % fewer).
- No dropped connections; 33 ms frame period in every sample. Harness peak
  CPU 40.8 %.
- Every collector journal has exactly one line per minute, and CloudWatch
  one `LoadIndex` sample per minute per instance.
- Which edge the scale in policy would remove was not observable: the fleet
  index only falls below 0.35 for fifteen minutes after the run, when both
  edges are empty.

An earlier StreamAware attempt that morning was abandoned before its scale
out and is not included.

## Files

As in the CPU run folder; roles `edge-1`, `edge-2`, `origin`, `harness` and
the same placeholders.
