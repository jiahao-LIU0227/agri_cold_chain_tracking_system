"""偏航检测的测试。

README 9 要求覆盖的一条是「偏离约 500 米 8 分钟时能生成一个偏航段」。
偏航算法本身没有时长门槛（超过缓冲区哪怕一个点也算），所以这里同时测
「一个点也算」和「没超过缓冲区一个都不算」，把门槛卡在 ROUTE_BUFFER 上。
"""

import pytest

from app.deviation import detect_deviations
from app.models import EVENT_DEVIATION, SEGMENT_STOP
from app.segmentation import segment_track
from conftest import T0, at_meters, point
from config import ROUTE_BUFFER

CRUISE = 15.0
INTERVAL = 60          # 一分钟一个点，8 分钟就是 9 个点

ON_ROUTE = [(0, 0, CRUISE), (1000, 0, CRUISE), (2000, 0, CRUISE)]
# 从路线上往北挪 500 米，横着走 9 个点 = 8 分钟
OFF_ROUTE_500M = [(3000 + 1500 * i, 500, CRUISE) for i in range(9)]
BACK_ON_ROUTE = [(16000, 0, CRUISE), (18000, 0, CRUISE)]


def track_from_spec(spec, interval=INTERVAL, **kwargs):
    """spec 是 [(东向米, 北向米, 速度 m/s), ...]，按顺序连成一条轨迹。

    偏航测试要自己控制车往哪儿走，所以不用 conftest 里那两个按速度积分的工具。
    注意东向米别超过路线长度（straight_route 默认 20 公里），
    超出路线末端的点本身就会被算成偏航。
    """
    return [
        point(T0 + interval * i, speed=speed, east_m=east, north_m=north, **kwargs)
        for i, (east, north, speed) in enumerate(spec)
    ]


def last_ts(spec, interval=INTERVAL):
    """spec 最后一个点的时间戳。"""
    return T0 + interval * (len(spec) - 1)


# ============================================================
# 主要场景：偏出去 500 米，8 分钟
# ============================================================

def test_500m_off_route_for_8_minutes_is_one_event(route):
    """偏离约 500 米、持续 8 分钟 —— 应该正好生成一个偏航段。

    连续的偏航点要合成**一个**事件，不是每个点报一次。
    """
    points = track_from_spec(ON_ROUTE + OFF_ROUTE_500M + BACK_ON_ROUTE)

    events = detect_deviations(points, route)

    assert len(events) == 1
    event = events[0]
    assert event.event_type == EVENT_DEVIATION
    assert event.max_distance_m == pytest.approx(500.0, abs=5.0)


def test_deviation_span_matches_the_off_route_points(route):
    """偏航段的时间范围要正好落在偏离的那几个点上。"""
    points = track_from_spec(ON_ROUTE + OFF_ROUTE_500M + BACK_ON_ROUTE)

    event = detect_deviations(points, route)[0]

    first_off = points[len(ON_ROUTE)]
    last_off = points[len(ON_ROUTE) + len(OFF_ROUTE_500M) - 1]
    assert event.start_ts == first_off.ts
    assert event.end_ts == last_off.ts
    assert event.duration_s == last_off.ts - first_off.ts
    assert event.duration_s == 8 * 60


def test_deviation_records_the_farthest_point(route):
    """事件位置要记「偏得最远的那一点」，地图上就标它。"""
    # 中间那个点偏出去 800 米，比两边都远
    spec = list(ON_ROUTE)
    spec += [(3000, 500, CRUISE), (4500, 800, CRUISE), (6000, 500, CRUISE)]
    spec += [(9000, 0, CRUISE), (11000, 0, CRUISE)]

    event = detect_deviations(track_from_spec(spec), route)[0]

    peak = spec[3 + 1]                     # (4500, 800, CRUISE)
    expected_lat, expected_lng = at_meters(peak[0], peak[1])
    assert event.latitude == pytest.approx(expected_lat, abs=1e-6)
    assert event.longitude == pytest.approx(expected_lng, abs=1e-6)
    assert event.max_distance_m == pytest.approx(800.0, abs=5.0)


def test_two_separate_excursions_make_two_events(route):
    """出去一趟、回来、再出去一趟，这是两次偏航，不能合成一次。"""
    spec = list(ON_ROUTE)
    spec += [(3000 + 1500 * i, 500, CRUISE) for i in range(4)]
    spec += [(9000, 0, CRUISE), (11000, 0, CRUISE)]
    spec += [(13000 + 1500 * i, 500, CRUISE) for i in range(4)]
    spec += [(19000, 0, CRUISE)]

    events = detect_deviations(track_from_spec(spec), route)

    assert len(events) == 2
    assert events[0].start_ts < events[1].start_ts


def test_deviation_at_the_end_of_the_track_is_not_lost(route):
    """轨迹最后还在偏航：没有「回到路线上」的那个点收尾，也不能漏掉。"""
    spec = list(ON_ROUTE) + [(3000 + 1500 * i, 500, CRUISE) for i in range(5)]

    events = detect_deviations(track_from_spec(spec), route)

    assert len(events) == 1
    assert events[0].end_ts == last_ts(spec)


# ============================================================
# ROUTE_BUFFER 的门槛
# ============================================================

def test_staying_on_the_route_makes_no_event(route):
    """老老实实沿路线开，一个偏航都不该有。"""
    spec = [(2000 * i, 0, CRUISE) for i in range(6)]

    assert detect_deviations(track_from_spec(spec), route) == []


def test_offset_below_the_buffer_makes_no_event(route):
    """偏出去的距离没超过缓冲区，不算偏航。"""
    offset = ROUTE_BUFFER - 20
    spec = list(ON_ROUTE) + [(3000 + 1500 * i, offset, CRUISE) for i in range(6)]
    spec += [(14000, 0, CRUISE), (16000, 0, CRUISE)]

    assert detect_deviations(track_from_spec(spec), route) == []


def test_offset_above_the_buffer_makes_an_event(route):
    """超过缓冲区就算。和上一条一起把门槛夹在 ROUTE_BUFFER 上。"""
    offset = ROUTE_BUFFER + 20
    spec = list(ON_ROUTE) + [(3000 + 1500 * i, offset, CRUISE) for i in range(6)]
    spec += [(14000, 0, CRUISE), (16000, 0, CRUISE)]

    events = detect_deviations(track_from_spec(spec), route)

    assert len(events) == 1
    assert events[0].max_distance_m == pytest.approx(offset, abs=5.0)


def test_a_single_off_route_point_counts(route):
    """偏航不设时长门槛：哪怕只有一个点偏出去，也要报出来。

    这和停留不一样——停留有 T_MIN 过滤红灯，偏航没有：
    重新回到路线上只需要几分钟，用时长过滤会把真的绕路漏掉。
    """
    spec = list(ON_ROUTE) + [(3000, 900, CRUISE)] + [(5000, 0, CRUISE), (7000, 0, CRUISE)]

    events = detect_deviations(track_from_spec(spec), route)

    assert len(events) == 1
    assert events[0].duration_s == 0


# ============================================================
# 和分段关联：偏航时是在停车还是在开
# ============================================================

def test_deviation_while_driving_is_not_during_stop(route):
    """一边开一边偏出去：during_stop 要是 False。"""
    points = track_from_spec(ON_ROUTE + OFF_ROUTE_500M + BACK_ON_ROUTE)
    segments = segment_track(points)

    event = detect_deviations(points, route, segments)[0]

    assert segments[0].segment_type != SEGMENT_STOP
    assert event.during_stop is False


def test_deviation_while_stopped_is_during_stop(route):
    """停在路线外面不动（比如拐进一条岔路停车）：要标出当时是停着的。

    这几个点的速度是 0，所以分段算法会把它们切成停留段；
    偏航算法通过时间戳找回那一段，把 during_stop 填成 True。
    """
    spec = list(ON_ROUTE)
    spec += [(3000, 500, 0.0)] * 5         # 偏出 500 米后停 4 分钟
    spec += [(9000, 0, CRUISE), (11000, 0, CRUISE)]

    points = track_from_spec(spec)
    segments = segment_track(points)
    events = detect_deviations(points, route, segments)

    assert len(events) == 1
    assert events[0].during_stop is True
    # 关联的应该是那段停留，不是行驶段
    stop_segment = [s for s in segments if s.segment_type == SEGMENT_STOP][0]
    assert stop_segment.start_ts <= events[0].start_ts
    assert events[0].end_ts <= stop_segment.end_ts


def test_deviations_are_sorted_by_time(route):
    """事件要按时间升序排好，前面板直接按顺序显示。"""
    spec = list(ON_ROUTE)
    spec += [(3000 + 1500 * i, 500, CRUISE) for i in range(3)]
    spec += [(8000, 0, CRUISE), (10000, 0, CRUISE)]
    spec += [(12000 + 1500 * i, 600, CRUISE) for i in range(3)]
    spec += [(17000, 0, CRUISE), (19000, 0, CRUISE)]

    events = detect_deviations(track_from_spec(spec), route)

    assert [e.start_ts for e in events] == sorted(e.start_ts for e in events)


# ============================================================
# 脏数据不能把程序搞崩
# ============================================================

def test_empty_points_returns_nothing(route):
    assert detect_deviations([], route) == []


def test_empty_route_returns_nothing():
    points = track_from_spec(ON_ROUTE)

    assert detect_deviations(points, []) == []


def test_unsorted_points_are_handled(route):
    """输入顺序乱的要先排好序，事件不能因为顺序不同就变。"""
    points = track_from_spec(ON_ROUTE + OFF_ROUTE_500M + BACK_ON_ROUTE)

    ordered = detect_deviations(points, route)
    shuffled = detect_deviations(list(reversed(points)), route)

    assert len(ordered) == len(shuffled) == 1
    assert ordered[0].start_ts == shuffled[0].start_ts


def test_single_point_track_does_not_crash(route):
    """只有一个点：算一下它离路线多远就行，不用报错。"""
    events = detect_deviations([point(T0, speed=CRUISE, east_m=3000, north_m=500)], route)

    assert len(events) == 1


# ============================================================
# 坐标系提醒
# ============================================================

def test_wgs84_style_shift_would_look_like_one_huge_deviation(route):
    """把整条轨迹平移 0.003 度（≈ 300 米），看起来就是一整段偏航。

    这不是算法的毛病，是坐标系掺混的典型症状：GCJ-02 和 WGS-84 之间
    就差这么几百米。所以项目里所有数据必须是同一套坐标，见 README 6.5。
    留着这条测试，是为了以后再遇到「每条轨迹都在偏航」时能马上想到这个原因。
    """
    spec = list(ON_ROUTE) + [(3000, 500, CRUISE)]
    points = track_from_spec(spec)
    for p in points:
        p.latitude += 0.003

    events = detect_deviations(points, route)

    assert len(events) == 1
    assert events[0].max_distance_m > 300
    assert events[0].start_ts == points[0].ts, "整条轨迹都在偏，从第一个点就开始了"
