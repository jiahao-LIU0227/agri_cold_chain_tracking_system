"""停留分类。

分段算法只告诉我们「车在这儿停了一会儿」，这个模块负责回答「停下来干什么」。

判断顺序见 README 6.2，从最确定的往最不确定的排：

    1. 起点围栏内          -> 装货
    2. 终点围栏内          -> 卸货
    3. 加油站 POI 内        -> 加油
    4. 装卸点 POI 内且开门  -> 中途装卸
    5. 不在任何 POI 且停很久 -> 异常停留
    6. 其他正常时长的停留   -> 休息
    7. 遇到没定义过的 POI    -> 未知

为什么要按这个顺序：起终点是最确定的，先判掉；POI 是外部证据，比时长可靠；
「停很久」只是个时长特征，最容易误判，所以放最后。
"""

from app.geo import haversine
from app.models import (POI_GAS_STATION, POI_LOADING_DOCK,
                        SEGMENT_STOP, STOP_ABNORMAL, STOP_LOAD, STOP_REFUEL,
                        STOP_REST, STOP_TRANSFER, STOP_UNKNOWN, STOP_UNLOAD)
from config import GEOFENCE_RADIUS, T_ABNORMAL


def _stop_center(segment):
    """停留段的位置。取首尾两点的中间，比只取首点稳一点。"""
    return ((segment.start_lat + segment.end_lat) / 2,
            (segment.start_lng + segment.end_lng) / 2)


def _door_opened(points, start_ts, end_ts):
    """这段停留期间车门开过没有。

    只要有过一次就算开过——司机开门搬货不会只开一瞬间。
    """
    for p in points:
        if start_ts <= p.ts <= end_ts and p.door_open:
            return True
    return False


def _pois_within(latitude, longitude, pois):
    """落在围栏内的 POI，按距离从近到远排。

    POI 各带自己的 radius_m，所以这里不能用一个统一半径一刀切。
    """
    hits = []
    for poi in pois:
        distance = haversine(latitude, longitude, poi.latitude, poi.longitude)
        if distance <= poi.radius_m:
            hits.append((distance, poi))
    hits.sort(key=lambda item: item[0])
    return [poi for _, poi in hits]


def classify_stop(segment, points, task, pois):
    """判断一段停留是什么类型，返回 models 里的 STOP_* 常量。"""
    latitude, longitude = _stop_center(segment)

    # 1 / 2. 起终点围栏。用 GEOFENCE_RADIUS，和 POI 的半径是两套参数
    if haversine(latitude, longitude, task.origin_lat, task.origin_lng) <= GEOFENCE_RADIUS:
        return STOP_LOAD
    if haversine(latitude, longitude, task.dest_lat, task.dest_lng) <= GEOFENCE_RADIUS:
        return STOP_UNLOAD

    # 3 / 4 / 7. 看停在哪类 POI 里
    nearby = _pois_within(latitude, longitude, pois)
    for poi in nearby:
        if poi.poi_type == POI_GAS_STATION:
            return STOP_REFUEL
        if poi.poi_type == POI_LOADING_DOCK:
            # 在装卸点却不开关门，说明不了在装卸，只能老实说不知道
            if _door_opened(points, segment.start_ts, segment.end_ts):
                return STOP_TRANSFER
            return STOP_UNKNOWN

    # 5. 周围没有 POI 又停了很久，这是要找的异常
    if not nearby and segment.duration_s > T_ABNORMAL:
        return STOP_ABNORMAL

    # 6. 其余正常时长的停留都按休息算
    return STOP_REST


def classify_segments(segments, points, task, pois):
    """给所有停留段填上 stop_type，行驶段保持 None。就地修改并返回入参。"""
    for segment in segments:
        if segment.segment_type != SEGMENT_STOP:
            continue
        segment.stop_type = classify_stop(segment, points, task, pois)
    return segments
