#!/usr/bin/env python3
"""
ams_metric_collector.py  (version 2)

Publishes a capacity weighted load index from an Ant Media Server node to
Amazon CloudWatch, so an auto scaling group can scale on how full a node
actually is rather than on CPU.

Run it ON the AMS node, as root.

    python3 ams_metric_collector.py --role edge --once --dry-run
    python3 ams_metric_collector.py --role origin --once
    python3 ams_metric_collector.py --role edge

WHY VERSION 2
-------------
Version 1 read stream and viewer counts from the application REST API
(/{app}/rest/v2/broadcasts/...). That works on a standalone server but is
wrong on a cluster. Cluster testing on 27 October 2026 showed those endpoints
are backed by the shared MongoDB and return CLUSTER WIDE figures even when
called directly on a node's own address. An idle edge reported the origin's
stream as if it were carrying it.

Observed, one stream published to the origin, no viewers:

    broadcasts/count          origin 1    edge 1     <- cluster wide
    broadcasts/list           origin 1    edge 1     <- cluster wide
    totalLiveStreamSize       origin 1    edge 1     <- cluster wide
    localLiveStreams          origin 1    edge 0     <- NODE LOCAL
    totalConnectionSize       origin 1    edge 0     <- NODE LOCAL

The edge had no established TCP connection carrying the stream, confirming it
was reporting a database record rather than its own work.

Version 2 therefore reads the local* fields from /rest/v2/system-resources.

AUTHENTICATION
--------------
/rest/v2/system-resources needs a server JWT, even from 127.0.0.1. The secret
is already on the node, in /usr/local/antmedia/conf/red5.properties as
server.jwtServerSecretKey. This collector reads that file and signs its own
short lived token.

That means no credential is ever distributed to the fleet, nothing is stored
in the repository, and there is nothing to rotate across instances. The node
authenticates to itself with a secret it already holds.

The token must be sent in the ProxyAuthorization header. Authorization does
not work.

WHICH FIELD MEANS WHAT
----------------------
Confirmed by observation on a live cluster, not from documentation:

  localLiveStreams          streams INGESTED on this node. The origin tier
                            signal. Stays 0 on an edge even while the edge
                            serves viewers.

  localWebRTCViewers        on an EDGE, exactly the WebRTC viewers that node
                            is terminating. Read 5 with 5 connected and 20
                            with 20. Returns to 0 within 5 seconds of the
                            last viewer leaving.

                            on the ORIGIN this is NOT a viewer count. It
                            reads 1 per published stream and never moved when
                            viewers were added anywhere. It follows the
                            publisher. Never feed it into an origin index.

  localWebRTCLiveStreams    distinct streams this node is DELIVERING over
                            WebRTC. Went 0 to 1 on the edge when it began
                            serving and back to 0 afterwards.

  localHLSViewers           node local, but the origin counts each EDGE as a
                            single HLS client. With 4 viewers on one edge the
                            edge read 4 and the origin read 1. Correct per
                            node. Never sum this across the cluster.

  totalLiveStreamSize       cluster wide. Do not use.

TRANSCODING
-----------
No API field says whether a stream is transcoded. The broadcast record is
identical for a plain and a transcoded stream apart from counters that move
anyway, and encoderSettingsList was null in every case observed.

Transcoding in Ant Media is an APPLICATION level setting, not a per stream
one. So the question is not "is this stream transcoded" but "does this app
transcode, and into how many renditions". The collector reads the app's
encoderSettings once per cycle and applies the resulting weight to every
stream in that app.

Known limitation: a settings change applies only to streams that start after
it, so streams already running when an operator changes the rendition set
will be weighted wrongly until they restart. Observed directly: enabling a
720p rendition did not affect the running stream until the publisher was
restarted.

CAPACITY FIGURES
----------------
Every number in CAPACITY below comes from the load tests run for this
dissertation. None is estimated.

A cluster side check supports them. On a c5.xlarge edge, 20 WebRTC viewers at
1080p put the AMS process at 51.7 percent of one core, about 12.9 percent of
a 4 vCPU box. The standalone table says 105 viewers at roughly 72 percent,
which predicts 13.8 percent for 20. Close enough that the standalone capacity
figures appear to carry over to cluster edges.

Requires: python3, boto3, and credentials with cloudwatch:PutMetricData.
"""

import argparse
import hashlib
import hmac
import json
import base64
import os
import re
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request

NAMESPACE = "AntMedia/Scaling"
RED5_PROPERTIES = "/usr/local/antmedia/conf/red5.properties"
DEFAULT_PORT = 5080
TOKEN_LIFETIME_S = 300


# ---------------------------------------------------------------------------
# Capacity table, from the load tests.
#
# INGEST     plain streams per node, 1080p, no transcoding, at 60-70% CPU
# PLAYBACK   WebRTC viewers per node, by rendition height, at 70-75% CPU
# ABR_CPU    streams per node when transcoding on a CPU instance, keyed by
#            the number of renditions enabled
# ABR_GPU    streams per node when transcoding on a GPU instance. The limit
#            is the single hardware encoder, so it is the SAME on every g5
#            size tested. This is why a CPU target never fires on the larger
#            ones: at 12 streams a g5.4xlarge reads 15-20% CPU while its
#            encoder is at 70-75%.
# ---------------------------------------------------------------------------

INGEST = {
    "c5.xlarge": 35, "c5.2xlarge": 75, "c5.4xlarge": 150, "c5.9xlarge": 350,
}

PLAYBACK = {
    "c5.xlarge":  {1080: 105, 720: 120, 480: 160, 360: 170},
    "c5.2xlarge": {1080: 200, 720: 220, 480: 300, 360: 310},
    "c5.4xlarge": {1080: 380, 720: 430, 480: 560, 360: 570},
    "c5.9xlarge": {1080: 760, 720: 830, 480: 1100, 360: 1150},
}

# renditions enabled -> streams per node
ABR_CPU = {
    "c5.xlarge":  {1: 2, 2: 1, 3: 1, 4: 1},
    "c5.2xlarge": {1: 3, 2: 2, 3: 2, 4: 2},
    "c5.4xlarge": {1: 4, 2: 2, 3: 2, 4: 2},
    "c5.9xlarge": {1: 6, 2: 4, 3: 4, 4: 4},
}

ABR_GPU = {1: 12, 2: 11, 3: 9, 4: 8}

# Fallbacks for an instance type that was never load tested. Deliberately
# the smallest tested values, so an unknown type is treated as the weakest
# node rather than silently assumed to be large.
FALLBACK_INGEST = 35
FALLBACK_PLAYBACK = 105


def is_gpu_instance(instance_type):
    """
    Decide from the INSTANCE TYPE, never from whether nvidia-smi answers.

    Version 1 had this wrong. It picked the GPU capacity whenever nvidia-smi
    responded, so a CPU instance that happened to have drivers installed
    weighted a transcoded stream at 12 instead of 2, understating its load by
    a factor of six.
    """
    return bool(instance_type) and instance_type.split(".")[0].startswith("g")


def abr_capacity(instance_type, renditions):
    renditions = max(1, min(4, renditions))
    if is_gpu_instance(instance_type):
        return ABR_GPU.get(renditions, ABR_GPU[4])
    table = ABR_CPU.get(instance_type)
    if not table:
        return max(1, FALLBACK_INGEST // 17)   # the measured 35 -> 2 collapse
    return table.get(renditions, table[4])


# ---------------------------------------------------------------------------
# JWT, signed locally from the node's own secret
# ---------------------------------------------------------------------------

def _b64(raw):
    return base64.urlsafe_b64encode(raw).rstrip(b"=")


def read_jwt_secret(path=RED5_PROPERTIES):
    """Read server.jwtServerSecretKey out of red5.properties."""
    try:
        with open(path) as fh:
            for line in fh:
                line = line.strip()
                if line.startswith("#") or "=" not in line:
                    continue
                key, _, value = line.partition("=")
                if key.strip() == "server.jwtServerSecretKey":
                    return value.strip()
    except IOError as exc:
        raise SystemExit(
            "cannot read %s (%s). This must run on the AMS node as root."
            % (path, exc))
    raise SystemExit(
        "server.jwtServerSecretKey not found in %s. The management API needs "
        "it, and server.jwtServerControlEnabled must be true." % path)


def make_token(secret, lifetime_s=TOKEN_LIFETIME_S):
    """An HS256 token carrying only an exp claim, which is what AMS wants."""
    header = _b64(json.dumps({"alg": "HS256", "typ": "JWT"},
                             separators=(",", ":")).encode())
    payload = _b64(json.dumps({"exp": int(time.time()) + lifetime_s},
                              separators=(",", ":")).encode())
    signing_input = header + b"." + payload
    signature = _b64(hmac.new(secret.encode(), signing_input,
                              hashlib.sha256).digest())
    return (signing_input + b"." + signature).decode()


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------

def get_json(url, token=None, timeout=5):
    req = urllib.request.Request(url)
    if token:
        # ProxyAuthorization, not Authorization. Authorization is ignored.
        req.add_header("ProxyAuthorization", token)
    req.add_header("Accept", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        if exc.code == 403:
            raise SystemExit(
                "403 from %s. If this is a management endpoint the JWT was "
                "rejected: check server.jwtServerControlEnabled is true and "
                "that the secret matches. If it is an application endpoint, "
                "it only accepts 127.0.0.1." % url)
        raise
    except Exception as exc:
        print("warning: %s failed: %s" % (url, exc), file=sys.stderr)
        return None


# ---------------------------------------------------------------------------
# Instance identity
# ---------------------------------------------------------------------------

def imds(path, timeout=2):
    """IMDSv2. The cluster template requires tokens, so IMDSv1 will fail."""
    try:
        req = urllib.request.Request(
            "http://169.254.169.254/latest/api/token", method="PUT")
        req.add_header("X-aws-ec2-metadata-token-ttl-seconds", "60")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            token = resp.read().decode()
        req = urllib.request.Request(
            "http://169.254.169.254/latest/meta-data/" + path)
        req.add_header("X-aws-ec2-metadata-token", token)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read().decode()
    except Exception:
        return None


def discover_asg_name(region):
    """
    Read this node's auto scaling group from its own EC2 tags.

    The group name cannot be passed in from the CloudFormation template: a
    launch template referencing its own auto scaling group is circular,
    because the group depends on the template. So the node looks itself up
    instead, using the aws:autoscaling:groupName tag that EC2 applies to
    every instance an auto scaling group launches.

    This is what the ec2:DescribeTags permission in the node role is for.
    """
    instance_id = imds("instance-id")
    if not instance_id:
        return None
    try:
        import boto3
        ec2 = boto3.client("ec2", **({"region_name": region} if region else {}))
        resp = ec2.describe_tags(Filters=[
            {"Name": "resource-id", "Values": [instance_id]},
            {"Name": "key", "Values": ["aws:autoscaling:groupName"]},
        ])
        tags = resp.get("Tags") or []
        return tags[0]["Value"] if tags else None
    except Exception as exc:
        print("warning: could not read the auto scaling group tag: %s" % exc,
              file=sys.stderr)
        return None


def local_ipv4():
    """
    This node's own private IP, read fresh every cycle.

    Never cache or configure this. These nodes are launched by an auto
    scaling group, so a replacement gets a different address, and a stale
    value would make the originAdress filter silently return zero streams.
    """
    ip = imds("local-ipv4")
    if ip:
        return ip
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(("10.255.255.255", 1))
        return sock.getsockname()[0]
    finally:
        sock.close()


# ---------------------------------------------------------------------------
# Node readings
# ---------------------------------------------------------------------------

def cpu_percent(sample_s=1.0):
    def read():
        with open("/proc/stat") as fh:
            parts = [float(x) for x in fh.readline().split()[1:]]
        idle = parts[3] + (parts[4] if len(parts) > 4 else 0.0)
        return sum(parts), idle
    total0, idle0 = read()
    time.sleep(sample_s)
    total1, idle1 = read()
    dt, di = total1 - total0, idle1 - idle0
    return 0.0 if dt <= 0 else max(0.0, min(100.0, 100.0 * (dt - di) / dt))


def gpu_percent():
    """(gpu utilisation, encoder utilisation), or (None, None) without a GPU."""
    try:
        out = subprocess.run(
            ["nvidia-smi",
             "--query-gpu=utilization.gpu,utilization.encoder",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=5)
        if out.returncode != 0:
            return None, None
        gpu, enc = out.stdout.strip().split("\n")[0].split(",")
        return float(gpu), float(enc)
    except Exception:
        return None, None


def system_resources(port, token):
    return get_json("http://127.0.0.1:%d/rest/v2/system-resources" % port,
                    token=token)


def list_applications(port, token):
    data = get_json("http://127.0.0.1:%d/rest/v2/applications" % port,
                    token=token)
    if isinstance(data, dict):
        return data.get("applications") or []
    return []


def rendition_count(app, port, token):
    """
    How many ABR renditions this application produces.

    Transcoding is configured per application, so this weight applies to
    every stream in the app. Zero means plain relay.
    """
    data = get_json(
        "http://127.0.0.1:%d/rest/v2/applications/settings/%s" % (port, app),
        token=token)
    if not isinstance(data, dict):
        return 0
    settings = data.get("encoderSettings")
    if isinstance(settings, list):
        return len(settings)
    if isinstance(settings, str) and settings.strip():
        try:
            return len(json.loads(settings))
        except ValueError:
            return 0
    return 0


def streams_ingested_here(app, port, my_ip):
    """
    Streams this node ingested, from the cluster wide list filtered by
    originAdress.

    The list itself is cluster wide, but every record carries the private IP
    of the node that ingested it, so filtering on this node's own address
    makes it node local. Verified: with two streams on the origin the filter
    returned 2 there and 0 on the edge, matching localLiveStreams on both.

    status is checked as well, because a node that dies uncleanly can leave a
    record behind still marked broadcasting.
    """
    url = ("http://127.0.0.1:%d/%s/rest/v2/broadcasts/list/0/500"
           % (port, app))
    data = get_json(url)          # application API, no token needed on loopback
    if not isinstance(data, list):
        return 0
    return sum(1 for b in data
               if b.get("originAdress") == my_ip
               and b.get("status") == "broadcasting")


# ---------------------------------------------------------------------------
# The load index
# ---------------------------------------------------------------------------

def origin_index(apps, instance_type, port, token, my_ip):
    """
    Origin tier. Load is the streams this node ingests, each weighted by what
    its application costs.

    A transcoded stream is enormously more expensive than a plain one. On a
    c5.xlarge the measured collapse is 35 plain streams to 2 with a single
    1080p rendition, so one transcoded stream weighs about 17 plain ones.
    An index that ignored this would read 0.57 on a node that is already
    over capacity.
    """
    capacity = INGEST.get(instance_type, FALLBACK_INGEST)
    index = 0.0
    total_streams = 0
    transcoded = 0
    detail = {}

    for app in apps:
        n = streams_ingested_here(app, port, my_ip)
        if not n:
            continue
        total_streams += n
        renditions = rendition_count(app, port, token)
        if renditions:
            effective = abr_capacity(instance_type, renditions)
            transcoded += n
        else:
            effective = capacity
        index += n / float(effective)
        detail[app] = {"streams": n, "renditions": renditions,
                       "capacity": effective}

    return index, total_streams, transcoded, detail


def edge_index(resources, instance_type, height=1080):
    """
    Edge tier. Load is the viewers this node is terminating.

    HLS viewers are counted against the same capacity figure as WebRTC. That
    is an approximation: the playback load tests measured WebRTC, which is
    what this index is built on, and no equivalent HLS capacity was measured.
    HLS is also a cheaper protocol per viewer, so treating an HLS viewer as a
    WebRTC viewer overstates the load slightly. Erring toward overstatement is
    the safe direction for a scaling signal.
    """
    table = PLAYBACK.get(instance_type, PLAYBACK["c5.xlarge"])
    capacity = table.get(height, FALLBACK_PLAYBACK)

    webrtc = int(resources.get("localWebRTCViewers") or 0)
    hls = int(resources.get("localHLSViewers") or 0)
    dash = int(resources.get("localDASHViewers") or 0)
    relayed = int(resources.get("localWebRTCLiveStreams") or 0)

    viewers = webrtc + hls + dash
    return viewers / float(capacity), viewers, webrtc, hls, relayed, capacity


# ---------------------------------------------------------------------------
# Publishing
# ---------------------------------------------------------------------------

def put_metrics(client, metrics, group, instance_id):
    """
    Each metric is published twice, once against the instance and once
    against the group.

    The per instance series is what a scaling policy aggregates with a metric
    math SUM. That matters: simulation showed that target tracking on a plain
    per instance AVERAGE badly over provisions this workload, because a
    WebRTC session cannot move to another node. After a surge the loaded node
    stays loaded while new nodes sit empty, the average stays high, and the
    policy keeps adding capacity. Driving the policy from total fleet load
    over total fleet capacity instead gave the same protection for about half
    the cost.
    """
    now = time.gmtime()
    base = [{"Name": "AutoScalingGroupName", "Value": group}]
    per_instance = base + [{"Name": "InstanceId", "Value": instance_id}]

    data = []
    for name, value, unit in metrics:
        if value is None:
            continue
        for dims in (per_instance, base):
            data.append({
                "MetricName": name,
                "Dimensions": dims,
                "Value": float(value),
                "Unit": unit,
                "Timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", now),
            })

    for i in range(0, len(data), 20):
        client.put_metric_data(Namespace=NAMESPACE, MetricData=data[i:i + 20])
    return len(data)


# ---------------------------------------------------------------------------
# One cycle
# ---------------------------------------------------------------------------

def collect(args, token):
    my_ip = local_ipv4()
    resources = system_resources(args.port, token)
    if resources is None:
        raise SystemExit("no response from system-resources on 127.0.0.1")

    instance_type = args.instance_type or imds("instance-type") or "c5.xlarge"
    gpu_util, gpu_enc = gpu_percent()

    metrics = [("CPUUtilization", cpu_percent(), "Percent")]
    if gpu_util is not None:
        metrics.append(("GPUUtilization", gpu_util, "Percent"))
        metrics.append(("GPUEncoderUtilization", gpu_enc, "Percent"))

    report = {"role": args.role, "instance_type": instance_type, "ip": my_ip}

    if args.role == "origin":
        apps = [args.app] if args.app else list_applications(args.port, token)
        index, streams, transcoded, detail = origin_index(
            apps, instance_type, args.port, token, my_ip)

        # cross check against the field AMS reports directly
        reported = int(resources.get("localLiveStreams") or 0)
        if streams != reported:
            print("warning: originAdress filter counted %d streams but "
                  "localLiveStreams reports %d. Possible stale record or an "
                  "app not in the list." % (streams, reported),
                  file=sys.stderr)

        metrics += [
            ("LoadIndex", index, "None"),
            ("StreamCount", streams, "Count"),
            ("ABRStreamCount", transcoded, "Count"),
            ("LocalLiveStreams", reported, "Count"),
        ]
        report.update({"load_index": round(index, 4), "streams": streams,
                       "transcoded": transcoded, "apps": detail,
                       "localLiveStreams": reported})
    else:
        index, viewers, webrtc, hls, relayed, capacity = edge_index(
            resources, instance_type, args.height)
        metrics += [
            ("LoadIndex", index, "None"),
            ("ViewerCount", viewers, "Count"),
            ("WebRTCViewerCount", webrtc, "Count"),
            ("HLSViewerCount", hls, "Count"),
            ("RelayedStreamCount", relayed, "Count"),
        ]
        report.update({"load_index": round(index, 4), "viewers": viewers,
                       "webrtc": webrtc, "hls": hls, "relayed": relayed,
                       "capacity": capacity})

    return metrics, report


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--role", choices=["origin", "edge"], required=True,
                   help="which tier this node is. The two use different "
                        "signals: an origin's load is the streams it "
                        "ingests, an edge's is the viewers it terminates.")
    p.add_argument("--app", help="limit to one application (origin only)")
    p.add_argument("--port", type=int, default=DEFAULT_PORT)
    p.add_argument("--height", type=int, default=1080,
                   help="rendition height served, for the edge capacity table")
    p.add_argument("--interval", type=int, default=60,
                   help="seconds between cycles; 60 matches CloudWatch")
    p.add_argument("--group",
                   help="auto scaling group name. Normally omitted: the "
                        "collector reads it from its own instance tags.")
    p.add_argument("--instance-type")
    p.add_argument("--region")
    p.add_argument("--once", action="store_true")
    p.add_argument("--dry-run", action="store_true",
                   help="print, publish nothing")
    args = p.parse_args()

    secret = read_jwt_secret()

    client = None
    instance_id = imds("instance-id") or socket.gethostname()
    region = args.region or imds("placement/region")

    # Explicit --group wins. Otherwise look it up from this instance's own
    # tags. Falling back to "standalone" keeps the collector usable on a
    # single server outside any auto scaling group.
    group = args.group or discover_asg_name(region) or "standalone"
    if group == "standalone" and not args.dry_run:
        print("warning: no auto scaling group found. Metrics will publish "
              "under the group name 'standalone' and no scaling policy will "
              "read them.", file=sys.stderr)

    if not args.dry_run:
        import boto3
        client = boto3.client("cloudwatch",
                              **({"region_name": region} if region else {}))

    token_expires = 0
    token = None

    while True:
        if time.time() > token_expires - 30:
            token = make_token(secret)
            token_expires = time.time() + TOKEN_LIFETIME_S

        metrics, report = collect(args, token)

        if args.dry_run:
            print(json.dumps(report, indent=2))
            for name, value, _ in metrics:
                print("  %-24s %s" % (name, value))
        else:
            count = put_metrics(client, metrics, group, instance_id)
            print("%s published %d datapoints, LoadIndex=%.4f"
                  % (time.strftime("%H:%M:%S"), count, report["load_index"]))

        if args.once:
            return
        sleep_to_next_boundary(args.interval)


def sleep_to_next_boundary(interval):
    """
    Sleep until the next multiple of interval on the wall clock.

    Sleeping a fixed interval AFTER the work made each cycle interval plus
    the time the work took, about 61 s. The cycles drifted later until one
    straddled a CloudWatch minute and that minute got no datapoint. Observed
    on 30 October 2026: two missing minutes out of about 66 on one edge.
    A missing minute is not harmless for StreamAware mode, because the fleet
    SUM for that minute is short by one node.

    Starting every cycle at :00 keeps one datapoint in every minute, however
    long the work takes. If a cycle overruns a whole interval, the boundary it
    missed is skipped rather than run twice, so a minute is never doubled.
    """
    now = time.time()
    time.sleep((int(now // interval) + 1) * interval - now)


if __name__ == "__main__":
    main()
