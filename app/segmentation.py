"""停走分段。

把一条轨迹切成一段段「行驶」和「停留」，供后面的分类、偏航、温度分析使用。

判断依据只有速度和前后连续性，不看 POI，也不看车门。规则见 README 6.1：

    1. 速度低于 V_STOP(0.5 m/s) 的点算「准停留」
    2. 准停留连续超过 T_MIN(180 秒) 才算正式停留
    3. 不够 T_MIN 的准停留，其实是红灯、堵车、起步这些，要合并回行驶段
    4. 单个孤立的低速点多半是 GPS 漂移，看前后点把它纠正过来

第 3 条是这个算法的关键：不设时长门槛的话，每个红灯都会变成一次「停留」，
停留次数会多到没法看。
"""

from app.geo import haversine
from app.models import SEGMENT_MOVE, SEGMENT_STOP, Segment
from config import T_MIN, V_STOP


def _slow_flags(points):
    """先按速度粗分：速度低于 V_STOP 的标记为准停留。"""
    return [p.speed < V_STOP for p in points]


def _smooth_isolated(flags, passes=2):
    """把孤立的单点纠正成和前后一致，消掉 GPS 漂移造成的假变化。

    两种情况都会出问题，所以两个方向都要管：
        - 车在正常行驶，某一个点报了 0 速度  -> 会被当成准停留，把行驶段劈成两半
        - 车停着没动，某一个点报了个高速度    -> 会把停留段劈成两半

    只处理「长度恰好为 1」的段，因为漂移通常就是一个点的事。
    跑两遍是为了处理 ``XXOXX`` 这种两个孤立点挨着的情况。
    """
    out = list(flags)
    for _ in range(passes):
        source = list(out)
        for i in range(1, len(source) - 1):
            if source[i - 1] == source[i + 1] and source[i] != source[i - 1]:
                out[i] = source[i - 1]
    return out


def _runs(flags):
    """把标记相同的连续点分成一段段，返回 [(标记, 起点下标, 终点下标), ...]。"""
    runs = []
    start = 0
    for i in range(1, len(flags) + 1):
        if i == len(flags) or flags[i] != flags[start]:
            runs.append([flags[start], start, i - 1])
            start = i
    return runs


def _merge_short_slow(runs, points):
    """时长达不到 T_MIN 的准停留，合并回行驶段。

    合并时要和相邻的同类段接着并，所以这里用下标游标边扫边并。
    """
    merged = []
    for is_slow, start, end in runs:
        duration = points[end].ts - points[start].ts
        is_stop = is_slow and duration >= T_MIN

        if merged and merged[-1][0] == is_stop:
            merged[-1][2] = end          # 和上一段同类，接上去
        else:
            merged.append([is_stop, start, end])
    return merged


def _segment_distance_m(points):
    """一段轨迹的行驶距离，米。逐点累加大圆距离。"""
    total = 0.0
    for prev, cur in zip(points, points[1:]):
        total += haversine(prev.latitude, prev.longitude, cur.latitude, cur.longitude)
    return total


def find_segment(segments, ts):
    """某个时刻落在哪一段轨迹里，找不到返回 None。

    温度异常要知道它发生时车是在停车还是在开，就靠这个函数。
    分段是按时间顺序排好的，所以这里朴素地扫一遍就够，
    一次分析顶多几百段。
    """
    for segment in segments:
        if segment.start_ts <= ts <= segment.end_ts:
            return segment
    return None


def segment_track(points):
    """把轨迹切成行驶段和停留段。

    points 是 TrackPoint 列表，会先按时间排一次序（重复时间戳不会报错）。
    返回 Segment 列表，此时 stop_type 还是 None，要等 classification.py 来填。
    """
    if not points:
        return []

    # 输入不保证有序，重复时间戳也不保证没有，所以这里稳稳地排一次
    points = sorted(points, key=lambda p: p.ts)

    flags = _smooth_isolated(_slow_flags(points))
    runs = _merge_short_slow(_runs(flags), points)

    segments = []
    for is_stop, start, end in runs:
        chunk = points[start:end + 1]
        duration = chunk[-1].ts - chunk[0].ts

        if is_stop:
            # 停留段按定义就没有位移。GPS 有 ±3 米抖动，150 个点逐点累加
            # 会算出几百米的假位移，所以这里直接记 0。
            distance = 0.0
            avg_speed = 0.0
        else:
            distance = _segment_distance_m(chunk)
            avg_speed = distance / duration if duration > 0 else 0.0

        segments.append(Segment(
            segment_type=SEGMENT_STOP if is_stop else SEGMENT_MOVE,
            start_ts=chunk[0].ts,
            end_ts=chunk[-1].ts,
            duration_s=duration,
            start_lat=chunk[0].latitude,
            start_lng=chunk[0].longitude,
            end_lat=chunk[-1].latitude,
            end_lng=chunk[-1].longitude,
            distance_m=round(distance, 2),
            avg_speed=round(avg_speed, 2),
        ))

    return segments
