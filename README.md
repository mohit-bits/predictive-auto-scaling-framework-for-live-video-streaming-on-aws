# Predictive Auto-Scaling Framework for Live Video Streaming on AWS

M.Tech dissertation, BITS Pilani WILP.

Ant Media Server (AMS) is commonly run on AWS behind an auto scaling group.
Ant Media's reference CloudFormation template scales that group on average
CPU utilisation, with a 60 percent target.

This project argues that CPU is the wrong signal for live video. A node can
be at or past the load it can actually carry while its CPU still looks
comfortable, especially when streams are transcoded or when transcoding runs
on a GPU. The project replaces CPU with a **capacity weighted load index**
built from stream and viewer counts:

    I = sum over stream kinds of  n(kind) / C(instance_type, kind)

`n` is how many streams or viewers of that kind the node is carrying. `C` is
how many of that kind load testing showed the instance type can hold.
`I = 1.0` means the node is at its tested capacity. The scaling target is
0.70.

Worked example on a c5.xlarge: 20 plain 1080p RTMP streams (C = 35) plus one
transcoded stream (C = 2) gives 0.57 + 0.50 = 1.07. The node is already past
capacity, but its CPU would still look comfortable.

The deliverable is the stock CloudFormation template extended with a
`ScalingMode` parameter, `CPU` or `StreamAware`, so the two policies can be
switched with a stack update and compared on the same infrastructure.

## About the capacity figures

The capacity table hard coded in `ams_metric_collector.py` comes from load
testing of Ant Media Server on standalone EC2 instances. **The load test
results themselves are not included in this repository.**

## Repository layout

| Path | What it holds |
|---|---|
| `ams_metric_collector.py` | Runs on each AMS node. Reads node-local stream and viewer counts, CPU and GPU, computes the load index, and publishes it to CloudWatch. |
| `cloudformation/patch_template.py` | Takes Ant Media's stock auto scaling template and writes a patched copy with the `ScalingMode` parameter and the stream aware policies added. |
| `cloudformation/ams-cluster-streamaware.yaml` | The patched template that `patch_template.py` produces. |
| `cloudformation/antmedia-aws-autoscale-template.yaml` | Ant Media's stock auto scaling template, the input to `patch_template.py`. |
| `simulator/` | Offline model comparing the CPU policy with the load index policy, using the measured capacity figures. Its own README explains the model, assumptions and results. `results/` holds the CSV time series and charts. |
| `measurements/` | Observations from a live two tier cluster, including the tests that showed which AMS counts are node-local. |
| `PROJECT_CONTEXT.md` | Working reference: the measured figures, the decisions taken and the questions still open. |

## The collector

`ams_metric_collector.py` runs on each AMS node as root, normally as a
systemd service. Each cycle (60 seconds by default) it:

1. Reads the node-local counters from AMS's `/rest/v2/system-resources`
   over 127.0.0.1. That endpoint needs a server JWT, so the collector signs a
   short lived token itself using `server.jwtServerSecretKey` from
   `/usr/local/antmedia/conf/red5.properties` on the same node. No credential
   is distributed to the fleet and none is stored in this repository.
2. Reads CPU from `/proc/stat` and, if present, GPU and encoder utilisation
   from `nvidia-smi`.
3. Computes the load index from the capacity table at the top of the file.
4. Publishes to the CloudWatch namespace `AntMedia/Scaling`, with dimensions
   `AutoScalingGroupName` and `InstanceId`.

Why node-local counters: on a cluster, the application REST endpoints
(`/{app}/rest/v2/broadcasts/...`) are backed by the shared database and
return cluster wide totals on every node, which is useless for a per node
index. The docstring at the top of the collector records what was observed.

### Requirements

* Python 3 and `boto3`
* AWS credentials allowing `cloudwatch:PutMetricData`, and
  `ec2:DescribeTags` so the collector can read its auto scaling group name
  from its own instance tags. On EC2 the instance profile created by the patched
  template provides these.
* `server.jwtServerControlEnabled=true` in AMS's `red5.properties`.

### Running it

Try it first without publishing anything:

    sudo python3 ams_metric_collector.py --role edge --once --dry-run

`--role` is `origin` or `edge`. The two tiers use different signals: an
origin's load is the streams it ingests, an edge's is the viewers it
terminates.

Publish once, then run continuously:

    sudo python3 ams_metric_collector.py --role origin --once
    sudo python3 ams_metric_collector.py --role origin

Other options: `--interval` (seconds between cycles), `--height` (rendition
height served, used by the edge capacity table), `--app` (limit an origin to
one application), `--group`, `--instance-type` and `--region` (normally
discovered from instance metadata). Run with `--help` for details.

Outside an auto scaling group the collector publishes under the group name
`standalone`, which no scaling policy reads.

## Applying the patch to the stock template

`patch_template.py` takes Ant Media's reference auto scaling CloudFormation
template as input. A copy of the stock template, as published in Ant Media's
documentation, is included as `cloudformation/antmedia-aws-autoscale-template.yaml`
so the patch can be reproduced:

    cd cloudformation
    python3 patch_template.py antmedia-aws-autoscale-template.yaml -o ams-cluster-streamaware.yaml

The output is byte for byte identical to the committed
`ams-cluster-streamaware.yaml`.

The script prints each change it made. All the changes are additions. With
`ScalingMode=CPU` the stack behaves as the stock template does. In summary:

* New parameters: `ScalingMode`, `LoadIndexTargetValue` (default 0.70),
  `ScaleInThreshold` (default 0.35) and `CollectorUrl`.
* An IAM role and instance profile, so nodes can call `PutMetricData`.
* Group metrics collection on both auto scaling groups.
* User data that downloads the collector from `CollectorUrl` and installs it
  as the `ams-collector` systemd service with the right `--role`. It is
  installed in both modes, so a CPU mode run still records the load index
  for comparison.
* The existing CPU policies, made conditional on `ScalingMode=CPU`.
* Stream aware policies, conditional on `ScalingMode=StreamAware`:
  * Scale out by target tracking on a CloudWatch metric math expression,
    **SUM of per node LoadIndex divided by GroupDesiredCapacity**, rather
    than a plain per instance average. WebRTC sessions are pinned to the
    edge that terminates them. After a surge the loaded node stays loaded
    while new nodes sit empty, so a plain average stays high and keeps
    scaling out. In simulation it reached 33 nodes where 17 were needed.
  * A separate, deliberately slow scale in: the fleet index must stay below
    `ScaleInThreshold` for fifteen consecutive minutes.
  * Safety alarms on CPU and GPU encoder utilisation above 75 percent.

When creating or updating the stack, set `CollectorUrl` to a URL the nodes
can fetch the collector from, for example this repository's raw file URL.
If it is left empty the collector is not installed, and StreamAware mode
has no metric to scale on.

**For comparison runs, pin `CollectorUrl` to a commit hash, not `main`.**
Each node downloads the collector when it boots. A URL pointing at `main`
gives whatever is on `main` at that moment, so a node launched after a push
would run different code from the nodes launched before it. Use the form

    https://raw.githubusercontent.com/mohit-bits/predictive-auto-scaling-framework-for-live-video-streaming-on-aws/<commit-hash>/ams_metric_collector.py

and record the hash with the results of each run.

## The simulator

See [`simulator/README.md`](simulator/README.md) for the model, its
assumptions and the results. In short:

    cd simulator
    pip install matplotlib
    python3 run_experiments.py    # three scenarios, writes results/
    python3 sensitivity.py        # sweeps session length and boot time

`measured_data.py` holds the capacity figures the simulator uses, each
traced to the load test workbook it came from. The workbooks themselves are
not included.

## Status

* The collector has not yet been deployed to a cluster through the patched
  template.
* The time from a scale out decision to an edge node accepting traffic has
  not been fully measured. One sample put the new edge healthy behind the
  load balancer 101 to 106 seconds after the decision; see
  `measurements/`.
