#!/usr/bin/env python3
"""
patch_template.py

Extends Ant Media's reference auto scaling CloudFormation template with a
ScalingMode parameter, so the cluster can scale either on CPU, as it does
today, or on the capacity weighted load index built for this dissertation.

    python3 patch_template.py STOCK.yaml -o ams-cluster-streamaware.yaml

Every change is additive except one repair (item 0 below), which removes a
misplaced block that CloudFormation never applied.

With ScalingMode left at CPU, scaling behaviour is identical to the stock
template, because the same CPU target tracking policy is the only thing
deciding capacity. The stack is not byte for byte identical: it also carries
the IAM instance profile and the metric collector, which the stock template
has neither of. That is deliberate. The collector must run during a CPU mode
run too, because its load index trace is what makes the two modes
comparable afterwards. It observes; it does not decide.

WHAT IT CHANGES, AND WHY

0. A repair to the stock template. Both auto scaling groups carry a
   NotificationConfiguration nested under UpdatePolicy, which is not a valid
   place for it. CloudFormation ignores UpdatePolicy when a stack is created,
   so the mistake is invisible at first. The first stack update that touches
   either group makes CloudFormation read UpdatePolicy, and it rejects the
   key: "Encountered unsupported property NotificationConfiguration", then
   rolls back. Observed on 30 October 2026 when a launch template change was
   applied to a running stack.

   Two consequences. No deployment of the stock template can be updated
   through a group change without a rollback. And its scaling notifications
   have never been applied: both groups of a stack built from it reported
   zero notification configurations.

   The block is removed, not moved to the group's Properties. Moving it would
   switch on email notifications that have never worked, which is a change in
   behaviour, not a repair. After removal the stack does exactly what it did
   before, and can now be updated. That matters for this design in
   particular: switching ScalingMode by stack update is how the two modes are
   meant to be compared. The NotificationTopic resource itself is left in
   place, unchanged.

1. Five new parameters: ScalingMode, LoadIndexTargetValue, ScaleInThreshold,
   CollectorUrl and DetailedMonitoring.

   DetailedMonitoring defaults to false, which is what the stock template
   does, so its CPU policy acts on five minute CPU data. Comparison runs set
   it to true on both modes, so the CPU policy gets one minute data and the
   comparison is about the signal rather than the sampling interval. A real
   deployment of the stock template would therefore do worse than the CPU
   results measured that way.

2. An IAM role and instance profile. The stock template creates no instance
   profile at all, so a node today has no permission to call PutMetricData
   and the collector could not publish anything. This is the change that
   makes the rest possible. The policy grants only PutMetricData and the
   DescribeTags needed to read the group name.

3. Group metrics collection on both auto scaling groups. The scaling policy
   divides fleet load by fleet size, and GroupDesiredCapacity is not
   published unless collection is enabled.

4. The collector installed and started by user data on every node, with
   --role origin or --role edge to match the tier. It is installed in BOTH
   modes deliberately. Running it while scaling on CPU is what produces the
   load index trace for a CPU mode run, which is what makes the comparison
   between the two modes possible at all.

5. The existing CPU policies become conditional on ScalingMode being CPU.

6. New stream aware policies, conditional on ScalingMode being StreamAware:

   Scale out, target tracking on the fleet load index. The expression is
   SUM of the per node LoadIndex divided by GroupDesiredCapacity, not the
   average of a per instance metric.

   That distinction is the single most important line in this file.
   Simulation against the measured capacity figures showed that target
   tracking on a per instance average over provisions this workload badly,
   reaching 33 nodes where 17 were needed. The cause is that a WebRTC
   session is pinned to the edge that terminates it and cannot be moved, so
   after a surge the loaded node stays loaded while new nodes sit empty and
   the average stays high. Dividing total load by total capacity gave the
   same protection for roughly half the cost.

   Dividing by GroupDesiredCapacity rather than GroupInServiceInstances
   counts nodes that are still booting, which stops the policy ordering the
   same capacity twice while it waits.

   Scale in, separate and deliberately slow. Target tracking's own scale in
   is disabled and replaced by an alarm requiring the fleet index to stay
   below the threshold for fifteen consecutive minutes. Observed on a live
   cluster: the stock CPU policy removed a node five minutes and forty five
   seconds after it was added, before it had served anything useful. A node
   takes around 106 seconds to become available and a further five minutes
   to drain on the way out, so removing one early costs more than keeping it.

   Two safety alarms, on CPU above 75 percent and on GPU encoder above 75
   percent. The index is only as good as the capacity table behind it, and
   these catch anything it has not anticipated. The GPU alarm is not
   decoration: on a g5.4xlarge the encoder saturates at twelve transcoded
   1080p streams while CPU reads 15 to 20 percent, so a CPU alarm alone
   would never fire on that hardware.
"""

import argparse
import sys


class PatchError(Exception):
    pass


def splice(text, anchor, addition, where="after", label=""):
    """Insert addition relative to anchor, failing loudly if anchor is absent."""
    count = text.count(anchor)
    if count == 0:
        raise PatchError(
            "anchor not found for %s.\nThe template may be a different "
            "version than this patch was written against.\nLooked for:\n%s"
            % (label, anchor))
    if count > 1:
        raise PatchError(
            "anchor is ambiguous for %s: found %d times. Refusing to guess."
            % (label, count))
    if where == "after":
        return text.replace(anchor, anchor + addition)
    return text.replace(anchor, addition + anchor)


def remove(text, block, label=""):
    """Delete block, failing loudly unless it occurs exactly once."""
    count = text.count(block)
    if count != 1:
        raise PatchError(
            "expected exactly one match for %s, found %d.\nLooked for:\n%s"
            % (label, count, block))
    return text.replace(block, "")


# ---------------------------------------------------------------------------
# 0. Repair: NotificationConfiguration misplaced under UpdatePolicy
#
#    The two copies are indented differently in the stock template, so each
#    is matched exactly. See item 0 in the docstring for why it is removed
#    rather than moved.
# ---------------------------------------------------------------------------

MISPLACED_NOTIFY_ORIGIN = """        NotificationConfiguration:
          TopicARN: !Ref NotificationTopic
          NotificationTypes:
            - 'autoscaling:EC2_INSTANCE_LAUNCH'
            - 'autoscaling:EC2_INSTANCE_LAUNCH_ERROR'
            - 'autoscaling:EC2_INSTANCE_TERMINATE'
            - 'autoscaling:EC2_INSTANCE_TERMINATE_ERROR'
"""

MISPLACED_NOTIFY_EDGE = """      NotificationConfiguration:
          TopicARN: !Ref NotificationTopic
          NotificationTypes:
            - 'autoscaling:EC2_INSTANCE_LAUNCH'
            - 'autoscaling:EC2_INSTANCE_LAUNCH_ERROR'
            - 'autoscaling:EC2_INSTANCE_TERMINATE'
            - 'autoscaling:EC2_INSTANCE_TERMINATE_ERROR'
"""


# ---------------------------------------------------------------------------
# 1. Parameters
# ---------------------------------------------------------------------------

ANCHOR_PARAMS = """  CPUPolicyTargetValue:
    Type: Number
    Default: 60.0
    Description: This parameter for creates new Instances when CPU load exceed to %60.
"""

NEW_PARAMS = """  ScalingMode:
    Type: String
    Default: CPU
    AllowedValues:
      - CPU
      - StreamAware
    Description: >-
      Which signal drives auto scaling. CPU keeps the stock behaviour, target
      tracking on average CPU utilisation. StreamAware scales on a capacity
      weighted load index built from stream and viewer counts, published by
      the collector running on each node. Switchable with a stack update, so
      both can be compared on the same infrastructure.
  LoadIndexTargetValue:
    Type: Number
    Default: 0.70
    Description: >-
      Target for the fleet load index when ScalingMode is StreamAware. An
      index of 1.0 means nodes are carrying exactly the load measured as
      their capacity in load testing, so 0.70 leaves headroom for the time a
      new node takes to become available.
  ScaleInThreshold:
    Type: Number
    Default: 0.35
    Description: >-
      The fleet load index must stay below this for fifteen consecutive
      minutes before a node is removed. Deliberately slow: a node costs
      around 106 seconds to add and five minutes to drain, so removing one
      early is expensive.
  CollectorUrl:
    Type: String
    Default: ''
    Description: >-
      HTTP(S) URL the nodes fetch ams_metric_collector.py from at boot. Leave
      empty to skip installing the collector, in which case StreamAware mode
      will have no metric to scale on.
  DetailedMonitoring:
    Type: String
    Default: 'false'
    AllowedValues:
      - 'true'
      - 'false'
    Description: >-
      EC2 detailed monitoring on every node, so AWS/EC2 CPUUtilization is
      published every minute instead of every five. The stock template
      leaves it off, which means its CPU policy acts on five minute data.
      Turn it on when comparing the two modes, so the comparison is about
      the signal and not the sampling interval.
"""

# ---------------------------------------------------------------------------
# 2. Conditions
# ---------------------------------------------------------------------------

ANCHOR_CONDITIONS = """Conditions:
  UseGPUImage: !Equals 
    - !Ref GPUImage
    - true
"""

NEW_CONDITIONS = """  UseCPUScaling: !Equals
    - !Ref ScalingMode
    - CPU
  UseStreamAware: !Equals
    - !Ref ScalingMode
    - StreamAware
  InstallCollector: !Not
    - !Equals
      - !Ref CollectorUrl
      - ''
"""

# ---------------------------------------------------------------------------
# 3. IAM role and instance profile
# ---------------------------------------------------------------------------

ANCHOR_IAM = """  DescribeImagesRole:
    Type: AWS::IAM::Role
"""

NEW_IAM = """  AntMediaNodeRole:
    Type: AWS::IAM::Role
    Properties:
      AssumeRolePolicyDocument:
        Version: '2012-10-17'
        Statement:
          - Action: sts:AssumeRole
            Effect: Allow
            Principal:
              Service: ec2.amazonaws.com
      Policies:
        - PolicyName: PublishScalingMetrics
          PolicyDocument:
            Version: '2012-10-17'
            Statement:
              - Effect: Allow
                Action: cloudwatch:PutMetricData
                Resource: '*'
                Condition:
                  StringEquals:
                    cloudwatch:namespace: AntMedia/Scaling
              - Effect: Allow
                Action:
                  - ec2:DescribeTags
                Resource: '*'

  AntMediaNodeInstanceProfile:
    Type: AWS::IAM::InstanceProfile
    Properties:
      Roles:
        - !Ref AntMediaNodeRole

"""

# ---------------------------------------------------------------------------
# 4. Group metrics collection. Note the two groups are indented differently
#    in the stock template, so each gets its own anchor.
# ---------------------------------------------------------------------------

ANCHOR_ORIGIN_TAGS = """        Tags:
        - Key: Name
          Value: Antmedia-Origin
          PropagateAtLaunch: 'true'
"""

NEW_ORIGIN_METRICS = """        MetricsCollection:
          - Granularity: 1Minute
            Metrics:
              - GroupDesiredCapacity
              - GroupInServiceInstances
"""

ANCHOR_EDGE_TAGS = """      Tags:
        - Key: Name
          Value: Antmedia-Edge
          PropagateAtLaunch: 'true'
"""

NEW_EDGE_METRICS = """      MetricsCollection:
        - Granularity: 1Minute
          Metrics:
            - GroupDesiredCapacity
            - GroupInServiceInstances
"""

# ---------------------------------------------------------------------------
# 5. Instance profile on both launch templates
# ---------------------------------------------------------------------------

ANCHOR_LT_ORIGIN = """        InstanceType: !Ref OriginInstanceType
        KeyName: !Ref KeyName
"""

ANCHOR_LT_EDGE = """        InstanceType: !Ref EdgeInstanceType
        KeyName: !Ref KeyName
"""

NEW_LT_PROFILE = """        IamInstanceProfile:
          Arn: !GetAtt AntMediaNodeInstanceProfile.Arn
        Monitoring:
          Enabled: !Ref DetailedMonitoring
"""

# ---------------------------------------------------------------------------
# 6. Collector install in user data.
#
#    Inside Fn::Base64 !Sub, ${X} is substituted by CloudFormation. Shell
#    variables therefore have to be written ${!X}. This block avoids shell
#    variables entirely to keep that trap out of the way.
#
#    The auto scaling group name is deliberately NOT passed in here. A launch
#    template cannot reference its own auto scaling group: the group depends
#    on the template, so the reference is circular and CloudFormation rejects
#    the stack. The collector reads its group from the
#    aws:autoscaling:groupName tag on its own instance instead, which is what
#    the ec2:DescribeTags permission in the IAM role is for.
# ---------------------------------------------------------------------------

def collector_userdata(role, group_resource=None):
    return """            # ---- stream aware scaling: metric collector ----
            # Installed in both scaling modes on purpose. Running it while
            # the stack scales on CPU is what produces the load index trace
            # for a CPU mode run, which is what makes the two modes
            # comparable.
            if [ -n "${CollectorUrl}" ]; then
              sudo apt-get -y install python3-boto3 || sudo pip3 install boto3 --break-system-packages
              sudo mkdir -p /opt/ams-collector
              sudo curl -fsSL "${CollectorUrl}" -o /opt/ams-collector/ams_metric_collector.py
              sudo chmod 755 /opt/ams-collector/ams_metric_collector.py
              sudo tee /etc/systemd/system/ams-collector.service > /dev/null <<'UNIT'
            [Unit]
            Description=Ant Media scaling metric collector
            After=antmedia.service network-online.target
            Wants=antmedia.service

            [Service]
            Type=simple
            # Without this Python buffers stdout in blocks when it is a
            # journald socket, so per cycle lines do not appear until the
            # buffer fills. Observed on the first real build: nine cycles in,
            # the journal still held one line. Errors came through, because
            # stderr is not buffered, but the routine output did not.
            Environment=PYTHONUNBUFFERED=1
            ExecStart=/usr/bin/python3 /opt/ams-collector/ams_metric_collector.py --role ROLE_PLACEHOLDER --interval 60
            Restart=always
            RestartSec=30
            User=root

            [Install]
            WantedBy=multi-user.target
            UNIT
              sudo sed -i "s|ROLE_PLACEHOLDER|%s|" /etc/systemd/system/ams-collector.service
              sudo systemctl daemon-reload
              sudo systemctl enable --now ams-collector.service || echo "ams-collector failed to start"
            fi
            # Do not let a collector failure change the exit status: cfn-signal
            # reads it next, and a missing collector must not roll back an
            # otherwise healthy cluster.
            true
""" % (role,)


ANCHOR_SIGNAL_ORIGIN = """            /usr/local/bin/cfn-signal -e $? --stack ${AWS::StackName} --resource OriginGroup --region ${AWS::Region}
"""

ANCHOR_SIGNAL_EDGE = """            /usr/local/bin/cfn-signal -e $? --stack ${AWS::StackName} --resource EdgeGroup --region ${AWS::Region}
"""

# ---------------------------------------------------------------------------
# 7. Condition on the existing CPU policies
# ---------------------------------------------------------------------------

ANCHOR_ORIGIN_CPU = """  OriginCPUPolicy:
    Type: AWS::AutoScaling::ScalingPolicy
    Properties:
"""
REPLACE_ORIGIN_CPU = """  OriginCPUPolicy:
    Type: AWS::AutoScaling::ScalingPolicy
    Condition: UseCPUScaling
    Properties:
"""

ANCHOR_EDGE_CPU = """  EdgeCPUPolicy:
    Type: AWS::AutoScaling::ScalingPolicy
    Properties:
"""
REPLACE_EDGE_CPU = """  EdgeCPUPolicy:
    Type: AWS::AutoScaling::ScalingPolicy
    Condition: UseCPUScaling
    Properties:
"""

# ---------------------------------------------------------------------------
# 8. The stream aware policies
# ---------------------------------------------------------------------------

def stream_aware_block(tier, group_ref):
    """
    Scale out, scale in and safety alarms for one tier.

    The scale out metric is SUM(LoadIndex) / GroupDesiredCapacity. Summing
    the per node index and dividing by the fleet size gives total load over
    total capacity, which is proportional to load and so makes target
    tracking's proportional sizing exact. An average of a per instance
    metric is not proportional when load is unevenly spread, which is the
    normal state of this workload because sessions cannot migrate.
    """
    return """
  {tier}LoadIndexPolicy:
    Type: AWS::AutoScaling::ScalingPolicy
    Condition: UseStreamAware
    Properties:
      AutoScalingGroupName: !Ref {group}
      PolicyType: TargetTrackingScaling
      TargetTrackingConfiguration:
        DisableScaleIn: true
        TargetValue: !Ref LoadIndexTargetValue
        CustomizedMetricSpecification:
          Metrics:
            - Id: fleetload
              Label: Sum of per node load index
              MetricStat:
                Metric:
                  Namespace: AntMedia/Scaling
                  MetricName: LoadIndex
                  Dimensions:
                    - Name: AutoScalingGroupName
                      Value: !Ref {group}
                Stat: Sum
              ReturnData: false
            - Id: fleetsize
              Label: Nodes asked for, including those still booting
              MetricStat:
                Metric:
                  Namespace: AWS/AutoScaling
                  MetricName: GroupDesiredCapacity
                  Dimensions:
                    - Name: AutoScalingGroupName
                      Value: !Ref {group}
                Stat: Average
              ReturnData: false
            - Id: fleetindex
              Label: Fleet load over fleet capacity
              Expression: fleetload / fleetsize
              ReturnData: true

  {tier}ScaleInPolicy:
    Type: AWS::AutoScaling::ScalingPolicy
    Condition: UseStreamAware
    Properties:
      AutoScalingGroupName: !Ref {group}
      PolicyType: SimpleScaling
      AdjustmentType: ChangeInCapacity
      ScalingAdjustment: -1
      Cooldown: '300'

  {tier}ScaleInAlarm:
    Type: AWS::CloudWatch::Alarm
    Condition: UseStreamAware
    Properties:
      AlarmDescription: >-
        Fleet load index has stayed low for fifteen consecutive minutes.
        Deliberately slow. A node takes around 106 seconds to become
        available and five minutes to drain, and the stock CPU policy was
        observed removing a node five minutes and forty five seconds after
        adding it.
      ComparisonOperator: LessThanThreshold
      Threshold: !Ref ScaleInThreshold
      EvaluationPeriods: 15
      DatapointsToAlarm: 15
      TreatMissingData: notBreaching
      Metrics:
        - Id: fleetload
          MetricStat:
            Metric:
              Namespace: AntMedia/Scaling
              MetricName: LoadIndex
              Dimensions:
                - Name: AutoScalingGroupName
                  Value: !Ref {group}
            Period: 60
            Stat: Sum
          ReturnData: false
        - Id: fleetsize
          MetricStat:
            Metric:
              Namespace: AWS/AutoScaling
              MetricName: GroupDesiredCapacity
              Dimensions:
                - Name: AutoScalingGroupName
                  Value: !Ref {group}
            Period: 60
            Stat: Average
          ReturnData: false
        - Id: fleetindex
          Expression: fleetload / fleetsize
          ReturnData: true
      AlarmActions:
        - !Ref {tier}ScaleInPolicy

  {tier}CPUSafetyPolicy:
    Type: AWS::AutoScaling::ScalingPolicy
    Condition: UseStreamAware
    Properties:
      AutoScalingGroupName: !Ref {group}
      PolicyType: SimpleScaling
      AdjustmentType: PercentChangeInCapacity
      ScalingAdjustment: 50
      MinAdjustmentMagnitude: 1
      Cooldown: '300'

  {tier}CPUSafetyAlarm:
    Type: AWS::CloudWatch::Alarm
    Condition: UseStreamAware
    Properties:
      AlarmDescription: >-
        CPU is high even though the load index did not ask for capacity. The
        index is only as good as the capacity table behind it, so this
        catches workloads it has not anticipated.
      Namespace: AntMedia/Scaling
      MetricName: CPUUtilization
      Dimensions:
        - Name: AutoScalingGroupName
          Value: !Ref {group}
      Statistic: Maximum
      Period: 60
      EvaluationPeriods: 3
      Threshold: 75
      ComparisonOperator: GreaterThanThreshold
      TreatMissingData: notBreaching
      AlarmActions:
        - !Ref {tier}CPUSafetyPolicy

  {tier}GPUSafetyAlarm:
    Type: AWS::CloudWatch::Alarm
    Condition: UseStreamAware
    Properties:
      AlarmDescription: >-
        GPU encoder utilisation is high. Not decoration: on a g5.4xlarge the
        encoder saturates at twelve transcoded 1080p streams while CPU reads
        15 to 20 percent, so a CPU alarm alone would never fire on that
        hardware. Published by the collector, which reads nvidia-smi.
      Namespace: AntMedia/Scaling
      MetricName: GPUEncoderUtilization
      Dimensions:
        - Name: AutoScalingGroupName
          Value: !Ref {group}
      Statistic: Average
      Period: 60
      EvaluationPeriods: 3
      Threshold: 75
      ComparisonOperator: GreaterThanThreshold
      TreatMissingData: notBreaching
      AlarmActions:
        - !Ref {tier}CPUSafetyPolicy
""".format(tier=tier, group=group_ref)


ANCHOR_OUTPUTS = "\nOutputs:\n"


def patch(text):
    steps = []

    text = remove(text, MISPLACED_NOTIFY_ORIGIN, "origin NotificationConfiguration")
    text = remove(text, MISPLACED_NOTIFY_EDGE, "edge NotificationConfiguration")
    steps.append("repaired the stock template: removed NotificationConfiguration "
                 "misplaced under UpdatePolicy on both groups")

    text = splice(text, ANCHOR_PARAMS, NEW_PARAMS, "after", "new parameters")
    steps.append("added ScalingMode, LoadIndexTargetValue, ScaleInThreshold, CollectorUrl, "
                 "DetailedMonitoring")

    text = splice(text, ANCHOR_CONDITIONS, NEW_CONDITIONS, "after", "new conditions")
    steps.append("added UseCPUScaling, UseStreamAware, InstallCollector conditions")

    text = splice(text, ANCHOR_IAM, NEW_IAM, "before", "IAM role and instance profile")
    steps.append("added AntMediaNodeRole and AntMediaNodeInstanceProfile "
                 "(the stock template had no instance profile at all)")

    text = splice(text, ANCHOR_ORIGIN_TAGS, NEW_ORIGIN_METRICS, "after",
                  "origin group metrics")
    text = splice(text, ANCHOR_EDGE_TAGS, NEW_EDGE_METRICS, "after",
                  "edge group metrics")
    steps.append("enabled 1 minute group metrics on both auto scaling groups")

    text = splice(text, ANCHOR_LT_ORIGIN, NEW_LT_PROFILE, "after",
                  "origin launch template profile")
    text = splice(text, ANCHOR_LT_EDGE, NEW_LT_PROFILE, "after",
                  "edge launch template profile")
    steps.append("attached the instance profile and the DetailedMonitoring "
                 "switch to both launch templates")

    text = splice(text, ANCHOR_SIGNAL_ORIGIN,
                  collector_userdata("origin"), "before",
                  "origin collector install")
    text = splice(text, ANCHOR_SIGNAL_EDGE,
                  collector_userdata("edge"), "before",
                  "edge collector install")
    steps.append("installed the collector from user data on both tiers, "
                 "in both scaling modes")

    if ANCHOR_ORIGIN_CPU not in text or ANCHOR_EDGE_CPU not in text:
        raise PatchError("could not find the stock CPU scaling policies")
    text = text.replace(ANCHOR_ORIGIN_CPU, REPLACE_ORIGIN_CPU)
    text = text.replace(ANCHOR_EDGE_CPU, REPLACE_EDGE_CPU)
    steps.append("made the stock CPU policies conditional on ScalingMode=CPU")

    block = (stream_aware_block("Origin", "OriginGroup")
             + stream_aware_block("Edge", "EdgeGroup"))
    text = splice(text, ANCHOR_OUTPUTS, block, "before", "stream aware policies")
    steps.append("added load index target tracking, slow scale in, and CPU "
                 "and GPU safety alarms for both tiers")

    return text, steps


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("stock", help="the stock Ant Media template")
    ap.add_argument("-o", "--out", default="ams-cluster-streamaware.yaml")
    args = ap.parse_args()

    with open(args.stock) as fh:
        original = fh.read()

    try:
        patched, steps = patch(original)
    except PatchError as exc:
        print("PATCH FAILED\n\n%s" % exc, file=sys.stderr)
        return 1

    with open(args.out, "w") as fh:
        fh.write(patched)

    print("wrote %s" % args.out)
    print("  %d lines in, %d lines out, %d added"
          % (original.count("\n"), patched.count("\n"),
             patched.count("\n") - original.count("\n")))
    print()
    for i, s in enumerate(steps, 1):
        print("  %d. %s" % (i, s))
    print()
    print("The only removal is the misplaced NotificationConfiguration, which")
    print("CloudFormation never applied. With ScalingMode=CPU the scaling")
    print("behaviour is identical to the stock template: the same CPU target")
    print("tracking policy is the only thing deciding capacity. The stack also")
    print("carries the instance profile and the collector, which only observe.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
