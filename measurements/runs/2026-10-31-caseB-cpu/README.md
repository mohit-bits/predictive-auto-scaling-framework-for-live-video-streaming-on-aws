# Case B, CPU mode, 31 October 2026

RTMP publish ramp on the origin tier, scaled by the stock CPU target tracking
policy (60 % target). Compare with `../2026-10-31-caseB-streamaware/`.

## Conditions

- Stack from commit `868ccfb`, `ScalingMode=CPU`, `DetailedMonitoring=true`
  (one minute EC2 CPU), origin maximum 3, c5.xlarge origins. One idle edge.
- Collector pinned to `868ccfb`, cycles aligned to the minute. Origin load
  index = streams ingested / 35.
- Source `test1080p_2500k.mp4`, the same file as case A: 1080p30 H.264
  High, no B-frames, video 2505 kbps, AAC 128 kbps stereo.
- One ffmpeg publisher per stream (`-re -c copy`), through the NLB on
  port 1935. Harness c5.2xlarge.
- Schedule: baseline 5 min, +3 streams every 2 min to 48, hold. The run was
  stopped 13 minutes into the hold, at 11:22:57, at the operator's request.
  Times UTC.

## Result

| Time | Streams | Event |
|---|---|---|
| 10:34:37 | 0 | baseline starts |
| 10:56 | 27 | fleet index first above 0.70 (0.771); no load index policy in this mode |
| 11:03 | 36 | first minute of origin group CPU above 60 % (61.6 %) |
| 11:06:44 | 42 | high CPU alarm, first scale out; origin-1 at index 1.20 |
| 11:07:29 | 42 | origin-2 healthy in the RTMP target group |
| 11:07:37 to 11:07:51 | 45 | `caseB-44` lands on origin-2 and is cut off when AMS on that node restarts during boot |
| 11:12:24 | 48 | high CPU alarm again, scale out to origin-3, which never received a stream |
| 11:22:57 | 48 | run stopped; origin-1 46 streams, origin-2 1 |

Origin CPU against origin-1's own stream count (settled minute of each
step):

| Streams | 9 | 15 | 21 | 27 | 30 | 33 | 36 | 39 | 42 | 44 | 46 |
|---|---|---|---|---|---|---|---|---|---|---|---|
| CPU % | 14.0 | 24.2 | 33.4 | 44.2 | 50.6 | 57.0 | 61.6 | 66.2 | 70.9 | 74.3 | 78.0 |

CPU reached 60 % at 36 streams and 70 % at 42.

Over the first 41 min 7 s from baseline, the window compared with the
StreamAware run: origin-1 peaked at 46 streams (index 1.314), was above 35
streams for 14 minutes, 108 stream-minutes of overload, 33.0 origin
instance-minutes. Over the whole run: 21 minutes above 35, 185
stream-minutes, 48.5 instance-minutes.

Three things that explain the numbers:

- **The imbalance between origins came from pinning, not from the load
  balancer.** By the time origin-2 was healthy, origin-1 already held 42
  streams, and those stay where they are. Of the six streams that started
  afterwards, four went to origin-1 and two to origin-2.
- **The third origin was added on an incomplete average.** From 11:08 to
  11:12 the group CPU the policy acts on equalled origin-1's CPU alone
  (72.6 to 77.9 %), while origin-2 ran at 2 to 3 %. The policy therefore saw
  about 77 % where the two-node average was about 40 %.
- **A new origin takes traffic before it has finished booting.** The NLB's
  TCP health check passed 38 s after launch. About 40 s after AMS started,
  the node's user data restarted it while installing packages, and
  the one publisher that had landed in that window was disconnected.

All other publishers ran at real time throughout. Harness peak CPU 11.8 %.
Every collector journal has exactly one line per minute.

## Files

Identifiers are replaced by roles (`origin-1` to `origin-3` in launch order,
`edge-1`, `harness`, `<account-id>`, `<nlb-dns>`, `<origin-N-private-ip>`,
`<subnet-origin>`; hostnames as `<role>-host`).

| File | Content |
|---|---|
| `summary.txt` | minute by minute table and derived figures |
| `harness/events.tsv` | every schedule action with its time |
| `harness/caseB-NN.log` | each publisher's ffmpeg header, errors, speed summary and final progress line |
| `harness/harness.tsv` | harness CPU, memory, running publishers and egress every 10 s |
| `asg_state.tsv`, `rtmp_target_health.tsv` | every 10 s: group state and RTMP target health |
| `stream_placement.tsv` | every 60 s: which origin each stream was on |
| `origin_*.json` | Auto Scaling and CloudWatch records of every decision |
| `journals/*.log` | each origin's collector journal |
| `cloudwatch/*.tsv` | one minute series: time, average, maximum, sum, sample count |
