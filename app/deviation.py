"""偏航检测。

规划路线是一条折线。轨迹点离这条折线超过 ROUTE_BUFFER(200 米) 就算偏航点，
连续的偏航点合成一次偏航事件，记下起止时间和最远偏出去多少米。

坐标系提醒：这里能算对的前提是规划路线和轨迹点在同一个坐标系里。
高德返回的路线是 GCJ-02，模拟器生成的也是 GCJ-02，所以没问题。
要是掺进 WGS-84 的真实 GPS 数据，每个点都会偏出去几百米，整条轨迹会被判成
一次大偏航——那是坐标系的问题，不是算法的问题，见 README 6.5。

距离计算统一走 app/geo.py 的 RoutePath，它负责把经纬度换成米制再交给 Shapely。
"""

from app.geo import RoutePath
from app.models import EVENT_DEVIATION, SEGMENT_STOP, Event
from app.segmentation import find_segment
from config import ROUTE_BUFFER


def _build_event(entries, segments):
    """把一串连续偏航的点合成一个事件。"""
    # 最远的那一点就是这次偏航的代表位置，地图上标它
    peak_point, peak_distance = max(entries, key=lambda item: item[1])
    start_ts = entries[0][0].ts
    end_ts = entries[-1][0].ts

    segment = find_segment(segments, peak_point.ts) if segments else None

    return Event(
        event_type=EVENT_DEVIATION,
        start_ts=start_ts,
        end_ts=end_ts,
        duration_s=end_ts - start_ts,
        max_distance_m=round(peak_distance, 2),
        latitude=peak_point.latitude,
        longitude=peak_point.longitude,
        during_stop=bool(segment and segment.segment_type == SEGMENT_STOP),
        segment_id=segment.id if segment else None,
    )


def detect_deviations(points, route_points, segments=None):
    """找出所有偏航事件，按时间升序返回 Event 列表。

    segments 可以不传。传了的话会给事件补上「当时是不是停在某处」，
    以及关联的分段 id，方便从事件反查停留类型。
    """
    if not points or not route_points:
        return []

    path = RoutePath([(p.latitude, p.longitude) for p in route_points])

    events = []
    current = []
    for point in sorted(points, key=lambda p: p.ts):
        distance = path.distance_to_route_m(point.latitude, point.longitude)
        if distance > ROUTE_BUFFER:
            current.append((point, distance))
        elif current:
            events.append(_build_event(current, segments))
            current = []

    # 轨迹末尾还在偏航的话，别忘了收尾
    if current:
        events.append(_build_event(current, segments))

    return events
