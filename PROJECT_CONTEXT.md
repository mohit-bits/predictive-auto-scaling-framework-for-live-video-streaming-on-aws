# Project context

Working reference for the implementation. Captures the measured figures,
the decisions taken and the questions still open, so the design rationale
is recorded alongside the code rather than only in the dissertation.

M.Tech dissertation, BITS Pilani WILP. Final submission 10 November 2026.

Title: Predictive Auto-Scaling Framework for Live Video Streaming on AWS.

## What the project is

Ant Media Server runs on AWS behind an auto scaling group. The stock
CloudFormation template scales on average CPU with a 60 percent target.

The claim is that CPU is the wrong signal for this workload, and that a
capacity weighted load index built from stream and viewer counts is a better
one. The end deliverable is the CloudFormation template extended with a
`ScalingMode` parameter taking `CPU` or `StreamAware`, so the two can be
switched by a stack update and compared.

## Cluster topology, and why it matters

Streams are PUBLISHED to the origin. They are PLAYED from the edges. An edge
does not hold a stream until a viewer asks for it there, at which point the
edge pulls it from the origin.

The two tiers therefore carry different work and need different capacity
tables:

* Origin: ingest, plus transcoding when enabled. Its load comes from the
  number of streams being published to it.
* Edge: playback. Its load has two parts, the viewers it is terminating AND
  the number of distinct streams it has pulled from the origin to serve them.
  An edge holding 10 streams with 10 viewers each is not carrying the same
  load as one holding 1 stream with 100 viewers.

The playback load tests measured viewers against a single stream, so the
figure of 105 viewers on a c5.xlarge already includes the relay cost of one
stream. The per-stream term on the edge is not yet measured and the index
does not yet account for it.

## The load index

    I = sum over stream kinds of  n(kind) / C(instance_type, kind)

`n` is how many of that kind the node is carrying. `C` is how many the load
tests said that instance type can hold. `I = 1.0` means the node is at its
tested capacity. The scaling target is 0.70.

Worked example, c5.xlarge: 20 plain 1080p RTMP streams (C=35) plus 1 stream
being transcoded (C=2) gives 0.57 + 0.50 = 1.07. The node is already past
capacity while CPU would still look comfortable.

## Measured capacity, 1080p, at 60 to 75 percent CPU

Plain relay, streams per node:

| instance | RTMP | RTSP+audio | SRT+audio | WebRTC publish | WebRTC playback |
|---|---|---|---|---|---|
| c5.xlarge | 35 | 40 | 42 | 35-40 | 105 viewers |
| c5.2xlarge | 75 | 85 | 86 | - | 200 viewers |
| c5.4xlarge | 150 | 170 | >=100 | 150 | 380 viewers |
| c5.9xlarge | 350 | 360 | >=100 | 350 | 760 viewers |

The SRT figures for c5.4xlarge and c5.9xlarge were capped by the test harness
at 100. They are lower bounds, not saturation points. Never treat them as
measured capacity.

Transcoding collapses this. On c5.xlarge, 35 plain RTMP streams becomes 2 with
a single 1080p rendition and 1 with four renditions.

GPU instances are bound by the hardware encoder, not the CPU. RTMP with one
1080p rendition stops at 12 streams on g5.xlarge, g5.2xlarge, g5.4xlarge and
g5.8xlarge alike, because they all carry one NVIDIA A10G. At that point CPU
reads 62, 45, 17 and 17 percent while the GPU encoder reads 65, 65, 72 and 72.
A 60 percent CPU target can never fire on g5.4xlarge or g5.8xlarge.

## The collector

`ams_metric_collector.py` runs on each node as a systemd service, started by
user data, with `--role origin` or `--role edge`. It finds its own auto
scaling group from the `aws:autoscaling:groupName` tag on its instance, reads
CPU from /proc/stat and GPU from nvidia-smi, computes the load index from the
capacity table hard coded at the top, and publishes to the CloudWatch
namespace `AntMedia/Scaling` under two dimension sets, one per instance and
one per group.

It reads its counts from `/rest/v2/system-resources`, authenticated with a
short lived HS256 JWT that the node signs from its own
`server.jwtServerSecretKey` in `/usr/local/antmedia/conf/red5.properties`.
The token goes in the `ProxyAuthorization` header; `Authorization` is
ignored. No credential is distributed to the fleet and nothing is stored in
the repository: each node authenticates to itself with a secret it already
holds.

The two tiers use different signals, because they carry different work. An
origin's load is the streams it ingests, weighted by whether its application
transcodes. An edge's load is the viewers it is terminating.

The nvidia-smi weighting bug from version 1 is fixed. Capacity is now chosen
from the instance type, never from whether nvidia-smi happens to respond.

## Open questions

All of these were settled by observation on a live two tier cluster on
27 October 2026. None of it came from documentation.

1. **Settled, and the answer was the awkward one.** The broadcast endpoints
   (`/{app}/rest/v2/broadcasts/...`) are cluster-wide even when called
   directly on a node's own address, because they read the shared MongoDB.
   With one stream published to the origin and nothing watching, both the
   origin and an idle edge reported `count = 1`. The edge had no TCP
   connection carrying the stream, so it was reporting a database record
   rather than its own work.

   The `local*` fields in `/rest/v2/system-resources` ARE node-local:

   | field | means | verified |
   |---|---|---|
   | `localLiveStreams` | streams INGESTED here | origin 1, edge 0 |
   | `localWebRTCViewers` | on an edge, viewers it terminates | read 5 with 5, 20 with 20 |
   | `localWebRTCLiveStreams` | distinct streams delivered over WebRTC here | edge 0 to 1 when serving |
   | `localHLSViewers` | HLS viewers here | edge 4, origin 1 with 4 on one edge |
   | `totalLiveStreamSize` | cluster-wide. Do not use | 1 on both nodes |

   Two traps. On the ORIGIN, `localWebRTCViewers` is not a viewer count: it
   reads 1 per published stream and never moved when viewers were added
   anywhere. And the origin counts each EDGE as a single HLS client, so
   `localHLSViewers` must never be summed across the cluster.

2. **Settled.** Yes, an edge reports only the viewers it is terminating.
   Confirmed with the Ant Media WebRTC load test tool: 5 viewers read 5, 20
   read 20, and the count returned to 0 within 5 seconds of the last viewer
   leaving. HLS took 13 to 20 seconds. The dashboard agreed with the API.

3. **Partially settled.** From the scale-out decision, the ALB marked a new
   edge healthy between 101 and 106 seconds, which is the first moment a
   viewer arriving through the load balancer could be routed to it. EC2
   status checks passed at around 200 seconds but are not on the critical
   path. Time to a first served viewer was not measured. Treat 106 seconds
   as a floor, not a confirmed figure.

4. **No API field identifies a transcoded stream.** The broadcast record is
   identical for plain and transcoded apart from counters that move anyway,
   and `encoderSettingsList` was null in every case. Transcoding in AMS is an
   APPLICATION level setting, so the collector reads the app's
   `encoderSettings` once and applies that weight to every stream in the app.
   A settings change only affects streams that start after it.

5. **Counting a node's own streams works.** Filtering the cluster-wide
   broadcast list by `originAdress` equal to the node's own private IP gave
   2 on the origin and 0 on the edge with two streams live, matching
   `localLiveStreams` on both. The IP must be read fresh each cycle: these
   are auto scaling group instances and a replacement gets a new address.

## Measured on the cluster, 27 October 2026

Cite these rather than estimating. AMS 3.1.0 Enterprise, c5.xlarge origin and
edge, ap-south-1.

* An edge sends about 2.33 Mbit/s per WebRTC viewer at 1080p, steady at both
  5 and 20 viewers.
* An origin sends about 2.4 Mbit/s per edge, ONE copy of the stream,
  regardless of how many viewers that edge has or whether they arrive
  together or one at a time. One unreproduced observation showed five copies;
  it did not recur in four further runs and should not be cited.
* 20 WebRTC viewers put the edge's AMS process at 51.7 percent of one core,
  about 12.9 percent of a 4 vCPU box. The standalone capacity table predicts
  13.8 percent for 20 of 105 viewers at 72 percent. The standalone figures
  therefore appear to carry over to cluster edges.
* Enabling one 720p rendition took origin CPU from 1 percent to 15 percent
  system, and the AMS process from 5 to 64 percent of one core. The 1 percent
  baseline is rounded, so no precise ratio can be drawn from this.
* An application settings change reached the other node within 10 seconds.
* The stock CPU policy removed a newly added edge 5 minutes 45 seconds after
  adding it, then took a further 5 minutes to drain and terminate. On a
  second attempt it reverted the capacity change before any instance
  launched, because its low CPU alarm had been in ALARM throughout. This is
  the thrashing the slow scale-in rule exists to prevent, and it was observed
  without anyone setting out to test for it.

## Simulator result that changes the template

Target tracking on a plain per instance average over provisions badly here,
because WebRTC sessions are pinned to the edge that terminates them. After a
surge the loaded node stays loaded while new nodes sit empty, the average
stays high, and the policy keeps scaling out. It reached 33 nodes where 17
were needed.

The policy must therefore use a CloudWatch metric math expression over the
SUM of per node load divided by fleet capacity, not a plain average of a per
instance metric.

## Working rules

* Read-only discovery first. Do not change cluster state without being asked.
* The AWS account is Ant Media's, provided for this dissertation. Not
  billed personally. Still treat the budget as small: prefer the smallest
  instances that answer the question, say what something will cost before
  creating it, and always say what is still running at the end of a session.
* Never invent a number. Every capacity figure traces to a load test
  spreadsheet. If something is unknown, say it is unknown.
* Explain changes in plain language. This is examined work and has to be
  defended in a viva by the author, so nothing should land in the repository
  that he cannot explain himself.
* Region is ap-south-1. The capacity figures come from standalone servers;
  the field semantics and cluster measurements come from a two tier cluster
  tested on 27 October 2026. A cluster side check supports the standalone
  figures carrying over: 20 WebRTC viewers on a c5.xlarge edge used about
  12.9 percent of the box, against 13.8 percent predicted by the standalone
  table. What remains untested is behaviour with more than one edge, and the
  deployed template itself, which has not yet been built on real
  infrastructure.
