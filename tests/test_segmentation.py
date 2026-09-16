"""停走分段的测试。

README 9 里要求覆盖的几条：

    - 40 秒红灯不应生成停留
    - 恰好 180 秒和 181 秒的候选段，结果要明确
    - 轨迹只有一个点、时间戳重复、定位点缺失时程序不能崩溃

这些边界值都不是随便挑的：180 秒是 T_MIN，正好卡在「算不算停留」的分界线上，
差一秒结论就反过来，所以必须各测一遍，把规则钉死。
"""

import pytest

from app.models import SEGMENT_MOVE, SEGMENT_STOP
from app.segmentation import find_segment, segment_track
from conftest import T0, make_points_at, make_stop_segment, make_track
from config import T_MIN

# 行驶速度：15 m/s ≈ 54 km/h，普通货车的速度
CRUISE = 15.0


def _types(segments):
    """把分段结果简化成 ['move', 'stop', ...]，断言写起来清楚。"""
    return [s.segment_type for s in segments]


def track_with_stop(duration_s, interval=60, before=10, after=10):
    """造一条「开一段 -> 停 duration_s 秒 -> 再开一段」的轨迹。

    停留段首尾的时间差**恰好**是 duration_s，所以这个函数可以直接拿来
    测 T_MIN 的边界。前后各放 10 个点，是为了让停留段两边的邻居足够多，
    不至于被当成孤立点抹掉。
    """
    assert duration_s > 2 * interval, "停留段至少要 3 个点，否则是被平滑处理的情形"

    times = [T0 + interval * i for i in range(before)]
    stop_start = times[-1] + interval
    times += [stop_start,
              stop_start + interval,
              stop_start + 2 * interval,
              stop_start + duration_s]
    times += [times[-1] + interval * (i + 1) for i in range(after)]

    speeds = [CRUISE] * before + [0.0] * 4 + [CRUISE] * after
    return make_points_at(times, speeds)


# ============================================================
# 红灯不该被算成停留
# ============================================================

def test_red_light_40s_is_not_a_stop():
    """40 秒红灯：速度是 0，但远不到 T_MIN，要合并回行驶段。"""
    points = make_track([(600, CRUISE), (40, 0.0), (600, CRUISE)])

    segments = segment_track(points)

    assert _types(segments) == [SEGMENT_MOVE]
    assert segments[0].duration_s == points[-1].ts - points[0].ts


def test_short_stops_do_not_pile_up():
    """一路开一路遇红灯，最后还是一整段行驶，不能每遇一次红灯就多一段。"""
    plan = []
    for _ in range(8):
        plan += [(600, CRUISE), (40, 0.0)]

    segments = segment_track(make_track(plan))

    assert _types(segments) == [SEGMENT_MOVE]


# ============================================================
# T_MIN 的边界：恰好 180 秒 和 181 秒
# ============================================================

def test_stop_of_exactly_T_MIN_counts():
    """恰好 180 秒算停留（规则是「不低于 T_MIN」，不是「大于」）。"""
    points = track_with_stop(T_MIN)

    segments = segment_track(points)

    assert _types(segments) == [SEGMENT_MOVE, SEGMENT_STOP, SEGMENT_MOVE]
    assert segments[1].duration_s == T_MIN


def test_stop_one_second_shorter_does_not_count():
    """179 秒不算停留：差这一秒结论就得反过来，边界必须卡死。"""
    points = track_with_stop(T_MIN - 1)

    segments = segment_track(points)

    assert _types(segments) == [SEGMENT_MOVE]


def test_stop_one_second_longer_counts():
    """181 秒算停留。和 179 秒那条一起，把边界夹死在 180 上。"""
    points = track_with_stop(T_MIN + 1)

    segments = segment_track(points)

    assert _types(segments) == [SEGMENT_MOVE, SEGMENT_STOP, SEGMENT_MOVE]
    assert segments[1].duration_s == T_MIN + 1


def test_long_stop_is_one_segment():
    """一次 25 分钟的停车就是一整段停留，不能被切碎。"""
    points = track_with_stop(1500)

    segments = segment_track(points)

    assert _types(segments) == [SEGMENT_MOVE, SEGMENT_STOP, SEGMENT_MOVE]
    assert segments[1].duration_s == 1500


# ============================================================
# 停留段不该有里程
# ============================================================

def test_stop_segment_distance_is_zero():
    """停留段按定义没有位移，GPS 抖动不能累加出几百米的假里程。"""
    points = track_with_stop(900)
    # 停留段是 points[10:14]，把这几点的经度左右飘几米，模拟 GPS 抖动
    for i in range(10, 14):
        points[i].longitude += 0.00003

    stop = segment_track(points)[1]

    assert stop.distance_m == 0.0
    assert stop.avg_speed == 0.0


def test_move_segment_distance_matches_speed_times_time():
    """行驶段的里程要接近「速度 × 时间」，量级明显大于 0。"""
    points = make_track([(600, CRUISE)])

    move = segment_track(points)[0]

    expected = CRUISE * (points[-1].ts - points[0].ts)
    assert move.distance_m == pytest.approx(expected, rel=0.01)
    assert move.avg_speed == pytest.approx(CRUISE, rel=0.01)


# ============================================================
# GPS 漂移：孤立的异常点要被纠正
# ============================================================

def test_isolated_zero_speed_in_moving_track_is_absorbed():
    """正常开着，某一个点报了 0 速度：那是漂移，不能把行驶段劈成两半。"""
    times = [T0 + 10 * i for i in range(21)]
    speeds = [CRUISE] * 21
    speeds[10] = 0.0

    segments = segment_track(make_points_at(times, speeds))

    assert _types(segments) == [SEGMENT_MOVE]


def test_isolated_fast_point_inside_stop_does_not_split_it():
    """反过来也一样：停着的时候某一个点报了个高速度，停留段不能被劈开。"""
    points = make_track([(600, CRUISE), (900, 0.0), (600, CRUISE)])
    points[75].speed = CRUISE      # 75 号点在停留段正中间

    segments = segment_track(points)

    assert _types(segments) == [SEGMENT_MOVE, SEGMENT_STOP, SEGMENT_MOVE]
    assert segments[1].duration_s > T_MIN


# ============================================================
# 脏数据不能把程序搞崩
# ============================================================

def test_empty_track_returns_nothing():
    assert segment_track([]) == []


def test_single_point_track_does_not_crash():
    """只有一个点：算不出速度也谈不上停留，返回一段、时长为 0 就行。"""
    segments = segment_track(make_track([(10, CRUISE)]))

    assert len(segments) == 1
    assert segments[0].duration_s == 0
    assert segments[0].distance_m == 0.0


def test_duplicate_timestamps_do_not_crash():
    """时间戳重复。数据库故意不加唯一约束，就是要让算法自己扛住。"""
    times = [T0, T0, T0 + 10, T0 + 10, T0 + 20, T0 + 30]
    speeds = [CRUISE] * 6

    segments = segment_track(make_points_at(times, speeds))

    assert len(segments) >= 1
    assert all(s.duration_s >= 0 for s in segments)


def test_unsorted_input_is_sorted_first():
    """输入顺序乱的要先排好序，分段结果不能受输入顺序影响。"""
    points = track_with_stop(900)

    ordered = _types(segment_track(points))
    shuffled = _types(segment_track(list(reversed(points))))

    assert ordered == shuffled == [SEGMENT_MOVE, SEGMENT_STOP, SEGMENT_MOVE]


def test_bad_position_quality_does_not_crash():
    """定位质量差的点（position_quality=0）也要能算。"""
    points = make_track([(600, CRUISE), (900, 0.0), (600, CRUISE)])
    for p in points[60:75]:
        p.position_quality = 0

    segments = segment_track(points)

    assert _types(segments) == [SEGMENT_MOVE, SEGMENT_STOP, SEGMENT_MOVE]


# ============================================================
# 分段本身要自洽
# ============================================================

def test_segments_cover_the_whole_track_in_order():
    """分段要把整条轨迹按时间顺序铺满，不能乱序、不能重叠。

    注意这里说的是「铺满」而不是「首尾严丝合缝」：
    每一段的时间范围是它自己那些点的首尾时间戳，所以相邻两段之间会差一个
    采样间隔。那个间隔里的状态本来就是模糊的——车恰好在这段时间里的某一刻
    从开到停，落在哪一段都说得过去，所以不去硬凑。
    """
    points = track_with_stop(900)
    spacing = min(b.ts - a.ts for a, b in zip(points, points[1:]))

    segments = segment_track(points)

    assert segments[0].start_ts == points[0].ts
    assert segments[-1].end_ts == points[-1].ts
    for prev, cur in zip(segments, segments[1:]):
        assert prev.end_ts <= cur.start_ts, "分段乱序或者重叠了"
        assert cur.start_ts - prev.end_ts <= spacing, "相邻两段之间漏掉太多了"


def test_segment_duration_matches_timestamps():
    """duration_s 必须等于起止时间之差，三个字段不能各说各的。"""
    for segment in segment_track(track_with_stop(900)):
        assert segment.duration_s == segment.end_ts - segment.start_ts


def test_move_and_stop_alternate():
    """相邻两段的类型必须不一样，否则说明该合并的没合并。"""
    segments = segment_track(make_track(
        [(600, CRUISE), (900, 0.0), (600, CRUISE), (600, 0.0), (600, CRUISE)]
    ))

    for prev, cur in zip(segments, segments[1:]):
        assert prev.segment_type != cur.segment_type


# ============================================================
# find_segment：事件要靠它反查「当时在干什么」
# ============================================================

def test_find_segment_locates_a_stop():
    points = track_with_stop(900)
    segments = segment_track(points)
    stop = segments[1]

    assert find_segment(segments, stop.start_ts).segment_type == SEGMENT_STOP
    assert find_segment(segments, stop.end_ts).segment_type == SEGMENT_STOP
    assert find_segment(segments, (stop.start_ts + stop.end_ts) // 2).segment_type == SEGMENT_STOP


def test_find_segment_locates_a_move():
    points = track_with_stop(900)
    segments = segment_track(points)

    assert find_segment(segments, points[0].ts).segment_type == SEGMENT_MOVE


def test_find_segment_returns_none_outside_the_track():
    """轨迹覆盖不到的时刻要返回 None，调用方好判断。"""
    segments = segment_track(track_with_stop(900))

    assert find_segment(segments, T0 - 10000) is None
    assert find_segment(segments, T0 + 100000) is None


def test_make_stop_segment_helper_is_consistent():
    """顺手验一下测试工具自己：造出来的停留段字段要自洽。"""
    segment = make_stop_segment(39.9, 116.4, 900)

    assert segment.segment_type == SEGMENT_STOP
    assert segment.duration_s == segment.end_ts - segment.start_ts
