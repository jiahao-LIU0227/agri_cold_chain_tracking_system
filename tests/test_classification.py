"""停留分类的测试。

README 9 要求覆盖的一条是「POI 边界上的点分类一致」：离围栏中心正好等于
半径的点，算在圈内还是圈外，必须是定死的规则，不能有时候算内有时候算外。

顺带把整个判断顺序也测一遍，这个顺序是分类算法的核心（见 classification.py）：

    起终点围栏 -> 加油站/装卸点 POI -> 停太久的异常

用到的点都按大圆距离精确摆放（conftest 里的 east_of），
不然 200 米的边界会偏二十几厘米，测出来的是边界内侧，等于没测边界。
"""

import pytest

from app.classification import classify_segments, classify_stop
from app.geo import haversine
from app.models import (POI_LOADING_DOCK, POI_SERVICE_AREA, SEGMENT_MOVE,
                        STOP_ABNORMAL, STOP_LOAD, STOP_REFUEL, STOP_REST,
                        STOP_TRANSFER, STOP_UNKNOWN, STOP_UNLOAD, Segment)
from conftest import (T0, at_meters, east_of, make_poi, make_stop_segment,
                      make_task, point)
from config import GEOFENCE_RADIUS, T_ABNORMAL

# 分类只用到「这段停留停在哪儿、停了多久、期间开没开门」，
# 所以除了这三点，其余字段随便造。
NORMAL_STOP = 600          # 10 分钟，正常时长，够不到异常门槛


def stop_at(latitude, longitude, duration_s=NORMAL_STOP):
    """一个停在指定位置的停留段。"""
    return make_stop_segment(latitude, longitude, duration_s)


def door_points(door_open, duration_s=NORMAL_STOP):
    """停留期间的两个点，用来体现车门开没开过。"""
    return [point(T0, door_open=door_open), point(T0 + duration_s, door_open=door_open)]


# ============================================================
# 起终点围栏
# ============================================================

def test_stop_at_origin_is_loading(task):
    """停在起点围栏里 = 装货。"""
    latitude, longitude = east_of(task.origin_lat, task.origin_lng, 50.0)

    assert classify_stop(stop_at(latitude, longitude), [], task, []) == STOP_LOAD


def test_stop_just_inside_origin_fence_is_loading(task):
    """贴着围栏内侧（差半米到边界）算装货。

    为什么是「差半米」而不是「正好卡在半径上」：算出来的距离是浮点数，
    按大圆距离精确摆到 200.000000 米，最后一次开方求反三角会差最后一两位，
    落到 200.0000000001 就会被判成圈外。所以边界测试的做法是
    「内侧半米算内、外侧一米算外」，把翻转点夹在半径附近一米之内。
    """
    latitude, longitude = east_of(task.origin_lat, task.origin_lng, GEOFENCE_RADIUS - 0.5)
    assert haversine(latitude, longitude, task.origin_lat, task.origin_lng) < GEOFENCE_RADIUS

    assert classify_stop(stop_at(latitude, longitude), [], task, []) == STOP_LOAD


def test_stop_just_outside_origin_fence_is_not_loading(task):
    """边界外一米就不算装货了。和上一条一起，把翻转点夹在半径附近。"""
    latitude, longitude = east_of(task.origin_lat, task.origin_lng, GEOFENCE_RADIUS + 1.0)
    assert haversine(latitude, longitude, task.origin_lat, task.origin_lng) > GEOFENCE_RADIUS

    assert classify_stop(stop_at(latitude, longitude), [], task, []) == STOP_REST


def test_fence_radius_is_the_flip_point(task):
    """围栏的翻转点就在半径上：半径以内算装货，以外不算。

    同一组距离扫一遍，把「从 load 变成 rest」的那个位置夹住，
    这样半径被改错（比如少写一个 0）会立刻被测出来。
    """
    results = {}
    for distance in (GEOFENCE_RADIUS - 1, GEOFENCE_RADIUS - 0.5,
                     GEOFENCE_RADIUS + 0.5, GEOFENCE_RADIUS + 1):
        latitude, longitude = east_of(task.origin_lat, task.origin_lng, distance)
        results[distance] = classify_stop(stop_at(latitude, longitude), [], task, [])

    assert results[GEOFENCE_RADIUS - 1] == STOP_LOAD
    assert results[GEOFENCE_RADIUS - 0.5] == STOP_LOAD
    assert results[GEOFENCE_RADIUS + 0.5] == STOP_REST
    assert results[GEOFENCE_RADIUS + 1] == STOP_REST


def test_stop_at_destination_is_unloading(task):
    """停在终点围栏里 = 卸货。"""
    latitude, longitude = east_of(task.dest_lat, task.dest_lng, 50.0)

    assert classify_stop(stop_at(latitude, longitude), [], task, []) == STOP_UNLOAD


def test_origin_fence_wins_over_a_nearby_poi(task):
    """判断顺序：先看起终点，再看 POI。

    装货点旁边有个加油站，也不能因为「旁边是加油站」就判成加油。
    起终点是最确定的证据，必须先判掉。
    """
    latitude, longitude = east_of(task.origin_lat, task.origin_lng, 50.0)
    gas = make_poi(east_m=50.0)

    assert classify_stop(stop_at(latitude, longitude), [], task, [gas]) == STOP_LOAD


# ============================================================
# POI 围栏
# ============================================================

def test_stop_inside_gas_station_is_refuel():
    """停在加油站围栏里 = 加油。"""
    gas = make_poi(east_m=5000.0)
    latitude, longitude = east_of(gas.latitude, gas.longitude, 50.0)

    assert classify_stop(stop_at(latitude, longitude), [], make_task(), [gas]) == STOP_REFUEL


def test_poi_boundary_is_inclusive():
    """距离正好等于半径算在圈内，小一毫米就不算 —— 把 <= 这条规则钉死。

    这里不是硬摆一个「正好 200 米」的点（浮点数摆不准，见上面围栏那条的说明），
    而是反过来：先算出停在 120 米处，再把围栏半径设成这个距离本身。
    这样「距离 == 半径」是精确成立的，能真正测到边界上的判断。
    """
    gas = make_poi(east_m=5000.0)
    latitude, longitude = east_of(gas.latitude, gas.longitude, 120.0)
    distance = haversine(latitude, longitude, gas.latitude, gas.longitude)
    stop = stop_at(latitude, longitude)

    gas.radius_m = distance                      # 半径正好等于距离
    assert classify_stop(stop, [], make_task(), [gas]) == STOP_REFUEL

    gas.radius_m = distance - 0.001              # 半径比距离小一毫米
    assert classify_stop(stop, [], make_task(), [gas]) == STOP_REST

    gas.radius_m = distance + 0.001              # 再大回去
    assert classify_stop(stop, [], make_task(), [gas]) == STOP_REFUEL


def test_stop_just_outside_gas_station_boundary_is_not_refuel():
    """边界外一米就不认这个加油站了。"""
    gas = make_poi(east_m=5000.0)
    latitude, longitude = east_of(gas.latitude, gas.longitude, gas.radius_m + 1.0)
    assert haversine(latitude, longitude, gas.latitude, gas.longitude) > gas.radius_m

    assert classify_stop(stop_at(latitude, longitude), [], make_task(), [gas]) == STOP_REST


def test_each_poi_uses_its_own_radius():
    """每个 POI 有自己的半径，不能拿一个固定半径一刀切。

    小加油站的半径是 100 米，在它 150 米外就不算在圈里。
    """
    small_gas = make_poi(east_m=5000.0, radius_m=100)
    latitude, longitude = east_of(small_gas.latitude, small_gas.longitude, 150.0)

    assert classify_stop(stop_at(latitude, longitude), [], make_task(), [small_gas]) == STOP_REST


# ============================================================
# 中途装卸：POI 加上「开过门」才算数
# ============================================================

def test_loading_dock_with_door_open_is_transfer():
    """在装卸点，而且停留期间开过车门 —— 这才叫中途装卸。"""
    dock = make_poi(name="中转仓", poi_type=POI_LOADING_DOCK, east_m=8000.0)
    latitude, longitude = east_of(dock.latitude, dock.longitude, 30.0)

    result = classify_stop(stop_at(latitude, longitude), door_points(True),
                           make_task(), [dock])

    assert result == STOP_TRANSFER


def test_loading_dock_without_door_open_is_unknown():
    """在装卸点但没开过门，说明不了在装卸，只能老实说不知道。

    这是故意留的「不确定」出口：宁可说不知道，也不要瞎猜成装卸。
    """
    dock = make_poi(name="中转仓", poi_type=POI_LOADING_DOCK, east_m=8000.0)
    latitude, longitude = east_of(dock.latitude, dock.longitude, 30.0)

    result = classify_stop(stop_at(latitude, longitude), door_points(False),
                           make_task(), [dock])

    assert result == STOP_UNKNOWN


def test_door_opened_at_any_moment_counts():
    """只要停留期间开过一次门就算开过，不用全程都开着。"""
    dock = make_poi(name="中转仓", poi_type=POI_LOADING_DOCK, east_m=8000.0)
    latitude, longitude = east_of(dock.latitude, dock.longitude, 30.0)

    points = [point(T0, door_open=False),
              point(T0 + 300, door_open=True),
              point(T0 + 600, door_open=False)]

    assert classify_stop(stop_at(latitude, longitude), points,
                         make_task(), [dock]) == STOP_TRANSFER


def test_door_opened_outside_the_stop_does_not_count():
    """别的时间开过门不算数——开门和这段停留必须在时间上对得上。"""
    dock = make_poi(name="中转仓", poi_type=POI_LOADING_DOCK, east_m=8000.0)
    latitude, longitude = east_of(dock.latitude, dock.longitude, 30.0)

    # 开门的点在停留开始前很久，不在这一段的时间范围里
    points = [point(T0 - 5000, door_open=True), point(T0, door_open=False)]

    assert classify_stop(stop_at(latitude, longitude), points,
                         make_task(), [dock]) == STOP_UNKNOWN


def test_door_open_without_a_loading_dock_is_not_transfer():
    """光开门、附近没有装卸点，不能凭猜判成装卸。"""
    latitude, longitude = at_meters(8000, 0)

    result = classify_stop(stop_at(latitude, longitude), door_points(True), make_task(), [])

    assert result == STOP_REST


# ============================================================
# 异常停留：没 POI 又停很久
# ============================================================

def test_long_stop_without_any_poi_is_abnormal():
    """路边停了半个多小时，周围什么 POI 都没有 —— 要找的就是这种。"""
    latitude, longitude = at_meters(8000, 0)

    result = classify_stop(stop_at(latitude, longitude, T_ABNORMAL + 1), [], make_task(), [])

    assert result == STOP_ABNORMAL


def test_stop_of_exactly_T_ABNORMAL_is_not_abnormal():
    """恰好 T_ABNORMAL 秒不算异常（规则是「超过」，不是「不低于」）。"""
    latitude, longitude = at_meters(8000, 0)

    result = classify_stop(stop_at(latitude, longitude, T_ABNORMAL), [], make_task(), [])

    assert result == STOP_REST


def test_long_stop_next_to_a_poi_is_not_abnormal():
    """旁边有加油站，停再久也不算「异常」——那是加油，不是乱停。"""
    gas = make_poi(east_m=8000.0)
    latitude, longitude = east_of(gas.latitude, gas.longitude, 20.0)

    result = classify_stop(stop_at(latitude, longitude, T_ABNORMAL * 2),
                           [], make_task(), [gas])

    assert result == STOP_REFUEL


# ============================================================
# 最近的 POI 说了算
# ============================================================

def test_nearest_poi_wins():
    """两个 POI 都在圈里时，按最近的判。"""
    gas = make_poi(name="加油站", east_m=8000.0)
    dock = make_poi(name="中转仓", poi_type=POI_LOADING_DOCK, east_m=8120.0)

    # 停在加油站旁边 10 米处：最近的 POI 是加油站
    latitude, longitude = east_of(gas.latitude, gas.longitude, 10.0)
    assert classify_stop(stop_at(latitude, longitude), door_points(True),
                         make_task(), [gas, dock]) == STOP_REFUEL

    # 停在装卸点旁边 10 米处：最近的 POI 换成装卸点，开门就判中途装卸
    latitude, longitude = east_of(dock.latitude, dock.longitude, 10.0)
    assert classify_stop(stop_at(latitude, longitude), door_points(True),
                         make_task(), [gas, dock]) == STOP_TRANSFER


def test_poi_order_in_the_list_does_not_matter():
    """POI 列表的先后顺序不能影响结果，分类要自己按距离排。"""
    gas = make_poi(name="加油站", east_m=8000.0)
    dock = make_poi(name="中转仓", poi_type=POI_LOADING_DOCK, east_m=8120.0)
    latitude, longitude = east_of(gas.latitude, gas.longitude, 10.0)

    forward = classify_stop(stop_at(latitude, longitude), door_points(True),
                            make_task(), [gas, dock])
    backward = classify_stop(stop_at(latitude, longitude), door_points(True),
                             make_task(), [dock, gas])

    assert forward == backward == STOP_REFUEL


def test_service_area_poi_is_not_special():
    """只定义了加油站和装卸点两类 POI，其它类型不参与判断，按普通停留算。"""
    other = make_poi(name="服务区", poi_type=POI_SERVICE_AREA, east_m=8000.0)
    latitude, longitude = east_of(other.latitude, other.longitude, 20.0)

    result = classify_stop(stop_at(latitude, longitude), [], make_task(), [other])

    assert result == STOP_REST


# ============================================================
# classify_segments：批量填类型
# ============================================================

def test_classify_segments_fills_stops_and_skips_moves():
    """停留段填上类型，行驶段保持 None。"""
    task = make_task()
    latitude, longitude = east_of(task.origin_lat, task.origin_lng, 50.0)

    segments = [
        Segment(segment_type=SEGMENT_MOVE, start_ts=T0, end_ts=T0 + 600,
                duration_s=600, start_lat=latitude, start_lng=longitude,
                end_lat=latitude, end_lng=longitude),
        stop_at(latitude, longitude),
    ]

    classify_segments(segments, [], task, [])

    assert segments[0].stop_type is None
    assert segments[1].stop_type == STOP_LOAD


def test_classify_segments_handles_empty_list():
    assert classify_segments([], [], make_task(), []) == []
