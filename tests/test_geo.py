"""地理计算的测试。

重点两条（README 9）：

    - 同一个点算两次距离，结果必须一模一样
    - 向北移动 1000 米，算出来的距离应该在 1000 米附近

用到的基准点和构造工具都在 conftest.py 里。
"""

import math

import pytest

from app.geo import (EARTH_RADIUS_M, RoutePath, bearing, from_local_meters,
                     haversine, to_local_meters)
from conftest import (LAT0, LNG0, at_meters, east_of, north_of,
                      straight_route_coords)


# ============================================================
# haversine
# ============================================================

def test_north_1000m_is_about_1000m():
    """向北移动 1000 米，算出来就该是 1000 米。

    正北方向最干净：沿经线走，纬度每差 1 度就是地球半径的 1 弧度长，
    所以这个方向上 haversine 是精确的，不是近似。
    """
    start = (LAT0, LNG0)
    end = north_of(*start, 1000.0)

    assert haversine(*start, *end) == pytest.approx(1000.0, abs=0.01)


def test_same_distance_computed_twice_is_identical():
    """同一个点算两次，结果必须完全一致（不能有随机性或者缓存污染）。"""
    a = at_meters(0, 0)
    b = at_meters(3000, 1000)

    first = haversine(*a, *b)
    second = haversine(*a, *b)

    assert first == second


def test_distance_is_symmetric():
    """A 到 B 和 B 到 A 是同一个距离。"""
    a = at_meters(0, 0)
    b = at_meters(5000, -2000)

    assert haversine(*a, *b) == pytest.approx(haversine(*b, *a), abs=1e-9)


def test_distance_to_self_is_zero():
    assert haversine(LAT0, LNG0, LAT0, LNG0) == 0.0


def test_east_1000m_is_about_1000m():
    """正东方向也验一下。

    这个方向上 haversine 算的是球面上的大圆弧，和「1 度经度 ≈ 111320·cos(φ) 米」
    的平面近似差千分之一左右，所以容差给宽一点。
    """
    start = (LAT0, LNG0)
    end = east_of(*start, 1000.0)

    assert haversine(*start, *end) == pytest.approx(1000.0, abs=1.0)


# ============================================================
# 经纬度 <-> 米制平面
# ============================================================

def test_meters_round_trip():
    """经纬度转成米再转回来，应该回到原地。"""
    x, y = to_local_meters(LAT0, LNG0, LAT0)
    lat, lng = from_local_meters(x, y, LAT0)

    assert lat == pytest.approx(LAT0, abs=1e-9)
    assert lng == pytest.approx(LNG0, abs=1e-9)


def test_east_offset_shortens_a_degree_of_longitude():
    """1 度经度对应的距离比 1 度纬度短，比例就是 cos(纬度)。

    这也正是 to_local_meters 里要乘 cos(ref_latitude) 的原因：
    不乘的话，同样度数的东西方向会被算得比实际长。
    """
    x1, _ = to_local_meters(LAT0, LNG0, LAT0)
    x2, _ = to_local_meters(LAT0, LNG0 + 1.0, LAT0)

    assert (x2 - x1) == pytest.approx(111320.0 * math.cos(math.radians(LAT0)), rel=1e-6)
    assert (x2 - x1) < 111320.0


def test_offset_point_moves_the_right_distance():
    """往正东平移 800 米，落点就应该在正东 800 米处。"""
    path = RoutePath(straight_route_coords())
    lat, lng = path.offset_point(LAT0, LNG0, 800.0, angle_deg=90.0)

    # offset_point 用的是平面近似，和 haversine 会差千分之一，容差按米给
    assert haversine(LAT0, LNG0, lat, lng) == pytest.approx(800.0, abs=2.0)
    assert lng > LNG0
    assert lat == pytest.approx(LAT0, abs=1e-6)


# ============================================================
# bearing
# ============================================================

def test_bearing_points():
    """四个正方向的航向角：北 0、东 90、南 180、西 270。"""
    north = north_of(LAT0, LNG0, 1000.0)
    east = east_of(LAT0, LNG0, 1000.0)

    assert bearing(LAT0, LNG0, *north) == pytest.approx(0.0, abs=0.5)
    assert bearing(LAT0, LNG0, *east) == pytest.approx(90.0, abs=0.5)
    assert bearing(*north, LAT0, LNG0) == pytest.approx(180.0, abs=0.5)
    assert bearing(*east, LAT0, LNG0) == pytest.approx(270.0, abs=0.5)


# ============================================================
# RoutePath
# ============================================================

def test_route_length_matches_point_by_point_sum():
    """路线总长要等于逐点 haversine 之和。"""
    coords = [at_meters(0, 0), at_meters(3000, 0), at_meters(3000, 4000)]
    path = RoutePath(coords)

    expected = haversine(*coords[0], *coords[1]) + haversine(*coords[1], *coords[2])
    assert path.total_length_m == pytest.approx(expected, rel=1e-9)


def test_position_at_walks_along_the_route():
    """按里程取位置：0 米在起点，总长处在终点，中间按比例插值。"""
    coords = [at_meters(0, 0), at_meters(10000, 0)]
    path = RoutePath(coords)

    start_lat, start_lng, _ = path.position_at(0.0)
    mid_lat, mid_lng, _ = path.position_at(path.total_length_m / 2)
    end_lat, end_lng, _ = path.position_at(path.total_length_m)

    assert (start_lat, start_lng) == pytest.approx(coords[0], abs=1e-9)
    assert (end_lat, end_lng) == pytest.approx(coords[1], abs=1e-6)
    # 中点应该落在首尾之间
    assert start_lng < mid_lng < end_lng


def test_position_at_clamps_out_of_range():
    """里程超出路线范围要钳到起点/终点，不能抛异常也不能算到天上去。"""
    coords = [at_meters(0, 0), at_meters(10000, 0)]
    path = RoutePath(coords)

    assert path.position_at(-5000.0)[:2] == pytest.approx(coords[0], abs=1e-9)
    assert path.position_at(path.total_length_m + 5000.0)[:2] == pytest.approx(coords[1], abs=1e-6)


def test_distance_to_route_is_zero_on_the_route():
    """路线上的点到路线距离应该是 0。"""
    path = RoutePath(straight_route_coords())
    lat, lng = at_meters(7000, 0)

    assert path.distance_to_route_m(lat, lng) == pytest.approx(0.0, abs=1e-6)


def test_distance_to_route_equals_sideways_offset():
    """从路线上横着挪开 500 米，点到路线的距离就该是 500 米。"""
    path = RoutePath(straight_route_coords())
    lat, lng = at_meters(7000, 500)

    assert path.distance_to_route_m(lat, lng) == pytest.approx(500.0, abs=0.5)


def test_distance_to_route_is_repeatable():
    """同一个点算两次，结果必须一模一样。"""
    path = RoutePath(straight_route_coords())
    lat, lng = at_meters(7000, 350)

    assert path.distance_to_route_m(lat, lng) == path.distance_to_route_m(lat, lng)


def test_distance_along_grows_with_progress():
    """沿线里程：越靠后的点，投影到路线上的里程越大。"""
    path = RoutePath(straight_route_coords())

    near = path.distance_along_m(*at_meters(1000, 0))
    far = path.distance_along_m(*at_meters(9000, 0))

    assert near == pytest.approx(1000.0, abs=1.0)
    assert far == pytest.approx(9000.0, abs=1.0)
    assert near < far


def test_route_path_needs_two_points():
    """只有一个点的「折线」算不了距离，要明确报错而不是给出乱七八糟的结果。"""
    with pytest.raises(ValueError):
        RoutePath([(LAT0, LNG0)])


def test_earth_radius_constant_is_sane():
    """兜底：地球半径用错了，上面所有距离都会跟着错。"""
    assert EARTH_RADIUS_M == pytest.approx(6371008.8, rel=1e-6)
