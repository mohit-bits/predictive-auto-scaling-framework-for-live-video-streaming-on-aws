# Case B, StreamAware mode, 31 October 2026

RTMP publish ramp on the origin tier, scaled by the load index policy:
target tracking at 0.70 on the fleet index, SUM of per origin `LoadIndex`
divided by `GroupDesiredCapacity`. Compare with `../2026-10-31-caseB-cpu/`.

## Conditions

Identical to the CPU run, on the same stack after a stack update to
`ScalingMode=StreamAware`, starting from one origin: commit `868ccfb`,
`DetailedMonitoring=true`, origin maximum 3, c5.xlarge origins, the same
`test1080p_2500k.mp4` source, publishers through the NLB, the same schedule.
Times UTC.

During the hold, ten extra streams were added one at a time at the
operator's request, to see where the load balancer placed them. The run was
stopped at 12:31:32, 17 minutes into the hold.

## Result

| Time | Streams | Event |
|---|---|---|
| 11:38:53 | 0 | baseline starts |
| 12:00 | 27 | fleet index first above 0.70 (0.771) |
| 12:03:08 | 30 | target tracking alarm, scale out to 2 (fleet index 0.771, 0.771, 0.857) |
| 12:04:11 | 33 | origin-2 healthy in the RTMP target group |
| 12:13:54 to 12:20 | 48 | hold: origin-1 42 streams, origin-2 6; fleet index 0.686; no further scaling |
| 12:20:03 to 12:23:34 | 58 | ten extra streams added: four to origin-1, six to origin-2 |
| 12:24:08 | 58 | fleet index 0.77 to 0.81, scale out to 3; index then 0.55 |
| 12:31:32 | 58 | run stopped; origin-1 46 streams, origin-2 12, origin-3 0 |

- Scale out came 7 min 52 s earlier relative to baseline than in the CPU
  run, with 30 streams connected instead of 42.
- At 48 streams the group held at two origins.
- The CPU safety alarm (collector CPU above 75 % for three minutes) did not
  fire; origin-1 ran at 69 to 70 %.

Over the first 41 min 7 s from baseline, up to the first extra stream and
the same window as the CPU run: origin-1 peaked at 42 streams (index 1.200),
was above 35 streams for 12 minutes, 70 stream-minutes of overload, 39.8
origin instance-minutes. The CPU run over the same window: 46 streams
(1.314), 14 minutes, 108 stream-minutes, 33.0 instance-minutes.

Two things that explain the numbers:

- **New streams split evenly between origins; the imbalance came from
  existing streams staying pinned.** Of the 25 streams that started after
  origin-2 was healthy, including the ten extra, 13 went to origin-1 and 12
  to origin-2. Origin-1 stayed above its capacity because the 33 streams it
  already held when origin-2 joined could not move, not because the load
  balancer favoured it.
- **StreamAware cost more in the measured window** because it scaled out
  earlier and so ran two origins for longer: 39.8 against 33.0 origin
  instance-minutes. The overload it removed was 38 stream-minutes.

The new origins' AMS restarted during boot as in the CPU run (origin-2 at
12:04:15 to 12:04:20), but no stream arrived in that window and none was
lost. All 58 publishers ran at real time throughout. Harness peak CPU 9.4 %.
Every collector journal has exactly one line per minute.

## Files

As in the CPU run folder; roles `origin-1` to `origin-3`, `edge-1`,
`harness` and the same placeholders.
