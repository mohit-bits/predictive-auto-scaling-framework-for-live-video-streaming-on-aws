# Case A, run 1, CPU mode, 30 October 2026: SUPERSEDED

**Not valid as a comparison run.** The publisher sent 1080p at 2000 kbps.
The playback capacity it is measured against (105 viewers on a c5.xlarge)
was established with a 2500 kbps source. Cost per viewer scales with
bitrate, so this edge could hold about 105 × 2500/2000 = 131 viewers, and
it held about 130 at the CPU the table gives for 105. The index and
overload figures below are therefore computed against the wrong capacity.
The run is to be repeated at 2500 kbps.

**Kept because** it is the first live observation of the over-provisioning
the simulator predicted. The mechanism does not depend on the bitrate: a
WebRTC session is pinned to the edge it connected to, so after a scale-out
the loaded edge stayed loaded, the new edges stayed nearly empty, the group
average stayed above target, and the CPU policy added an edge that never
served a viewer.

## What happened

Stack from commit `c6be742`, `ScalingMode=CPU`, `DetailedMonitoring=true`
(one minute EC2 CPU), edge maximum 3. All times UTC.

| Time | Viewers requested | Event |
|---|---|---|
| 17:27:25 | 0 | baseline starts, publisher already running |
| 17:52 | 110 | first minute of edge group CPU above 60 % (61.5 %) |
| 17:55:13 | 120 | high CPU alarm, first scale out; edge-1 at 120 viewers |
| 17:56:25 | 130 | that step's viewers land on edge-1, one second before edge-2 is healthy (17:56:26) |
| 17:58:25 | 140 | the last step's 10 viewers land on edge-2 |
| 18:01:13 | 140 | high CPU alarm again, second scale out to edge-3 |
| 18:02 to 18:18 | 140 | edge-1 130 viewers at about 73 % CPU, edge-2 10, edge-3 0; group average about 27 % |
| 18:18:07 | 140 | low CPU alarm, from fifteen minutes of hold data |
| 18:22:14, 18:28:08 | 80, 20 | scale in: edge-2, then edge-3 |
| 18:32:25 | 0 | run ends |

Edge-3 ran for 26 minutes and never carried a viewer. Viewer quality was
unaffected throughout: no dropped connections and a 33 ms frame period in
every sample. New edges were healthy behind the load balancer 73 s and 79 s
after their alarms.

## Files

Identifiers are replaced by roles: `edge-1`, `edge-2`, `edge-3` in launch
order, `origin`, `harness`, `<account-id>`, `<alb-dns>`,
`<origin-private-ip>`, `<subnet-edge>`, `<subnet-origin>`.

| File | Content |
|---|---|
| `summary.txt` | minute by minute table and derived figures |
| `harness/events.tsv` | every schedule action with its time |
| `harness/player-NN.stats.tsv` | the load tool's 10 s statistics for each 10 viewer process |
| `harness/harness_cpu.tsv` | harness CPU every 10 s, from 17:36:37 |
| `harness/harness.tsv` | harness memory and running tool processes every 10 s. Its CPU column is blank because of a bug in the sampler; `harness_cpu.tsv` replaced it, and `cloudwatch/harness_EC2CPU.tsv` covers the start |
| `harness/publisher.log` | ffmpeg header and its final progress line |
| `asg_state.tsv`, `edge_target_health.tsv` | every 10 s: group desired, instances and their state; edge target health |
| `edge_scaling_activities.json`, `edge_alarm_history.json`, `edge_alarms.json` | Auto Scaling and CloudWatch records of every decision |
| `cloudwatch/*.tsv` | one minute series: time, average, maximum, sum, sample count |

The raw load tool logs (about 740 MB, mostly a repeated native WebRTC
decoder warning) are not included. The statistics lines extracted from
them are.
