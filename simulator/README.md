# Scaling policy simulator

Compares a CPU driven auto scaling policy against a load index driven policy
for an Ant Media Server edge tier, using capacity measured in the load tests
run for this dissertation.

## Why this is offline

The expensive measurement is already done. The five load test workbooks
established how much each instance type can actually carry. What remains is a
question about control rather than capacity: given that a node holds N
viewers, does a policy driven by CPU or by the load index add nodes at the
right time.

That question needs the capacity numbers plus a model of CloudWatch alarm and
instance boot behaviour. Running it offline covers many demand patterns
instead of the two or three that would fit the AWS budget. The live AWS run
then confirms that the implemented policy behaves on real infrastructure the
way the model says.

## Files

    measured_data.py     capacity and CPU response data, with the source cell
                         for every number
    simulator.py         the cluster, policy and CloudWatch model
    run_experiments.py   the three scenarios and the reporting
    results/             CSV time series and a chart per scenario

Run with `python3 run_experiments.py`.

## What is measured and what is modelled

Measured, traceable to a cell in a workbook:

* node capacity per instance type, protocol and resolution
* the CPU response curve for WebRTC publish at several load levels, from
  column F of the Without ABR sheet
* CPU and GPU encoder readings at saturation on every g5 size

Modelled, each with a stated assumption:

| Assumption | Value | Basis |
|---|---|---|
| CloudWatch metric period | 60 s | AWS default |
| Datapoints to scale out | 3 | AWS target tracking default |
| Datapoints to scale in | 15 | AWS target tracking default |
| Instance boot to in service | 300 s | EC2 launch, Ant Media start, cluster join |
| Mean viewer session | 20 min | assumption, needs a sensitivity run |
| Playback CPU curve shape | straight line | see below |
| Session mobility | none | a WebRTC session is pinned to its edge |

### The playback CPU curve

The playback workbook recorded only the capacity point, at 70 to 75 percent
CPU, without the intermediate readings the publisher sheet has. The shape
between idle and capacity therefore has to be chosen.

The straight line is used, and it is the conservative choice. The publisher
data shows the real curve is convex: on a c5.xlarge at 1080p a straight line
predicts 36 percent CPU at 20 streams where the measured value is 32.5. A
straight line makes CPU rise sooner than it really does, which makes the CPU
policy react sooner than it really would. Any advantage the load index shows
here would be larger in reality.

### Session stickiness

A WebRTC session is terminated by one edge node and cannot be moved. When a
new node joins, nothing already connected migrates to it. The new node fills
only through natural churn as sessions end and new ones arrive.

This is why adding capacity late is expensive in this workload. The capacity
arrives, but the overloaded node stays overloaded until its own sessions age
out, which takes about one mean session length.

## Results

| Scenario | Policy | Cost $ | Shortfall | Under provisioned |
|---|---|---|---|---|
| Gradual ramp | CPU 60% | 5.31 | 1249 viewer-min | 19.8 min |
| Gradual ramp | Index 0.70 fleet | 5.68 | 525 viewer-min | 12.3 min |
| Flash crowd | CPU 60% | 3.26 | 19755 viewer-min | 27.7 min |
| Flash crowd | Index 0.70 per node mean | 8.66 | 6780 viewer-min | 7.7 min |
| Flash crowd | Index 0.70 fleet | 4.66 | 6780 viewer-min | 7.7 min |
| GPU transcode | CPU 60% | 3.99 * | 2632 stream-min | 79.7 of 110 min |
| GPU transcode | Index 0.70 fleet | 25.51 * | 22 stream-min | 7.7 min |

\* The g5 prices have NOT been verified for ap-south-1 and are carried over
from another region. The c5 figures above them were corrected on 27 October
2026; an earlier version of this file quoted ap-southeast-1 prices by
mistake. Do not quote the two starred figures until the g5 prices are
checked. The shortfall and under provisioned columns do not depend on price
and are unaffected.

Shortfall is demand the in service fleet had no room for at all. It separates
a policy's own failure from the unavoidable cost of boot time, and it is the
column that shows what a low instance count actually cost.

### Three findings

**1. On a gradual ramp the gain is modest.** Seven percent more spend for
fifty eight percent less shortfall. Worth having, not dramatic. Reported as
measured rather than dressed up.

**2. On a GPU tier the CPU policy never fires at all.** Twelve transcoded
1080p streams fill the A10G encoder on a g5.4xlarge while CPU reads 15 to 20
percent. Past that the encoder cannot do more work, so CPU does not climb
either. Across 110 simulated minutes the CPU policy took zero scaling
actions, held one node throughout, and spent 79.7 minutes short of capacity
while the worst node ran at five times its tested limit. This is not a policy
that reacts late. It is a policy that cannot react.

**3. Averaging the index across nodes over provisions badly.** A plain
CloudWatch target tracking policy averages the per instance metric. With
sticky sessions, the node that absorbed a surge stays loaded while new nodes
sit empty, so the average stays high and the policy keeps scaling out. In the
flash crowd it reached 33 nodes where 17 were needed.

Driving the policy from total fleet load over total fleet capacity, built with
a CloudWatch metric math expression over the SUM of the per node load, gives
exactly the same protection at 46 percent lower cost. This changes the
CloudFormation design: the target tracking policy should use a metric math
expression, not a plain per instance average.

## Still to do

* sensitivity runs on mean session length and boot time
* a mixed workload scenario, plain and transcoded streams on one tier
* re-check the ap-south-1 on demand prices before the final report
* one live AWS run to confirm the implemented policy matches this model
