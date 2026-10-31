# Case A, CPU mode, 31 October 2026

WebRTC viewer ramp on the edge tier, scaled by the stock CPU target tracking
policy (60 % target). Compare with `../2026-10-31-caseA-streamaware/`.

## Conditions

- Stack from commit `868ccfb`, `ScalingMode=CPU`, `DetailedMonitoring=true`
  (one minute EC2 CPU), edge maximum 3, c5.xlarge origin and edge.
- Collector pinned to `868ccfb`, cycles aligned to the minute.
- Source `test1080p_2500k.mp4`: 1080p30 H.264 High, no B-frames, video
  2505 kbps, AAC 128 kbps stereo. 2500 kbps is the bitrate the playback
  capacity (105 viewers per c5.xlarge) was measured at.
- Viewers connected through the ALB on port 5080, one load tool process per
  step of 10. Harness c5.2xlarge.
- Schedule: baseline 5 min, +10 viewers every 2 min to 140, hold 20 min,
  -20 every 2 min to 0 (newest processes stop first). Times UTC.

## Result

| Time | Viewers | Event |
|---|---|---|
| 05:40:14 | 0 | baseline starts |
| 06:01 | 90 | first minute of edge group CPU above 60 % (64.0 %) |
| 06:04:37 | 100 | high CPU alarm, first scale out |
| 06:06:04 | 110 | edge-2 healthy; edge-1 at 110 viewers, index 1.05 |
| 06:10:37 | 140 | high CPU alarm again, scale out to edge-3 |
| 06:11 to 06:31 | 140 | edge-1 110 viewers, edge-2 30, edge-3 0 |
| 06:34:26 | 100 | low CPU alarm (from minutes 06:19 to 06:33); edge-2 removed |
| 06:45:15 | 0 | run ends |

- Edge-1 was above its tested capacity for 28 minutes, peak index 1.048,
  140 viewer-minutes of overload.
- Edge-3 never carried a viewer.
- At the two scale-out decisions the fleet load index, computed from this
  run's own collector data, was 0.857 to 0.952 and 0.571 to 0.619. A load
  index policy with a 0.70 target would have scaled out from 06:00 and would
  not have added edge-3.
- 70.8 edge instance-minutes from baseline to the end of the hold.
- No dropped connections; 33 ms frame period in every sample. Harness peak
  CPU 47.8 %.
- Every collector journal has exactly one line per minute, and CloudWatch
  one `LoadIndex` sample per minute per instance.

## Files

Identifiers are replaced by roles (`edge-1` to `edge-3` in launch order,
`origin`, `harness`, `<account-id>`, `<alb-dns>`, `<origin-private-ip>`,
`<subnet-edge>`, `<subnet-origin>`; hostnames as `<role>-host`).

| File | Content |
|---|---|
| `summary.txt` | minute by minute table and derived figures |
| `harness/events.tsv` | every schedule action with its time |
| `harness/player-NN.stats.tsv` | the load tool's 10 s statistics per 10 viewer process |
| `harness/harness.tsv` | harness CPU, memory and running tool processes every 10 s |
| `harness/publisher.log` | ffmpeg header and final progress line |
| `asg_state.tsv`, `edge_target_health.tsv` | every 10 s: group state and edge target health |
| `edge_*.json`, `origin_scaling_activities.json` | Auto Scaling and CloudWatch records of every decision |
| `journals/*.log` | each node's collector journal |
| `cloudwatch/*.tsv` | one minute series: time, average, maximum, sum, sample count |

The raw load tool logs (hundreds of MB, mostly a repeated native decoder
warning) are not included; their statistics lines are.
