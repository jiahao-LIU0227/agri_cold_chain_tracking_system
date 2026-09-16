"""温度异常的测试。

README 9 要求覆盖的一条是「温度恰好等于阈值时的判断规则要统一」：
温度正好等于上限或下限，到底算不算超限，全系统必须是一个说法。

本项目定的规则是「等于阈值算达标」——超限指的是**超出**范围，
所以 temp_min <= 温度 <= temp_max 都算正常。下面两条测试把这条规则钉死。

另一条要求是「事件要能说清当时在哪儿、在干什么」，所以最后几组测试
专门验证和停留段的关联。
"""

import pytest

from app.models import EVENT_TEMPERATURE, SEGMENT_STOP
from app.segmentation import segment_track
from app.temperature import detect_temperature_events
from conftest import T0, make_task, make_track, point
from config import SAMPLE_INTERVAL, T_TEMP

CRUISE = 15.0
TEMP_MIN = -25.0       # 和 conftest.make_task 里的冷冻货阈值一致
TEMP_MAX = -18.0


def track_temps(times, temperatures, speeds=None, **kwargs):
    """按给定的时间戳和温度造点，位置沿正东匀速前进。"""
    speeds = speeds if speeds is not None else [CRUISE] * len(times)
    return [
        point(ts, speed=speeds[i], east_m=CRUISE * (ts - times[0]),
              temperature=temperatures[i], **kwargs)
        for i, ts in enumerate(times)
    ]


def over_limit_track(seconds, interval=SAMPLE_INTERVAL, temperature=TEMP_MAX + 1.0):
    """一条「全程超限 seconds 秒」的轨迹，首尾时间戳之差恰好是 seconds。"""
    times = [T0 + interval * i for i in range(seconds // interval)]
    times.append(T0 + seconds)
    return track_temps(times, [temperature] * len(times))


def spike_track(seconds, temperature=TEMP_MAX + 1.0, interval=SAMPLE_INTERVAL):
    """中间超限一小段，两头都在允许范围内。"""
    before = [T0 + interval * i for i in range(5)]
    during = [before[-1] + interval * (i + 1) for i in range(seconds // interval + 1)]
    after = [during[-1] + interval * (i + 1) for i in range(5)]

    times = before + during + after
    temperatures = [TEMP_MAX - 1.0] * 5 + [temperature] * len(during) + [TEMP_MAX - 1.0] * 5
    return track_temps(times, temperatures)


# ============================================================
# 阈值边界：等于阈值算达标
# ============================================================

def test_temperature_exactly_at_the_upper_limit_is_ok():
    """温度正好等于上限：算达标，不生成事件。

    制冷机把温度压在 -18.0℃，这本来就是合格线上的正常状态。
    """
    points = track_temps([T0 + 60 * i for i in range(11)], [TEMP_MAX] * 11)

    assert detect_temperature_events(make_task(), points) == []


def test_temperature_exactly_at_the_lower_limit_is_ok():
    """温度正好等于下限：同样算达标。"""
    points = track_temps([T0 + 60 * i for i in range(11)], [TEMP_MIN] * 11)

    assert detect_temperature_events(make_task(), points) == []


def test_temperature_just_above_the_upper_limit_creates_an_event():
    """高出上限一点点，只要持续够久就要报。和「等于上限」那条一起把规则夹死。"""
    points = over_limit_track(T_TEMP + 60, temperature=TEMP_MAX + 0.1)

    events = detect_temperature_events(make_task(), points)

    assert len(events) == 1
    assert events[0].temp_max == pytest.approx(TEMP_MAX + 0.1)


def test_temperature_just_below_the_lower_limit_creates_an_event():
    """低于下限也要报：冷冻货怕的不是不冷，是反复冻融。"""
    points = over_limit_track(T_TEMP + 60, temperature=TEMP_MIN - 0.1)

    events = detect_temperature_events(make_task(), points)

    assert len(events) == 1
    assert events[0].temp_min == pytest.approx(TEMP_MIN - 0.1)


def test_temperature_inside_the_range_creates_nothing():
    """老老实实待在范围内，一个事件都不该有。"""
    points = track_temps([T0 + 60 * i for i in range(11)], [TEMP_MAX - 2.0] * 11)

    assert detect_temperature_events(make_task(), points) == []


# ============================================================
# T_TEMP 的门槛
# ============================================================

def test_excursion_of_exactly_T_TEMP_is_ignored():
    """恰好持续 T_TEMP 秒：规则是「超过」，等于还不算，瞬时抖动就该被滤掉。"""
    points = over_limit_track(T_TEMP)

    assert detect_temperature_events(make_task(), points) == []


def test_excursion_of_T_TEMP_plus_one_second_is_reported():
    """多一秒就报。和上一条一起把门槛卡在 T_TEMP 上。"""
    points = over_limit_track(T_TEMP + 1)

    events = detect_temperature_events(make_task(), points)

    assert len(events) == 1
    assert events[0].duration_s == T_TEMP + 1


def test_short_spike_is_ignored():
    """开门搬货那两分钟温度窜一下，这是正常的，不算异常事件。"""
    points = spike_track(120)

    assert detect_temperature_events(make_task(), points) == []


def test_long_spike_is_reported():
    """同一个位置窜十分钟，那就是制冷有问题了。"""
    points = spike_track(600)

    events = detect_temperature_events(make_task(), points)

    assert len(events) == 1
    assert events[0].duration_s == 600


# ============================================================
# 事件的内容
# ============================================================

def test_event_records_start_end_and_extremes():
    """事件的起止时间、持续时长、期间最低最高温都要对得上。"""
    times = [T0 + 200 * i for i in range(6)]
    temperatures = [TEMP_MAX - 1.0, TEMP_MAX - 1.0,
                    TEMP_MAX + 2.0, TEMP_MAX + 5.0, TEMP_MAX + 1.0,
                    TEMP_MAX - 1.0]
    points = track_temps(times, temperatures)

    event = detect_temperature_events(make_task(), points)[0]

    assert event.event_type == EVENT_TEMPERATURE
    assert event.start_ts == times[2]
    assert event.end_ts == times[4]
    assert event.duration_s == times[4] - times[2]
    assert event.temp_max == pytest.approx(TEMP_MAX + 5.0)
    assert event.temp_min == pytest.approx(TEMP_MAX + 1.0)


def test_event_marks_the_peak_position():
    """事件在地图上的位置取「超得最狠的那一点」，不是随便挑一个。"""
    times = [T0 + 200 * i for i in range(6)]
    temperatures = [TEMP_MAX - 1.0, TEMP_MAX - 1.0,
                    TEMP_MAX + 2.0, TEMP_MAX + 5.0, TEMP_MAX + 1.0,
                    TEMP_MAX - 1.0]
    points = track_temps(times, temperatures)

    event = detect_temperature_events(make_task(), points)[0]
    peak = points[3]

    assert event.latitude == pytest.approx(peak.latitude, abs=1e-9)
    assert event.longitude == pytest.approx(peak.longitude, abs=1e-9)


def test_two_excursions_make_two_events():
    """温度窜上去、压下来、又窜上去，这是两次，不能合并。"""
    times = [T0 + 100 * i for i in range(15)]
    temperatures = [TEMP_MAX - 1.0] * 15
    for i in range(2, 8):
        temperatures[i] = TEMP_MAX + 2.0
    for i in range(10, 15):
        temperatures[i] = TEMP_MAX + 2.0
    points = track_temps(times, temperatures)

    events = detect_temperature_events(make_task(), points)

    assert len(events) == 2
    assert events[0].start_ts < events[1].start_ts


def test_excursion_at_the_end_of_the_track_is_not_lost():
    """轨迹最后还在超限，没有「恢复正常」的点收尾，也不能漏掉。"""
    times = [T0 + 100 * i for i in range(12)]
    temperatures = [TEMP_MAX - 1.0] * 6 + [TEMP_MAX + 3.0] * 6
    points = track_temps(times, temperatures)

    events = detect_temperature_events(make_task(), points)

    assert len(events) == 1
    assert events[0].end_ts == times[-1]


# ============================================================
# 和分段关联：超限的时候车在哪儿、在干什么
# ============================================================

def test_excursion_while_driving_is_not_during_stop():
    """开着车温度超了：during_stop 要是 False。"""
    times = [T0 + 60 * i for i in range(11)]
    points = track_temps(times, [TEMP_MAX + 2.0] * 11, speeds=[CRUISE] * 11)
    segments = segment_track(points)

    event = detect_temperature_events(make_task(), points, segments)[0]

    assert segments[0].segment_type != SEGMENT_STOP
    assert event.during_stop is False


def test_excursion_while_stopped_is_during_stop():
    """停车期间温度超了：这是最有价值的一条 —— 停在哪儿温度不对，得去查。

    车门开着搬货、熄火停车都会让温度飘上去，所以「超限」和「停留」
    对上了才说明问题。
    """
    before = [T0 + 60 * i for i in range(4)]
    during = [before[-1] + 60 * (i + 1) for i in range(7)]     # 停 6 分钟
    after = [during[-1] + 60 * (i + 1) for i in range(3)]

    times = before + during + after
    temperatures = [TEMP_MAX - 1.0] * 4 + [TEMP_MAX + 3.0] * 7 + [TEMP_MAX - 1.0] * 3
    speeds = [CRUISE] * 4 + [0.0] * 7 + [CRUISE] * 3

    points = track_temps(times, temperatures, speeds=speeds)
    segments = segment_track(points)
    events = detect_temperature_events(make_task(), points, segments)

    assert len(events) == 1
    assert events[0].during_stop is True

    stop_segment = [s for s in segments if s.segment_type == SEGMENT_STOP][0]
    assert stop_segment.start_ts <= events[0].start_ts
    assert events[0].end_ts <= stop_segment.end_ts


# ============================================================
# 脏数据不能把程序搞崩
# ============================================================

def test_empty_points_returns_nothing():
    assert detect_temperature_events(make_task(), []) == []


def test_single_point_track_does_not_crash():
    points = [point(T0, speed=CRUISE, temperature=TEMP_MAX + 5.0)]

    assert detect_temperature_events(make_task(), points) == []


def test_unsorted_points_are_handled():
    """输入顺序乱的要先排好序，结果不能因为顺序变化。"""
    points = over_limit_track(T_TEMP + 300)

    ordered = detect_temperature_events(make_task(), points)
    shuffled = detect_temperature_events(make_task(), list(reversed(points)))

    assert len(ordered) == len(shuffled) == 1
    assert ordered[0].start_ts == shuffled[0].start_ts


def test_door_open_and_fridge_off_do_not_affect_the_result():
    """开关门、制冷机状态只是记录，判超限只看温度本身，不用它们做条件。

    这些字段是留给报告里解释原因的，不是算法的输入。
    """
    times = [T0 + 60 * i for i in range(11)]
    temperatures = [TEMP_MAX + 2.0] * 11

    plain = detect_temperature_events(make_task(), track_temps(times, temperatures))
    with_flags = detect_temperature_events(make_task(), track_temps(
        times, temperatures, door_open=True, refrigerator_on=False))

    assert len(plain) == len(with_flags) == 1
    assert plain[0].duration_s == with_flags[0].duration_s


def test_chilled_cargo_uses_its_own_range():
    """冷藏货（0~4℃）和冷冻货走同一套逻辑，只是阈值不同。

    -20℃ 对冷冻货是正常温度，对冷藏货就是严重超限。
    """
    task = make_task(cargo_type="chilled", temp_min=0.0, temp_max=4.0)
    points = over_limit_track(T_TEMP + 60, temperature=-20.0)

    events = detect_temperature_events(task, points)

    assert len(events) == 1
    assert events[0].temp_min == pytest.approx(-20.0)


def test_frozen_cargo_accepts_the_same_temperature():
    """同一批 -20℃ 的数据，换成冷冻货的任务就完全正常。"""
    points = over_limit_track(T_TEMP + 60, temperature=-20.0)

    assert detect_temperature_events(make_task(), points) == []


def test_events_are_sorted_by_time():
    """事件按时间升序排好，面板直接顺着显示。"""
    times = [T0 + 100 * i for i in range(15)]
    temperatures = [TEMP_MAX - 1.0] * 15
    # 两段超限都要超过 T_TEMP 才会成为事件，所以各占 5 个点（跨度 400 秒）
    for i in (2, 3, 4, 5, 6):
        temperatures[i] = TEMP_MAX + 2.0
    for i in (9, 10, 11, 12, 13):
        temperatures[i] = TEMP_MAX + 2.0

    events = detect_temperature_events(make_task(), track_temps(times, temperatures))

    assert len(events) == 2
    assert [e.start_ts for e in events] == sorted(e.start_ts for e in events)


def test_make_track_default_temperature_is_within_range():
    """顺手验一下测试工具：make_track 默认造的点温度是合格的。"""
    points = make_track([(900, CRUISE)])

    assert detect_temperature_events(make_task(), points) == []
