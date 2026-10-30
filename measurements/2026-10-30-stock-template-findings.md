# Stock template findings, 30 October 2026

Two properties of Ant Media's stock auto scaling template
(`cloudformation/antmedia-aws-autoscale-template.yaml`), found while
deploying the patched version. Both were observed on a live stack in
ap-south-1, not inferred from documentation. Times are UTC.

## 1. NotificationConfiguration is misplaced, so the stack cannot be updated

Both auto scaling groups nest `NotificationConfiguration` under
`UpdatePolicy`:

```yaml
      UpdatePolicy:
        AutoScalingRollingUpdate:
          MinInstancesInService: '1'
          MaxBatchSize: '1'
          PauseTime: PT15M
          WaitOnResourceSignals: 'true'
        NotificationConfiguration:          # stock line 570 (origin), 645 (edge)
          TopicARN: !Ref NotificationTopic
```

`UpdatePolicy` is not a place this key is valid.

**What was observed.**

- Stack creation succeeded. CloudFormation does not read `UpdatePolicy`
  when it creates a stack, so the mistake is invisible at first.
- At 16:55:02 a stack update changed the launch templates (a new
  `CollectorUrl` and detailed monitoring). Both launch templates updated,
  then `OriginGroup` failed at 16:55:16 with
  `Encountered unsupported property NotificationConfiguration`, and the stack
  rolled back to `UPDATE_ROLLBACK_COMPLETE` at 16:55:22.
- On that stack, `describe-notification-configurations` returned **zero**
  notification configurations for both groups.

**Consequences.**

- No deployment of the stock template can be updated through a change to
  either auto scaling group without a rollback. That includes changing the
  instance type, the maximum size, or anything in the launch templates.
- The stock template's scaling notifications have never been applied. The
  SNS topic and its email subscription are created, but nothing publishes to
  them.

**Why it matters here.** The deliverable's claim is that the CPU and
StreamAware modes can be switched by a stack update and compared on the same
infrastructure. That requires the stack to be updatable.

**Repair.** `patch_template.py` removes the block from both groups (item 0 in
its docstring). It is removed, not moved to the group's `Properties`: moving
it would enable email notifications that have never worked, which is a change
in behaviour rather than a repair. The `NotificationTopic` resource is left as
it is.

## 2. Detailed monitoring is off, so the CPU policy acts on five minute data

The stock launch templates set no `Monitoring`, so EC2 publishes
`AWS/EC2 CPUUtilization` every five minutes. The stock CPU target tracking
policy therefore acts on five minute data. On the stack above,
`Monitoring.State` was `disabled` on all three instances.

The patched template adds a `DetailedMonitoring` parameter, default `false`
to match the stock template. Comparison runs set it to `true` in **both**
modes, so the CPU policy acts on one minute data and the comparison is about
the signal rather than the sampling interval.

A real deployment of the stock template, with monitoring left off, would
therefore react more slowly than the CPU-mode results measured in the
comparison runs.
