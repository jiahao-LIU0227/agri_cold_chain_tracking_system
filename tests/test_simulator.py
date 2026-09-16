"""模拟器的测试。

这个文件里最重要的一条是 test_simulator_plants_cases_the_classifier_rediscovers：
模拟器知道「标准答案」，它在轨迹里埋了红灯、加油、中途装卸、路边久停几种情况，
而分类算法只能看轨迹点，不许偷看 STOP_PLAN。两边独立算出来还能对得上，
才说明这套分析真的成立——这也是整个项目最值得写在报告里的一条验证。

模拟器不碰数据库，所以这组测试同样不需要 MySQL。
"""

import pytest

from app.classification import classify_segments
from app.deviation import detect_deviations
from app.geo import RoutePath
from app.models import (POI_GAS_STATION, POI_LOADING_DOCK, SEGMENT_STOP,
                        STOP_ABNORMAL, STOP_REFUEL, STOP_TRANSFER, Poi)
from app.segmentation import segment_track
from app.simulator import STOP_PLAN, build_stops, simulate_task
from conftest import T0, make_route, make_task, straight_route_coords
from config import SAMPLE_INTERVAL, T_MIN

SEED = 42


@pytest.fixture
def route_points():
    """20 公里的直线路线，够长，STOP_PLAN 里那几个比例都能摆得下。"""
    return make_route(straight_route_coords())


def poi_on_route(path, ratio, poi_type, name, offset_m=100.0):
    """在路线沿线 ratio 处、横向挪 offset_m 米的位置放一个 POI。

    横向偏移控制在 POI_STOP_MAX_OFFSET_M(150 米) 以内：车为了停进这个 POI
    会跟着跑出去这么远，超了就多出一个假偏航，测出来的东西就不干净了。
    """
    at_m = path.total_length_m * ratio
    latitude, longitude, _ = path.position_at(at_m)
    latitude, longitude = path.offset_point(
        latitude, longitude, offset_m, path.normal_angle_at(at_m))
    return Poi(name=name, poi_type=poi_type, latitude=latitude, longitude=longitude,
               radius_m=200, source="manual")


@pytest.fixture
def pois(route_points):
    """加油站和装卸点各一个，位置对应 STOP_PLAN 里那两个比例。"""
    path = RoutePath([(p.latitude, p.longitude) for p in route_points])
    return [
        poi_on_route(path, 0.09, POI_GAS_STATION, "测试加油站", offset_m=100.0),
        poi_on_route(path, 0.62, POI_LOADING_DOCK, "测试中转仓", offset_m=-100.0),
    ]


# ============================================================
# 固定种子可复现
# ============================================================

def test_same_seed_gives_the_same_track(route_points, pois):
    """同一个种子跑两遍，轨迹完全一样。

    演示、截图、测试都要能复现，所以模拟器里所有随机数都走同一个种子。
    """
    task = make_task()

    first = simulate_task(task, route_points, pois, seed=SEED)
    second = simulate_task(task, route_points, pois, seed=SEED)

    assert len(first) == len(second)
    assert [p.ts for p in first] == [p.ts for p in second]
    assert [p.temperature for p in first] == [p.temperature for p in second]
    assert [p.latitude for p in first] == [p.latitude for p in second]


def test_different_seed_gives_a_different_track(route_points, pois):
    """换个种子结果就不一样，否则「随机」是假的。"""
    task = make_task()

    first = simulate_task(task, route_points, pois, seed=1)
    second = simulate_task(task, route_points, pois, seed=2)

    assert [p.temperature for p in first] != [p.temperature for p in second]


# ============================================================
# 轨迹的形状
# ============================================================

def test_track_is_sampled_at_a_fixed_interval(route_points, pois):
    """采样间隔固定：一个点一个 SAMPLE_INTERVAL 秒，不多不少。"""
    points = simulate_task(make_task(), route_points, pois, seed=SEED)

    gaps = {b.ts - a.ts for a, b in zip(points, points[1:])}
    assert gaps == {SAMPLE_INTERVAL}


def test_track_starts_at_the_planned_start_time(route_points, pois):
    points = simulate_task(make_task(), route_points, pois, seed=SEED)

    assert points[0].ts == T0


def test_track_starts_and_ends_on_the_route(route_points, pois):
    """车从起点出发、到终点停下，首尾要贴在路线上。"""
    points = simulate_task(make_task(), route_points, pois, seed=SEED)
    path = RoutePath([(p.latitude, p.longitude) for p in route_points])

    assert path.distance_to_route_m(points[0].latitude, points[0].longitude) < 50
    assert path.distance_to_route_m(points[-1].latitude, points[-1].longitude) < 50


def test_temperature_starts_within_the_allowed_range(route_points, pois):
    """出发时温度是合格的，异常是后面自己走出来的，不是一上来就不对。"""
    task = make_task()
    points = simulate_task(task, route_points, pois, seed=SEED)

    assert task.temp_min <= points[0].temperature <= task.temp_max


def test_simulator_needs_a_real_route(pois):
    """路线不足两个点没法走，要明确报错。"""
    with pytest.raises(ValueError):
        simulate_task(make_task(), [], pois, seed=SEED)


# ============================================================
# 埋进去的几种情况，分类算法要能自己找出来
# ============================================================

def test_stop_plan_covers_the_cases_from_the_readme():
    """STOP_PLAN 本身要覆盖到想要的几种情况，改坏了下面那条测试就没意义了。

    这是给测试自己上的一道保险：计划表里少了「没有 POI 的路边久停」，
    下面那条测试就会悄悄失去意义，所以先把它钉住。
    """
    durations = sorted(item["duration_s"] for item in STOP_PLAN)
    poi_types = {item["poi_type"] for item in STOP_PLAN}

    assert durations == [40, 300, 1500, 2100]
    assert None in poi_types                             # 停在路边的
    assert POI_GAS_STATION in poi_types
    assert any(item["poi_type"] == POI_LOADING_DOCK and item["door_open"]
               for item in STOP_PLAN)                    # 装卸点且开门
    assert any(item["duration_s"] > 1800 and item["poi_type"] is None
               for item in STOP_PLAN)                    # 路边久停


def test_simulator_plants_cases_the_classifier_rediscovers(route_points, pois):
    """模拟器埋的几种情况，分类算法要能独立地重新找出来。

    模拟器知道标准答案（STOP_PLAN），但分类算法只能拿到轨迹点，
    不许偷看那张表。所以两边算出来还能对得上，才说明分析是真的成立：

        300 秒停在加油站       -> 加油
        1500 秒停在装卸点且开门 -> 中途装卸
        2100 秒周围没有 POI     -> 异常停留
        40 秒红灯              -> 不该生成停留

    这也是报告里最值得写的一条验证：不是「跑通了」，而是「算对了」。
    """
    task = make_task()
    points = simulate_task(task, route_points, pois, seed=SEED)

    segments = segment_track(points)
    classify_segments(segments, points, task, pois)

    stops = [s for s in segments if s.segment_type == SEGMENT_STOP]
    stop_types = [s.stop_type for s in stops]

    assert stop_types == [STOP_REFUEL, STOP_TRANSFER, STOP_ABNORMAL], \
        f"分类结果和模拟器埋的情况对不上：{stop_types}"

    # 红灯那 40 秒被合并进行驶段了，所以停留是 3 次而不是 4 次
    assert len(stops) == 3
    assert all(s.duration_s > 0 for s in stops)


def test_discovered_stops_happen_where_the_plan_says(route_points, pois):
    """找出来的停留，位置和时长要落在计划的位置附近。

    只比类型还不够：类型对但位置全错，说明是碰巧蒙对的。
    """
    task = make_task()
    points = simulate_task(task, route_points, pois, seed=SEED)
    path = RoutePath([(p.latitude, p.longitude) for p in route_points])

    segments = segment_track(points)
    classify_segments(segments, points, task, pois)
    stops = [s for s in segments if s.segment_type == SEGMENT_STOP]

    # 计划里那 40 秒红灯不会生成停留（它在上面那条测试里单独验过），
    # 所以配对时把它滤掉，剩下的三个才该一一对上
    planned = [item for item in build_stops(path, pois) if item["duration_s"] >= T_MIN]
    assert len(stops) == len(planned) == 3

    for stop, plan in zip(stops, planned):
        # 停留段的时间范围是它自己那些点的首尾之差，会比计划的少一个采样间隔
        assert stop.duration_s == pytest.approx(plan["duration_s"], abs=SAMPLE_INTERVAL)
        along = path.distance_along_m(stop.start_lat, stop.start_lng)
        assert along == pytest.approx(plan["at_m"], abs=200)


def test_planted_deviation_is_detected(route_points, pois):
    """埋的那一段 450 米偏航要被找出来，而且只找出一次。"""
    points = simulate_task(make_task(), route_points, pois, seed=SEED)

    events = detect_deviations(points, route_points)

    assert len(events) == 1
    assert events[0].max_distance_m == pytest.approx(450.0, abs=60.0)


def test_track_stays_near_the_route_apart_from_the_planned_deviation(route_points, pois):
    """除了计划里那一段，其余时间都该贴着路线走。

    真要有别的地方也飘出去了，说明模拟器自己就不自洽——
    那样后面偏航检测报出来的一堆事件就说不清是谁的问题。
    """
    points = simulate_task(make_task(), route_points, pois, seed=SEED)
    path = RoutePath([(p.latitude, p.longitude) for p in route_points])

    far = [p for p in points
           if path.distance_to_route_m(p.latitude, p.longitude) > 250]
    # DEVIATION_PLAN 里的那一段占全程 8%，450 米偏移，控制在这个量级内
    assert 0 < len(far) < len(points) * 0.15


def test_planted_temperature_excursion_is_detected(route_points, pois):
    """制冷机关掉那一段，温度要真的飘出允许范围。

    温度模型里的升温速率是放大的（见 simulator.py 的说明），
    不然一趟一两个小时的演示运输根本看不出异常。
    """
    task = make_task()
    points = simulate_task(task, route_points, pois, seed=SEED)

    over = [p for p in points if p.temperature > task.temp_max]

    assert over, "整条轨迹温度都没超限，制冷机故障那段没模拟出来"
    assert max(p.temperature for p in points) > task.temp_max + 1.0
