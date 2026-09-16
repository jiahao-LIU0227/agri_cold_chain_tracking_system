"""地理计算。

本项目所有经纬度都是 GCJ-02（高德坐标系），见 README 6.5。
偏航检测和 POI 围栏判断都要算距离，统一用这里的函数，不要各写一份。
"""

import math

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
