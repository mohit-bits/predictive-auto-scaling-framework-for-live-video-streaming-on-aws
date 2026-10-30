"""
Measured data from the load tests.

Every number in this file came from one of the five workbooks that were run
for this dissertation. The cell reference is given against each entry so any
value can be traced back to the sheet it came from.

Nothing here is estimated or invented. Where a sheet gave a range such as
"65-70", the midpoint is used and the original range is kept in the comment.

Workbooks:
  RTMP_LOAD_TEST_RESULT.xlsx
  RTSP_LOAD_TEST_RESULTS.xlsx
  SRT_Load_Test.xlsx
  WebRTC_PLAYBACK_LOAD_TEST.xlsx
  WebRTC_Publisher_LOAD_TEST.xlsx
"""

# ---------------------------------------------------------------------------
# Instance sizes, for cost and for reporting
# ---------------------------------------------------------------------------

# On demand price per hour, ap-south-1 (Mumbai).
# These are list prices and should be re-checked before the final report.
INSTANCE_PRICE_PER_HOUR = {
    # On demand, ap-south-1 (Mumbai), verified 27 October 2026.
    # Earlier versions of this file used ap-southeast-1 prices by mistake.
    "c5.large": 0.085,
    "c5.xlarge": 0.170,
    "c5.2xlarge": 0.340,
    "c5.4xlarge": 0.680,
    "c5.9xlarge": 1.530,
    # g5 prices NOT yet verified for ap-south-1. These are carried over and
    # must be checked before any cost figure from scenario 3 is quoted.
    "g5.xlarge": 1.210,
    "g5.2xlarge": 1.452,
    "g5.4xlarge": 2.178,
    "g5.8xlarge": 3.388,
}

# Supporting infrastructure in the cluster stack, ap-south-1, per hour.
# Needed because a per instance figure understates what a cluster costs.
FIXED_STACK_COST_PER_HOUR = {
    "mongodb_m5.large": 0.108,
    "alb": 0.024,
    "nlb": 0.024,
    "public_ipv4_each": 0.005,
}

VCPU = {
    "c5.large": 2, "c5.xlarge": 4, "c5.2xlarge": 8,
    "c5.4xlarge": 16, "c5.9xlarge": 36,
    "g5.xlarge": 4, "g5.2xlarge": 8, "g5.4xlarge": 16, "g5.8xlarge": 32,
}


# ---------------------------------------------------------------------------
# WebRTC playback capacity, viewers per node
# Source: WebRTC_PLAYBACK_LOAD_TEST.xlsx, Sheet1
# All rows recorded at 70-75 percent CPU.
#
# IMPORTANT, AND EASY TO GET WRONG: these playback figures were measured with
# a 1080p source at 2500 kbps. The ingest figures elsewhere in this file were
# measured at 2000 kbps. The two sets are NOT on the same basis and a viewer
# count from this table only applies to a 2500 kbps source.
#
# Cost per viewer scales with bitrate, so the capacity does too. Confirmed on
# a live cluster on 30 October 2026: a c5.xlarge edge serving a 2000 kbps
# source held about 130 viewers at the CPU this table gives for 105. The
# prediction from scaling the bitrate is 105 * 2500/2000 = 131.
#
# So when comparing against this table, either publish at 2500 kbps or scale
# the capacity figure by 2500/source_bitrate. Mixing the two silently
# overstates capacity by about a quarter.
# ---------------------------------------------------------------------------

PLAYBACK_SOURCE_BITRATE_KBPS = 2500     # basis for WEBRTC_PLAYBACK_CAPACITY
INGEST_SOURCE_BITRATE_KBPS = 2000       # basis for the ingest tables


def playback_capacity_at_bitrate(instance, resolution, bitrate_kbps):
    """
    Scale a measured playback capacity to a different source bitrate.

    Linear in bitrate, which matched observation to within one percent at
    2000 kbps. Only validated at that one point, so treat it as a correction
    for nearby bitrates rather than a general law.
    """
    base = WEBRTC_PLAYBACK_CAPACITY[instance][resolution]
    return base * PLAYBACK_SOURCE_BITRATE_KBPS / float(bitrate_kbps)

WEBRTC_PLAYBACK_CAPACITY = {
    # instance        1080p 720p  480p  360p        source rows
    "c5.xlarge":   {"1080p": 105, "720p": 120, "480p": 160, "360p": 170},   # D4:D7
    "c5.2xlarge":  {"1080p": 200, "720p": 220, "480p": 300, "360p": 310},   # D11:D14
    "c5.4xlarge":  {"1080p": 380, "720p": 430, "480p": 560, "360p": 570},   # D18:D21
    "c5.9xlarge":  {"1080p": 760, "720p": 830, "480p": 1100, "360p": 1150}, # D25:D28
}

# The CPU reading at which those capacities were recorded.
PLAYBACK_CAPACITY_CPU = 72.5   # sheet says 70-75 on every row


# ---------------------------------------------------------------------------
# WebRTC publish, no transcoding. CPU response curve.
# Source: WebRTC_Publisher_LOAD_TEST.xlsx, sheet "Without ABR", column F.
#
# This is the most valuable part of the data set. Column F recorded CPU at
# several intermediate stream counts, not just at the capacity point, so the
# shape of the CPU response curve is measured rather than assumed.
#
# Each entry is a list of (stream_count, cpu_percent) using range midpoints.
# ---------------------------------------------------------------------------

WEBRTC_PUBLISH_CPU_CURVE = {
    "c5.xlarge": {
        # F3: 10=15-20, 20=30-35, 30=50-55, 40=70-75, 50=99
        "1080p": [(0, 0), (10, 17.5), (20, 32.5), (30, 52.5), (40, 72.5), (50, 99)],
        # F4: 10=10-15, 20=25-30, 30=45-50, 40=60-65, 55=90+
        "720p":  [(0, 0), (10, 12.5), (20, 27.5), (30, 47.5), (40, 62.5), (55, 90)],
        # F5: 10=10, 20=20-25, 30=40-45, 40=55-60, 50=80-85, 60=95+
        "480p":  [(0, 0), (10, 10.0), (20, 22.5), (30, 42.5), (40, 57.5), (50, 82.5), (60, 95)],
        # F6: 10=7-10, 20=20-25, 30=30-35, 40=50-55, 50=65-75, 65=95+
        "360p":  [(0, 0), (10, 8.5), (20, 22.5), (30, 32.5), (40, 52.5), (50, 70.0), (65, 95)],
    },
    "c5.2xlarge": {
        # F11: 15=10-12, 30=20-25, 45=35-40, 60=55-60, 75=75-80
        "720p":  [(0, 0), (15, 11.0), (30, 22.5), (45, 37.5), (60, 57.5), (75, 77.5)],
        # F12: 30=15-20, 45=30-35, 60=45-50, 90=75-80
        "480p":  [(0, 0), (30, 17.5), (45, 32.5), (60, 47.5), (90, 77.5)],
        # F13: 45=25-30, 60=40-45, 90=70-75
        "360p":  [(0, 0), (45, 27.5), (60, 42.5), (90, 72.5)],
        # NOTE: the c5.2xlarge 1080p row (F10) repeats the c5.xlarge 1080p
        # numbers exactly and its capacity column reads 35-45, which is the
        # same as the smaller instance. Treated as a copy-paste error in the
        # sheet and excluded. This was flagged in the mid semester report.
    },
    "c5.4xlarge": {
        # F17: 30=5-10, 60=15-20, 90=35-40, 120=50-55, 150=65-70, 180=85-90
        "1080p": [(0, 0), (30, 7.5), (60, 17.5), (90, 37.5), (120, 52.5), (150, 67.5), (180, 87.5)],
        # F18: 50=15-20, 100=35-40, 150=50-55, 180=70-75
        "720p":  [(0, 0), (50, 17.5), (100, 37.5), (150, 52.5), (180, 72.5)],
        # F19: 100=25-30, 150=45-50, 200=80-85
        "480p":  [(0, 0), (100, 27.5), (150, 47.5), (200, 82.5)],
        # F20: 100=20-25, 150=40-45, 200=75-80
        "360p":  [(0, 0), (100, 22.5), (150, 42.5), (200, 77.5)],
    },
    "c5.9xlarge": {
        # F24: 100=15-20, 200=25-30, 250=45-50, 350=65-70
        "1080p": [(0, 0), (100, 17.5), (200, 27.5), (250, 47.5), (350, 67.5)],
        # F25: 100=10-15, 250=45-50, 350=55-60, 400=70-75
        "720p":  [(0, 0), (100, 12.5), (250, 47.5), (350, 57.5), (400, 72.5)],
        # F26: 100=10-15, 200=30-35, 400=60-65
        "480p":  [(0, 0), (100, 12.5), (200, 32.5), (400, 62.5)],
        # F27: 100=10, 200=25-30, 300=35-45, 400=50-55, 500=70-75
        "360p":  [(0, 0), (100, 10.0), (200, 27.5), (300, 40.0), (400, 52.5), (500, 72.5)],
    },
}

# Publish capacity, streams per node, at 65-70 percent CPU.
# Source: WebRTC_Publisher_LOAD_TEST.xlsx "Without ABR" column D.
WEBRTC_PUBLISH_CAPACITY = {
    "c5.xlarge":  {"1080p": 37, "720p": 42, "480p": 47, "360p": 52},   # D3:D6 (ranges, midpoints)
    "c5.2xlarge": {"720p": 70, "480p": 80, "360p": 85},                 # D11:D13
    "c5.4xlarge": {"1080p": 150, "720p": 165, "480p": 180, "360p": 195},# D17:D20
    "c5.9xlarge": {"1080p": 350, "720p": 390, "480p": 420, "360p": 480},# D24:D27
}


# ---------------------------------------------------------------------------
# RTMP ingest, no transcoding. Capacity only, recorded at 60-70 percent CPU.
# Source: RTMP_LOAD_TEST_RESULT.xlsx, sheet "Without ABR"
# ---------------------------------------------------------------------------

RTMP_PLAIN_CAPACITY = {
    "c5.xlarge":  {"1080p": 35, "720p": 40, "480p": 40, "360p": 40},    # D2:D5
    "c5.2xlarge": {"1080p": 75, "720p": 80, "480p": 80, "360p": 80},    # D9:D12
    "c5.4xlarge": {"1080p": 150, "720p": 160, "480p": 160, "360p": 160},# D16:D19
    "c5.9xlarge": {"1080p": 350, "720p": 360, "480p": 360, "360p": 360},# D23:D26
}


# ---------------------------------------------------------------------------
# RTMP ingest WITH transcoding, on CPU instances.
# Source: RTMP_LOAD_TEST_RESULT.xlsx, sheet "With  ABR"
#
# This is the collapse that motivates the whole load index. One 1080p stream
# with a single 1080p rendition costs roughly as much as 17 plain streams on
# a c5.xlarge (35 plain vs 2 transcoded).
# ---------------------------------------------------------------------------

RTMP_ABR_CPU_CAPACITY = {
    # key is (instance, number of renditions enabled on a 1080p input)
    ("c5.xlarge", 1):  2,    # E2,  CPU 60-70
    ("c5.xlarge", 2):  1,    # E6,  CPU 40-60
    ("c5.xlarge", 3):  1,    # E8
    ("c5.xlarge", 4):  1,    # E9
    ("c5.2xlarge", 1): 3,    # E13
    ("c5.2xlarge", 2): 2,    # E17
    ("c5.2xlarge", 3): 2,    # E19
    ("c5.2xlarge", 4): 2,    # E20
    ("c5.4xlarge", 1): 4,    # E24
    ("c5.4xlarge", 2): 2,    # E28
    ("c5.4xlarge", 3): 2,    # E30
    ("c5.4xlarge", 4): 2,    # E31
    ("c5.9xlarge", 1): 6,    # E35
    ("c5.9xlarge", 2): 4,    # E39
    ("c5.9xlarge", 3): 4,    # E41
    ("c5.9xlarge", 4): 4,    # E42
}


# ---------------------------------------------------------------------------
# RTMP ingest WITH transcoding, on GPU instances.
# Source: RTMP_LOAD_TEST_RESULT.xlsx, sheet "With ABR (GPU)"
#
# This is the central result of the capacity study.
#
# Reading down the 1080p input, single 1080p rendition row for every g5 size:
#
#   g5.xlarge   12 streams   CPU 60-65   GPU encoder 60-70
#   g5.2xlarge  12 streams   CPU 40-50   GPU encoder 60-70
#   g5.4xlarge  12 streams   CPU 15-20   GPU encoder 70-75
#   g5.8xlarge  12 streams   CPU 15-20   GPU encoder 70-75
#
# The stream count does not improve with instance size, because every one of
# these instances carries a single NVIDIA A10G encoder. What changes is the
# CPU reading at the point the node is full. On g5.4xlarge and g5.8xlarge the
# node is saturated while CPU shows 15 to 20 percent.
#
# A scaling policy with a 60 percent CPU target can never fire on those two
# instance types, no matter how much load arrives. It is not that it reacts
# late. It does not react at all.
# ---------------------------------------------------------------------------

RTMP_ABR_GPU = {
    # (instance, renditions): (streams, cpu_percent, gpu_encoder_percent)
    ("g5.xlarge", 1):   (12, 62.5, 65.0),   # E2,  F2 60-65,  G2 60-70
    ("g5.xlarge", 2):   (8,  67.5, 72.5),   # E6
    ("g5.xlarge", 3):   (4,  67.5, 35.0),   # E8
    ("g5.xlarge", 4):   (4,  67.5, 45.0),   # E9
    ("g5.2xlarge", 1):  (12, 45.0, 65.0),   # E13, F13 40-50, G13 60-70
    ("g5.2xlarge", 2):  (9,  55.0, 67.5),   # E17
    ("g5.2xlarge", 3):  (7,  65.0, 60.0),   # E19
    ("g5.2xlarge", 4):  (6,  65.0, 55.0),   # E20
    ("g5.4xlarge", 1):  (12, 17.5, 72.5),   # E24, F24 15-20, G24 70-75
    ("g5.4xlarge", 2):  (11, 52.5, 72.5),   # E28
    ("g5.4xlarge", 3):  (9,  47.5, 72.5),   # E30
    ("g5.4xlarge", 4):  (8,  50.0, 67.5),   # E31
    ("g5.8xlarge", 1):  (12, 17.5, 72.5),   # E35, F35 15-20, G35 70-75
    ("g5.8xlarge", 2):  (11, 47.5, 72.5),   # E39
    ("g5.8xlarge", 3):  (9,  37.5, 72.5),   # E41
    ("g5.8xlarge", 4):  (8,  40.0, 72.5),   # E42
}


# ---------------------------------------------------------------------------
# RTSP and SRT ingest, plain relay, 1080p with audio.
# Source: RTSP_LOAD_TEST_RESULTS.xlsx and SRT_Load_Test.xlsx
#
# The SRT figures for c5.4xlarge and c5.9xlarge were capped at 100 streams by
# the test harness, not by the server. They are lower bounds and must not be
# read as saturation points. Marked here so nothing downstream treats them as
# measured capacity.
# ---------------------------------------------------------------------------

RTSP_CAPACITY_1080P_WITH_AUDIO = {
    "c5.xlarge": 40, "c5.2xlarge": 85, "c5.4xlarge": 170, "c5.9xlarge": 360,
}

SRT_CAPACITY_1080P_WITH_AUDIO = {
    "c5.xlarge": 42, "c5.2xlarge": 86,
}
SRT_LOWER_BOUNDS_ONLY = {
    "c5.4xlarge": 100, "c5.9xlarge": 100,   # harness limit, not server limit
}


# ---------------------------------------------------------------------------
# Helper: piecewise linear lookup on a measured curve
# ---------------------------------------------------------------------------

def interpolate(curve, x):
    """
    Read a value off a measured curve by straight line interpolation between
    the two nearest measured points.

    Beyond the last measured point the final segment's slope is continued.
    That region is extrapolation and is flagged by the caller where it
    matters, because the real curve bends upward steeply near saturation.
    """
    if x <= curve[0][0]:
        return curve[0][1]
    for i in range(len(curve) - 1):
        x0, y0 = curve[i]
        x1, y1 = curve[i + 1]
        if x <= x1:
            if x1 == x0:
                return y1
            return y0 + (y1 - y0) * (x - x0) / (x1 - x0)
    # past the end of the measured range, continue the last slope
    x0, y0 = curve[-2]
    x1, y1 = curve[-1]
    slope = (y1 - y0) / (x1 - x0) if x1 != x0 else 0.0
    return y1 + slope * (x - x1)


def playback_cpu_curve(instance, resolution, convex=True):
    """
    Build a CPU response curve for WebRTC playback.

    The playback workbook recorded only the capacity point, at 70-75 percent
    CPU. It did not record intermediate readings the way the publisher sheet
    did. So the shape between zero and capacity has to be chosen.

    Two options:

      convex=False  straight line from idle to the measured capacity point.

      convex=True   borrow the shape measured on the publisher sheet for the
                    same instance and resolution, rescaled so it passes
                    through the measured playback capacity point.

    The publisher data shows the true shape is convex: CPU rises slowly while
    the node is lightly loaded and steepens near saturation. On a c5.xlarge at
    1080p a straight line predicts 36 percent at 20 streams, where the
    measured value is 32.5.

    The straight line is therefore the conservative choice for this study. It
    makes CPU appear to rise sooner than it really does, which makes a CPU
    based policy react sooner than it really would. Any advantage the load
    index shows under the straight line assumption would be larger in reality.
    """
    cap = WEBRTC_PLAYBACK_CAPACITY[instance][resolution]
    if not convex:
        return [(0, 0.0), (cap, PLAYBACK_CAPACITY_CPU)]

    shape = WEBRTC_PUBLISH_CPU_CURVE.get(instance, {}).get(resolution)
    if not shape:
        return [(0, 0.0), (cap, PLAYBACK_CAPACITY_CPU)]

    # rescale the publisher shape onto the playback capacity point
    shape_x_at_cap = None
    for x, y in shape:
        if y >= PLAYBACK_CAPACITY_CPU:
            shape_x_at_cap = x
            break
    if shape_x_at_cap is None:
        shape_x_at_cap = shape[-1][0]
    if shape_x_at_cap == 0:
        return [(0, 0.0), (cap, PLAYBACK_CAPACITY_CPU)]

    scale = cap / shape_x_at_cap
    return [(x * scale, y) for x, y in shape]
