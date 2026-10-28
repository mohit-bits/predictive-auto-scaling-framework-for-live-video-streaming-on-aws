# Cluster findings, 27 October 2026

Observations on a live two tier Ant Media Server cluster, made to settle
whether the counts the collector reads are node-local or cluster-wide, and
to time a scale-out. Everything here was observed, not taken from
documentation. Times are UTC.

The cluster was deleted after the tests.

## Setup

- **Software and hardware:** AMS 3.1.0 Enterprise, build 20260831_1442.
  Origin and edge on c5.xlarge (4 vCPU), MongoDB on m5.large, region
  ap-south-1, application `LiveApp`.
- **Publisher:** ffmpeg 4.4.2 on a c5.large test harness in the edge subnet,
  `-c copy` over RTMP to the origin's private IP. The source was 1080p30
  H.264 High at a 2000k target, measured at about 2.07 to 2.08 Mbit/s, with
  AAC 128k audio.
- **Test file:** the HLS and REST tests used a file with B-frames
  (`has_b_frames=2`). From 18:45 on, a file with none (`has_b_frames=0`,
  confirmed with ffprobe).
- **WebRTC load tool:** Ant Media's `webrtc-load-test-tool-2.8.3-SNAPSHOT.zip`,
  sha256 `873fa27c7159475e187763883efa928a7fa857ee7f2fbd434070434cab5ce1a3`,
  run on OpenJDK 17.0.20.1.

## Methods

Each value below refers to these by code.

- **[R]** Application REST API, over SSH to `127.0.0.1:5080`, no
  authentication. From any other address it returns 403.
- **[J]** Root REST API (`/rest/v2/system-resources` and
  `/rest/v2/live-clients-size`): an HS256 JWT expiring after 300 s, signed on
  each node with its own `server.jwtServerSecretKey` and sent as a
  `ProxyAuthorization` header. Without it, the response is 403.
- **[N]** Network byte counters from
  `/sys/class/net/ens5/statistics/{tx,rx}_bytes`, differenced over a 10 s
  window.
- **[C]** CPU from `/proc/stat` busy/total deltas; the AMS Java process from
  utime+stime in `/proc/<pid>/stat`.
- **[S]** Socket tables from `ss -tn`.
- **[T]** The load tool's `StatManager` lines.
- **[A]** AWS API timestamps and settings.

## 1. Node-local vs cluster-wide

One stream on the origin, no viewers, at 18:18:

| Field | Origin | Edge | Verdict |
|---|---|---|---|
| `broadcasts/count`, `active-live-stream-count` [R] | 1 | 1 | **cluster-wide** |
| `broadcasts/list` [R] | 1 record | the same record (the edge's copy about 10 s newer) | **cluster-wide** |
| `totalLiveStreamSize` [J] | 1 | 1 | **cluster-wide** |
| `localLiveStreams` [J] | 1 | 0 | node-local |
| `totalConnectionSize` [J] | 1 | 0 | node-local, counts the ingest connection |

The edge had no established TCP connection on 1935 or 5000 [S], so it was
not carrying the stream. It was reporting a database record.

## 2. Publisher lifecycle

18:29 to 18:30, read 15 s after each change [J].

- **Publisher stopped:** every `local*` field on the origin went to 0.
- **Publisher restarted, no viewers:** the origin's `localWebRTCViewers` came
  back to **1**. It follows the publisher, not viewers.

## 3. HLS viewers on the edge

ffmpeg viewers on the harness pulling from the edge's private IP [J].

- **1 viewer:** edge `localHLSViewers` 1, origin 1.
- **4 viewers:** edge 4, **origin 1**. The origin counts the edge as one
  client.
- **Edge `localLiveStreams` stayed 0 throughout.** The edge log shows
  `HttpLiveStreamValve` fetching every `.m3u8` and `.ts` from the origin over
  HTTP.
- **Back to 0 after the viewers stopped:** the edge between 13 and 20 s; the
  origin between 20 and 27 s (sampled every 7 s).

## 4. WebRTC playback on the edge

[J][T]. The load tool reported 0 dropped connections and a 33 ms frame
period (30 fps) in every stage.

| Stage | Edge `localWebRTCViewers` | Edge `localWebRTCLiveStreams` | Edge `localLiveStreams` | Origin `localWebRTCViewers` |
|---|---|---|---|---|
| A: none | 0 | 0 | 0 | 1 |
| B: 5 at once | **5** | **1** | 0 | 1 |
| C: 20 | **20** | 1 | 0 | 1 |
| D: stopped | 0 within **5 s or less** (first sample at +5 s) | 0 | 0 | 1 |

Also measured:

- **Edge send rate** [N]: 11,741 kbit/s at 5 viewers and 46,598 kbit/s at 20,
  about **2.33 to 2.35 Mbit/s per viewer**.
- **CPU at 20 viewers:** the edge's AMS Java process 51.7 percent of one
  core, the origin's 5.5 percent (one `top` sample each).
- **Harness at 20 viewers** [C]: CPU 16 percent of 2 vCPU, memory 502 MB of
  3,794 MB. Not saturated, so it was not the limit.
- **Network path:** media flowed between private IPs even though the cluster
  security group's only UDP rule was 4200. The application's WebRTC port
  range is 50000 to 60000, read from the settings [J]. Why it worked was not
  established.

## 5. Origin send bandwidth per edge

[N][S], 10 s windows. The origin received about 2.2 to 2.3 Mbit/s from the
publisher throughout.

| Edge viewers | How they arrived | Origin tx (kbit/s) | :5000 connections |
|---|---|---|---|
| 0 | none | 76, 76, 79 (also 73 and 78 on other runs) | 0 |
| 1 → 2 → 5 | one at a time (19:01 to 19:02) | 2,431 / 2,430 / 2,433 | 3 |
| 5 | all at once from zero (19:12) | 2,414 / 2,432 | 3 |
| 10 | all at once from zero (19:19) | 2,454 / 2,432 | 3 |
| 11 → 1 | the 10 left, 1 remained | 2,428 / 2,435 | 3 |
| 5 (stage B) | all at once from zero (18:46) | **11,838** | **15** |
| 20 (stage C) | 15 added to B's 5 | **11,880** | **15** |

**What can be cited:** one pull per edge, about 2.4 Mbit/s, regardless of
viewer count or how viewers arrive. The exception is a single unreproduced
5x case (stage B), in which five connections each carried 2.08 Mbit/s and
five more 0.24 Mbit/s. **"A flash crowd multiplies origin bandwidth" is not
supported:** it did not reproduce in 2 attempts.

## 6. Counting a node's own streams by `originAdress`

[R][J]. The cluster-wide broadcast list, filtered to records whose
`originAdress` equals the node's own private IP.

- **1 stream:** origin 1 match, `localLiveStreams` 1; edge 0, 0.
- **2 streams (19:10:55):** origin 2 = 2; edge 0 = 0.
- **After a stream stopped:** its record was gone within 10 s.

## 7. Transcoding

One 720p rendition at 2 Mbit/s, 19:24 to 19:30.

- **Propagation:** the setting change reached the edge within 10 s [J].
- **It applies only to streams started after the change.** The running
  stream stayed plain (1 percent CPU) until the publisher restarted.
- **The broadcast record changed only in time and counter fields.**
  `encoderSettingsList` stayed `null` and `encoderQueueSize` 0.
  `localLiveStreams` and `system-resources` did not change either. **No API
  field shows that a stream is transcoded.**
- **Evidence on the node:** `teststream_720p2000kbps.m3u8`/`.ts` and
  `teststream_adaptive.m3u8` appear in `webapps/LiveApp/streams/`, and the
  log shows `StreamAdaptor` lines.
- **Origin CPU** [C], 60 s windows:
  - plain stream: 1 percent system, AMS Java 5 percent of one core
  - transcoded: **15 percent** system, **64 percent** of one core
  - plain again (30 s window): 1 percent system
  - The 1 percent baseline is rounded to a whole number, so a cost ratio
    cannot be computed precisely. It is also 720p, not the 1080p behind the
    standalone transcoding figures.
- **Side effect of changing settings through the API:** `dbId` in
  `red5-web.properties` changed on both nodes, and the two nodes ended up
  with different values (blank on the origin). Left as it was.

## 8. Scale-out timing: partial, still open

**Run 1:** scale-out request at T0 = 19:38:50.8. Times marked AWS are AWS's
own record; the rest are when the change was observed.

| Milestone | When | How measured |
|---|---|---|
| API returned | +1.3 s | [A] local timer |
| Desired capacity changed | 19:38:53 (+2 s) | [A] scaling-activity `Cause` (AWS) |
| Instance started | 19:38:56 (+5 s) | [A] scaling-activity `Cause` (AWS) |
| LaunchTime | 19:38:58 (+7 s) | [A] EC2 (AWS) |
| `running` | between +7 and +12 s | [A] waiter polling every 15 s |
| **ALB target healthy** | **between +101 and +106 s** | [A] polled every 5 s. The check is `GET /`, every 30 s, 3 successes needed, HTTP 200 |
| EC2 status checks passed | between about +187 and +202 s | [A] waiter polling every 15 s |
| AMS app API answering on 5080 | **not measured** | the probe was invalid (403 from off-host) |
| Joined the cluster, served a viewer | **not measured** | instance scaled in first |

What happened after:

- **Scale-in:** `AlarmLow` fired at 19:44:36 (+5 min 45 s). The instance was
  selected for termination at 19:44:44 and reached terminated at 19:49:46,
  5 min 2 s later. That is consistent with
  **`deregistration_delay.timeout_seconds = 300`, read from the target group
  config** [A].
- **Settings read from config** [A]: ASG `HealthCheckType=EC2`, grace 0, no
  lifecycle hooks, slow start 0. `AlarmLow` triggers below 42 percent CPU for
  15 × 60 s periods, and it had been in ALARM since 17:55:54.

**Run 2** (20:00:29): the API call returned at +1.2 s, but **no instance
launched**, and desired capacity went back to 1. The most likely cause is
the stock policy resetting it while its low CPU alarm stayed in ALARM; there
is no record of the exact moment. The test harness was then terminated at
20:04:07, so the run was not continued.

**For the simulator:** the only defensible number is **ALB-healthy at about
101 to 106 s after the decision**, from one sample. That is the earliest
point a viewer coming through the load balancer could be routed to the new
edge, but it does not prove the edge could serve a stream then. The
simulator's 300 s is neither confirmed nor ruled out.

## Not established

- Edge-to-edge `local*` comparison with viewers on both edges.
- Time to the first served viewer.
- An API field that identifies transcoding.
- The 1080p transcoding cost ratio.
- What the AMS dashboard showed at stage C.
