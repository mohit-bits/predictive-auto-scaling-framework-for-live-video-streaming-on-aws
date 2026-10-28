"""
A scaling policy simulator for an Ant Media Server edge tier.

What this does
--------------
It replays a demand pattern through an auto scaling group twice, once under a
CPU target tracking policy and once under a load index target tracking policy,
and records what each one cost and how much load was served on an overloaded
node.

Why simulate rather than run every comparison on AWS
----------------------------------------------------
The expensive measurement, which is how much a node of each type can actually
carry, is already done. Those load tests were run on real instances for this
dissertation. What is left is a question about control, not about capacity:
given that a node of this type holds N viewers, does a policy driven by CPU or
a policy driven by the load index add nodes at the right moment.

That question only needs the capacity numbers plus a model of how CloudWatch
alarms and instance boot times behave. Running it offline means many demand
patterns can be covered instead of the two or three that would fit in the AWS
budget, and the one live run on AWS is then spent confirming that the policy
behaves on real infrastructure the way the model says it does.

What is measured and what is modelled
-------------------------------------
Measured, from the workbooks:
  node capacity for each instance type, protocol and resolution
  the CPU response curve for WebRTC publish, at several load levels
  CPU and GPU encoder readings at saturation on g5 instances

Modelled, with the assumption stated in each case:
  CloudWatch metric period and alarm breach counts, set to the AWS defaults
  instance boot plus Ant Media start plus cluster registration time
  viewer sessions are sticky, so an existing viewer stays on its node when a
    new node joins and only new arrivals land on the new node
  load is sent to the least loaded in service node
"""

import math
from dataclasses import dataclass, field
from typing import Callable, List, Optional

from measured_data import (
    INSTANCE_PRICE_PER_HOUR,
    interpolate,
)


# ---------------------------------------------------------------------------
# Node profile: what one instance of a given type can carry, and what its
# sensors read at a given load
# ---------------------------------------------------------------------------

@dataclass
class NodeProfile:
    """
    Describes one instance type running one kind of workload.

    capacity is the load at which the measured tests said the node was full.
    A load index of 1.0 means the node is carrying exactly that load.

    cpu_curve and gpu_curve are lists of (load, percent) measured points.
    gpu_curve is None for CPU only instances.
    """
    name: str
    instance_type: str
    workload: str
    capacity: float
    cpu_curve: list
    gpu_curve: Optional[list] = None
    unit: str = "viewers"

    # True when something other than the CPU binds first, as on a GPU node
    # where the hardware encoder saturates. Past that point extra streams
    # cannot be encoded, so they generate no further CPU work and the CPU
    # reading flattens out instead of continuing to climb. Modelling this
    # correctly matters: it is the difference between a CPU policy that
    # reacts late and one that never reacts at all.
    cpu_saturates: bool = False

    def cpu_at(self, load: float) -> float:
        if self.cpu_saturates and load > self.capacity:
            value = interpolate(self.cpu_curve, self.capacity)
        else:
            value = interpolate(self.cpu_curve, load)
        return max(0.0, min(100.0, value))

    def gpu_at(self, load: float) -> float:
        if self.gpu_curve is None:
            return 0.0
        return max(0.0, min(100.0, interpolate(self.gpu_curve, load)))

    def index_at(self, load: float) -> float:
        return load / self.capacity if self.capacity else 0.0


# ---------------------------------------------------------------------------
# Scaling policy
# ---------------------------------------------------------------------------

@dataclass
class Policy:
    """
    A target tracking scaling policy.

    metric is the name of the signal the policy watches:
      "cpu"    the CPU utilisation reported by the instance
      "index"  the capacity weighted load index published by the collector
      "gpu"    the GPU encoder utilisation

    The alarm behaviour follows the AWS target tracking defaults: the metric
    is published once a minute, scale out needs three consecutive breaching
    minutes, scale in needs fifteen. After any action the policy waits for the
    cooldown before acting again.
    """
    name: str
    metric: str
    target: float
    breach_out_datapoints: int = 3
    breach_in_datapoints: int = 15
    cooldown_s: int = 300
    min_nodes: int = 1
    max_nodes: int = 40

    # How the group level number is formed from the per node readings.
    #
    #   "mean_per_node"  what a plain CloudWatch target tracking policy does:
    #                    average the per instance metric across the group.
    #
    #   "fleet_total"    total load divided by total fleet capacity, built in
    #                    CloudWatch with a metric math expression over the
    #                    SUM of the per node load.
    #
    # These are the same number when load is spread evenly. They are not the
    # same when it is not, and in this workload it often is not, because a
    # WebRTC session cannot be moved to a different edge once it is
    # established. After a surge the node that absorbed it stays loaded while
    # the new nodes sit empty, so the per node average stays high long after
    # enough capacity has been added. A policy reading that average keeps
    # scaling out and badly over provisions.
    aggregate: str = "mean_per_node"

    # optional safety alarm, used by the load index policy to catch anything
    # the index model has not anticipated
    safety_metric: Optional[str] = None
    safety_threshold: float = 75.0


@dataclass
class ClusterConfig:
    profile: NodeProfile
    policy: Policy
    boot_time_s: int = 300       # EC2 launch, Ant Media start, cluster join
    metric_period_s: int = 60
    tick_s: int = 10
    drain_grace_s: int = 600     # how long a node is given to empty out
    mean_session_s: int = 1200   # mean viewer session length, 20 minutes


# ---------------------------------------------------------------------------
# Instance state
# ---------------------------------------------------------------------------

PENDING = "pending"
INSERVICE = "inservice"
DRAINING = "draining"


@dataclass
class Node:
    state: str
    boot_remaining_s: int = 0
    load: float = 0.0
    drain_remaining_s: int = 0
    uptime_s: int = 0


# ---------------------------------------------------------------------------
# The simulation
# ---------------------------------------------------------------------------

@dataclass
class Result:
    policy_name: str
    profile_name: str
    times: List[int] = field(default_factory=list)
    demand: List[float] = field(default_factory=list)
    served: List[float] = field(default_factory=list)
    nodes_inservice: List[int] = field(default_factory=list)
    nodes_total: List[int] = field(default_factory=list)
    mean_cpu: List[float] = field(default_factory=list)
    mean_index: List[float] = field(default_factory=list)
    mean_gpu: List[float] = field(default_factory=list)
    max_index: List[float] = field(default_factory=list)

    # summary figures
    instance_minutes: float = 0.0
    cost_usd: float = 0.0
    overload_unit_minutes: float = 0.0   # load carried beyond tested capacity
    shortfall_unit_minutes: float = 0.0  # demand the cluster had no room for
    underprovisioned_s: int = 0          # time with less capacity than demand
    peak_index: float = 0.0
    scale_out_events: int = 0
    scale_in_events: int = 0
    first_action_s: Optional[int] = None
    unit: str = "viewers"

    def summary(self) -> dict:
        return {
            "policy": self.policy_name,
            "profile": self.profile_name,
            "instance_minutes": round(self.instance_minutes, 1),
            "cost_usd": round(self.cost_usd, 3),
            f"overload_{self.unit}_minutes": round(self.overload_unit_minutes, 1),
            f"shortfall_{self.unit}_minutes": round(self.shortfall_unit_minutes, 1),
            "underprovisioned_min": round(self.underprovisioned_s / 60.0, 1),
            "peak_index": round(self.peak_index, 3),
            "scale_out_events": self.scale_out_events,
            "scale_in_events": self.scale_in_events,
            "first_action_s": self.first_action_s,
        }


def simulate(config: ClusterConfig,
             demand_fn: Callable[[int], float],
             duration_s: int,
             start_nodes: int = 1) -> Result:
    """
    Run one demand pattern through one policy.

    demand_fn(t) returns the concurrent load wanted at time t, in whatever
    unit the node profile uses: viewers for a playback tier, streams for an
    ingest tier.
    """
    prof = config.profile
    pol = config.policy

    nodes: List[Node] = [Node(state=INSERVICE) for _ in range(start_nodes)]

    res = Result(policy_name=pol.name, profile_name=prof.name, unit=prof.unit)

    breach_out_run = 0
    breach_in_run = 0
    cooldown_remaining = 0
    seconds_since_metric = 0

    for t in range(0, duration_s, config.tick_s):
        dt = config.tick_s

        # --- advance node lifecycle -------------------------------------
        for n in nodes:
            n.uptime_s += dt
            if n.state == PENDING:
                n.boot_remaining_s -= dt
                if n.boot_remaining_s <= 0:
                    n.state = INSERVICE
            elif n.state == DRAINING:
                n.drain_remaining_s -= dt

        # a draining node leaves once it is empty or its grace period is up
        nodes = [n for n in nodes
                 if not (n.state == DRAINING and (n.load <= 0 or n.drain_remaining_s <= 0))]

        serving = [n for n in nodes if n.state == INSERVICE]
        accepting = serving  # draining nodes take no new load

        # --- place demand -----------------------------------------------
        #
        # Two things happen each tick.
        #
        # First, natural churn. A live audience is not a fixed set of people.
        # Viewers leave and others join all the time, so over one mean session
        # length the whole audience turns over even if the headline number
        # never moves. The mean session length is the key parameter here and
        # is stated with the results.
        #
        # Second, the demand trace is followed. Whatever is needed on top of
        # the churn is added, or removed if the audience is shrinking.
        #
        # Arrivals always go to the least loaded node that is in service.
        # Nothing that is already connected ever moves, because a WebRTC
        # session is pinned to the edge that terminates it. This is why a new
        # node fills up gradually rather than instantly, and it is the reason
        # scaling late is expensive in this workload: the capacity arrives,
        # but the overloaded node stays overloaded until its own sessions age
        # out.
        want = max(0.0, demand_fn(t))

        # churn: a slice of every node's audience ends its session
        churn_fraction = min(1.0, dt / config.mean_session_s)
        for n in nodes:
            n.load = max(0.0, n.load - n.load * churn_fraction)

        current = sum(n.load for n in nodes)
        delta = want - current

        if delta > 0 and accepting:
            # spread arrivals in small slices so they fill the emptiest nodes
            # first rather than all landing on one
            slices = 20
            step = delta / slices
            for _ in range(slices):
                target_node = min(accepting, key=lambda n: prof.index_at(n.load))
                target_node.load += step
        elif delta < 0:
            # audience shrinking faster than churn alone; take the difference
            # off every node in proportion to what it holds
            leaving = -delta
            total = sum(n.load for n in nodes)
            if total > 0:
                for n in nodes:
                    n.load = max(0.0, n.load - leaving * (n.load / total))

        served_now = sum(n.load for n in serving) + sum(
            n.load for n in nodes if n.state == DRAINING)

        # --- read sensors -----------------------------------------------
        if serving:
            cpus = [prof.cpu_at(n.load) for n in serving]
            idxs = [prof.index_at(n.load) for n in serving]
            gpus = [prof.gpu_at(n.load) for n in serving]
        else:
            cpus, idxs, gpus = [0.0], [0.0], [0.0]

        mean_cpu = sum(cpus) / len(cpus)
        mean_index = sum(idxs) / len(idxs)
        mean_gpu = sum(gpus) / len(gpus)
        peak_index_now = max(idxs)

        # --- record -------------------------------------------------------
        res.times.append(t)
        res.demand.append(want)
        res.served.append(served_now)
        res.nodes_inservice.append(len(serving))
        res.nodes_total.append(len(nodes))
        res.mean_cpu.append(mean_cpu)
        res.mean_index.append(mean_index)
        res.mean_gpu.append(mean_gpu)
        res.max_index.append(peak_index_now)

        res.peak_index = max(res.peak_index, peak_index_now)

        # instance minutes: every node that exists is billed, including ones
        # that are still booting, because EC2 bills from launch
        res.instance_minutes += len(nodes) * dt / 60.0

        # load carried above the tested capacity of its node
        for n in nodes:
            over = max(0.0, n.load - prof.capacity)
            res.overload_unit_minutes += over * dt / 60.0

        # Capacity shortfall: how far the in service fleet was from being able
        # to hold the demand at all.
        #
        # This separates a policy's own failure from the unavoidable cost of
        # boot time. A policy that runs the fleet permanently too small looks
        # cheap on the instance minutes column, and this is the column that
        # shows what that saving actually cost.
        fleet_capacity = len(serving) * prof.capacity
        shortfall = max(0.0, want - fleet_capacity)
        res.shortfall_unit_minutes += shortfall * dt / 60.0
        if shortfall > 0:
            res.underprovisioned_s += dt

        # --- CloudWatch sampling and alarm evaluation ---------------------
        #
        # This follows how AWS target tracking actually behaves:
        #
        #  * the metric is aggregated once a minute across the group
        #  * instances still warming up are counted at the target value
        #    rather than at their real reading, which stops the group from
        #    over provisioning while it waits for new nodes to come up
        #  * the new group size is current_desired * metric / target
        #  * scale out is not blocked by a cooldown; the group may keep
        #    growing while earlier instances are still booting
        #  * scale in needs a long run of quiet datapoints and does observe
        #    a cooldown
        #
        seconds_since_metric += dt
        if cooldown_remaining > 0:
            cooldown_remaining -= dt

        if seconds_since_metric >= config.metric_period_s:
            seconds_since_metric = 0

            warming = [n for n in nodes if n.state == PENDING]
            desired = max(1, len([n for n in nodes if n.state != DRAINING]))

            if pol.aggregate == "fleet_total" and pol.metric == "index":
                # total load across the fleet over the capacity the fleet will
                # have once everything that has been asked for is in service.
                # Counting the booting nodes here is what stops the policy
                # from asking for the same capacity twice.
                total_load = sum(n.load for n in nodes)
                fleet_capacity = desired * prof.capacity
                signal = total_load / fleet_capacity if fleet_capacity else 0.0
            else:
                raw = {"cpu": mean_cpu, "index": mean_index,
                       "gpu": mean_gpu}[pol.metric]
                # warmup damping: a booting instance is assumed to be sitting
                # at the target, so the group average is pulled toward target
                # by the capacity that is already on its way
                if serving or warming:
                    signal = ((raw * len(serving)) + (pol.target * len(warming))) \
                             / max(1, (len(serving) + len(warming)))
                else:
                    signal = raw

            # safety alarm, read on the real signal rather than the damped one
            safety_breach = False
            if pol.safety_metric:
                safety_value = {"cpu": mean_cpu, "index": mean_index,
                                "gpu": mean_gpu}[pol.safety_metric]
                safety_breach = safety_value > pol.safety_threshold

            if signal > pol.target or safety_breach:
                breach_out_run += 1
                breach_in_run = 0
            elif signal < pol.target * 0.5:
                breach_in_run += 1
                breach_out_run = 0
            else:
                breach_out_run = 0
                breach_in_run = 0

            if breach_out_run >= pol.breach_out_datapoints:
                # Target tracking sizes the group so the metric lands on the
                # target. The formula is proportional, which is exact when the
                # metric is proportional to load.
                #
                # The load index is proportional to load by construction, so
                # one evaluation sizes the group correctly however far behind
                # it is.
                #
                # CPU is not. It is a bounded percentage that stops at 100, so
                # the most this formula can ever ask for is
                # ceil(desired * 100 / target), which at a 60 percent target
                # is 1.67 times the current size. A CPU driven group that is
                # ten times behind cannot say so, and has to climb in rounds.
                if pol.target > 0:
                    wanted = math.ceil(desired * signal / pol.target)
                else:
                    wanted = desired + 1
                wanted = max(wanted, desired + 1)
                wanted = min(wanted, pol.max_nodes)
                to_add = wanted - desired
                if to_add > 0:
                    for _ in range(to_add):
                        nodes.append(Node(state=PENDING,
                                          boot_remaining_s=config.boot_time_s))
                    res.scale_out_events += 1
                    if res.first_action_s is None:
                        res.first_action_s = t
                breach_out_run = 0

            elif breach_in_run >= pol.breach_in_datapoints and cooldown_remaining <= 0:
                if len(serving) > pol.min_nodes:
                    victim = min(serving, key=lambda n: n.load)
                    victim.state = DRAINING
                    victim.drain_remaining_s = config.drain_grace_s
                    res.scale_in_events += 1
                    if res.first_action_s is None:
                        res.first_action_s = t
                    cooldown_remaining = pol.cooldown_s
                breach_in_run = 0

    price = INSTANCE_PRICE_PER_HOUR.get(prof.instance_type, 0.0)
    res.cost_usd = res.instance_minutes / 60.0 * price
    return res
