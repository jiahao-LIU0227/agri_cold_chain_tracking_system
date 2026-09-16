"""地理计算。

本项目所有经纬度都是 GCJ-02（高德坐标系），见 README 6.5。
偏航检测和 POI 围栏判断都要算距离，统一用这里的函数，不要各写一份。
"""

import math

from shapely.geometry import LineString, Point

EARTH_RADIUS_M = 6371008.8    # 地球平均半径，米
METERS_PER_DEGREE = 111320.0  # 1 度纬度约等于多少米


def haversine(lat1, lng1, lat2, lng2):
    """两点之间的大圆距离，单位米。

    两个经纬度点之间的真实距离要按球面算，直接拿经纬度差做勾股定理是不对的
    （越靠近两极，1 度经度对应的实际距离越短）。
    """
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lng2 - lng1)

    a = (math.sin(dphi / 2) ** 2
         + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2)
    return 2 * EARTH_RADIUS_M * math.asin(math.sqrt(a))


def to_local_meters(latitude, longitude, ref_latitude):
    """把经纬度换算成以 ref_latitude 为基准的局部平面坐标，单位米。

    为什么需要这一步：Shapely 在平面上算距离，直接喂经纬度得到的是「度」不是「米」，
    200 米的缓冲区阈值就没法设。在纬度 φ 处：

        1 度纬度 ≈ 111320 米
        1 度经度 ≈ 111320 * cos(φ) 米

    换算之后交给 Shapely，算出来的距离单位就是米。

    返回 (x, y)，x 对应经度方向，y 对应纬度方向。

    注意：这是等距圆柱投影的近似，只在中小范围（几十公里）够用。
    本项目的单车单任务场景没问题，跨几百公里的话误差会明显变大。
    """
    x = longitude * METERS_PER_DEGREE * math.cos(math.radians(ref_latitude))
    y = latitude * METERS_PER_DEGREE
    return x, y


def from_local_meters(x, y, ref_latitude):
    """to_local_meters 的反向换算：局部平面坐标 -> (纬度, 经度)。

    模拟器要往路线旁边「挪」一段距离造偏航，就是在米制坐标里挪完再换回来。
    """
    latitude = y / METERS_PER_DEGREE
    longitude = x / (METERS_PER_DEGREE * math.cos(math.radians(ref_latitude)))
    return latitude, longitude


def bearing(lat1, lng1, lat2, lng2):
    """从点 1 指向点 2 的航向角，0~360 度，正北为 0，顺时针增大。

    和地图上的车头方向一致，直接用经纬度算，不需要转米制。
    """
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    dlambda = math.radians(lng2 - lng1)

    x = math.sin(dlambda) * math.cos(phi2)
    y = (math.cos(phi1) * math.sin(phi2)
         - math.sin(phi1) * math.cos(phi2) * math.cos(dlambda))
    return (math.degrees(math.atan2(x, y)) + 360) % 360


class RoutePath:
    """规划路线，可以按「走了多少米」查到具体位置。

    模拟器沿路线前进、偏航检测算点到路线的距离，都需要这类计算，
    所以统一放在这里，不要在别处再写一遍。

    内部把经纬度转成以路线中点为基准的局部米制坐标，
    距离计算都在米制下做（原因见 to_local_meters 的说明）。
    """

    def __init__(self, points):
        """points 是 [(纬度, 经度), ...]，按行驶顺序排好。"""
        if len(points) < 2:
            raise ValueError("规划路线至少要有 2 个点")

        self.points = [(float(lat), float(lng)) for lat, lng in points]
        # 用整条路线的平均纬度做投影基准，误差最小的位置在路线中部
        self.ref_latitude = sum(p[0] for p in self.points) / len(self.points)

        self._xy = [to_local_meters(lat, lng, self.ref_latitude)
                    for lat, lng in self.points]
        # 米制下的折线，交给 Shapely 算点到路线的距离
        self._line = LineString(self._xy)

        # 每个折线点距离起点的累计米数，用来按里程定位
        self._cumulative = [0.0]
        for i in range(1, len(self.points)):
            self._cumulative.append(
                self._cumulative[-1] + haversine(*self.points[i - 1], *self.points[i])
            )

    @property
    def total_length_m(self):
        """路线总长度，米。"""
        return self._cumulative[-1]

    def position_at(self, distance_m):
        """走到 distance_m 米处的位置，返回 (纬度, 经度, 航向角)。

        超出路线范围就钳到起点或终点。返回航向角是为了直接填进轨迹点的 heading。
        """
        distance_m = max(0.0, min(float(distance_m), self.total_length_m))

        # 找到 distance_m 落在哪一段折线上
        i = 1
        while i < len(self._cumulative) - 1 and self._cumulative[i] < distance_m:
            i += 1

        seg_start = self._cumulative[i - 1]
        seg_len = self._cumulative[i] - seg_start

        if seg_len <= 0:
            ratio = 0.0
        else:
            ratio = (distance_m - seg_start) / seg_len

        lat1, lng1 = self.points[i - 1]
        lat2, lng2 = self.points[i]
        lat = lat1 + (lat2 - lat1) * ratio
        lng = lng1 + (lng2 - lng1) * ratio

        # 航向角用整段折线的方向，比用相邻两个采样点更稳，不会因为抖动乱转
        heading = bearing(lat1, lng1, lat2, lng2)
        return lat, lng, heading

    def offset_point(self, latitude, longitude, distance_m, angle_deg):
        """把一个点沿指定角度方向平移 distance_m 米，返回 (纬度, 经度)。

        angle_deg 是航向角：0 度向北、90 度向东。模拟器拿它把点挪到路线旁边造偏航。
        """
        x, y = to_local_meters(latitude, longitude, self.ref_latitude)
        rad = math.radians(angle_deg)
        # 航向角 0 度朝北(即 y 正方向)，90 度朝东(即 x 正方向)
        x += distance_m * math.sin(rad)
        y += distance_m * math.cos(rad)
        return from_local_meters(x, y, self.ref_latitude)

    def normal_angle_at(self, distance_m):
        """路线上某一点的「左侧垂直方向」航向角，用来造偏航。

        车沿路线走的航向角减 90 度，就是车左边的方向。
        """
        _, _, heading = self.position_at(distance_m)
        return (heading - 90) % 360

    def distance_to_route_m(self, latitude, longitude):
        """一个点到整条路线的最短距离，米。

        用 Shapely 算点到线段的垂距，比「取最近折线点」准，
        尤其是路线拐弯的地方。前提是先把经纬度换成米制，
        否则 Shapely 算出来的是「度」，200 米的阈值就没法设了。
        """
        x, y = to_local_meters(latitude, longitude, self.ref_latitude)
        return self._line.distance(Point(x, y))

    def distance_along_m(self, latitude, longitude):
        """把一个点投影到路线上，返回它离起点多少米。

        模拟器要在「加油站所在的位置」停车，就得先知道加油站在路线的第几米处。
        """
        x, y = to_local_meters(latitude, longitude, self.ref_latitude)
        return self._line.project(Point(x, y))
