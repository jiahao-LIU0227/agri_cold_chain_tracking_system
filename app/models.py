"""数据对象。

字段和 sql/schema.sql 里的表一一对应，方便用 TrackPoint(**row) 这种写法直接构造。
所有经纬度都是 GCJ-02（高德坐标系），不是 WGS-84，见 README 6.5。
"""

import time
from dataclasses import dataclass, field

# ---- 全系统统一的坐标系 ----
COORD_SYSTEM = "gcj02"

# ---- 分段类型 ----
SEGMENT_MOVE = "move"
SEGMENT_STOP = "stop"

# ---- 停留类型 ----
STOP_LOAD = "load"          # 装货
STOP_UNLOAD = "unload"      # 卸货
STOP_TRANSFER = "transfer"  # 中途装卸，路上在别的仓库过一手货
STOP_REFUEL = "refuel"      # 加油
STOP_REST = "rest"          # 休息
STOP_ABNORMAL = "abnormal"  # 异常停留
STOP_UNKNOWN = "unknown"    # 未知

# ---- 异常事件类型 ----
EVENT_DEVIATION = "deviation"      # 偏航
EVENT_TEMPERATURE = "temperature"  # 温度异常

# ---- 货物类型 ----
CARGO_FROZEN = "frozen"    # 冷冻，示例阈值 ≤ -18℃
CARGO_CHILLED = "chilled"  # 冷藏，示例阈值 0~4℃

# ---- POI 类型 ----
POI_GAS_STATION = "gas_station"      # 加油站
POI_SERVICE_AREA = "service_area"    # 服务区
POI_LOADING_DOCK = "loading_dock"    # 中途装卸点
POI_OTHER = "other"

STOP_TYPE_NAMES = {
    STOP_LOAD: "装货",
    STOP_UNLOAD: "卸货",
    STOP_TRANSFER: "中途装卸",
    STOP_REFUEL: "加油",
    STOP_REST: "休息",
    STOP_ABNORMAL: "异常停留",
    STOP_UNKNOWN: "未知",
}


@dataclass
class RoutePoint:
    """规划路线上的一个折线点。"""

    seq: int              # 第几个点，从 0 开始，越小越靠近起点
    latitude: float
    longitude: float
    task_id: int | None = None
    id: int | None = None

    @classmethod
    def from_row(cls, row):
        return cls(
            seq=row["seq"],
            latitude=float(row["latitude"]),
            longitude=float(row["longitude"]),
            task_id=row.get("task_id"),
            id=row.get("id"),
        )


@dataclass
class TrackPoint:
    """车辆上报的一个轨迹点。"""

    task_id: int
    ts: int                # Unix 秒
    latitude: float
    longitude: float
    speed: float           # 米/秒
    heading: float         # 航向角 0~360
    temperature: float     # 车厢温度 ℃
    refrigerator_on: bool = False
    door_open: bool = False
    position_quality: int = 1  # 0 差 1 好
    id: int | None = None

    @classmethod
    def from_row(cls, row):
        return cls(
            task_id=row["task_id"],
            ts=row["ts"],
            latitude=float(row["latitude"]),
            longitude=float(row["longitude"]),
            speed=float(row["speed"]),
            heading=float(row["heading"]),
            temperature=float(row["temperature"]),
            refrigerator_on=bool(row["refrigerator_on"]),
            door_open=bool(row["door_open"]),
            position_quality=row["position_quality"],
            id=row.get("id"),
        )


@dataclass
class TransportTask:
    """一次完整的运输任务。"""

    task_no: str
    origin_name: str
    dest_name: str
    origin_lat: float
    origin_lng: float
    dest_lat: float
    dest_lng: float
    cargo_type: str        # CARGO_FROZEN / CARGO_CHILLED
    temp_min: float        # 允许温度下限 ℃
    temp_max: float        # 允许温度上限 ℃
    planned_start: int     # Unix 秒
    planned_end: int       # Unix 秒
    coord_system: str = COORD_SYSTEM
    created_at: int = field(default_factory=lambda: int(time.time()))
    id: int | None = None

    @classmethod
    def from_row(cls, row):
        return cls(
            task_no=row["task_no"],
            origin_name=row["origin_name"],
            dest_name=row["dest_name"],
            origin_lat=float(row["origin_lat"]),
            origin_lng=float(row["origin_lng"]),
            dest_lat=float(row["dest_lat"]),
            dest_lng=float(row["dest_lng"]),
            cargo_type=row["cargo_type"],
            temp_min=float(row["temp_min"]),
            temp_max=float(row["temp_max"]),
            planned_start=row["planned_start"],
            planned_end=row["planned_end"],
            coord_system=row["coord_system"],
            created_at=row["created_at"],
            id=row["id"],
        )


@dataclass
class Segment:
    """一个行驶段或停留段。"""

    segment_type: str      # SEGMENT_MOVE / SEGMENT_STOP
    start_ts: int
    end_ts: int
    duration_s: int
    start_lat: float
    start_lng: float
    end_lat: float
    end_lng: float
    distance_m: float = 0.0
    avg_speed: float = 0.0
    stop_type: str | None = None  # 只有停留段才有
    task_id: int | None = None
    id: int | None = None

    @classmethod
    def from_row(cls, row):
        return cls(
            segment_type=row["segment_type"],
            start_ts=row["start_ts"],
            end_ts=row["end_ts"],
            duration_s=row["duration_s"],
            start_lat=float(row["start_lat"]),
            start_lng=float(row["start_lng"]),
            end_lat=float(row["end_lat"]),
            end_lng=float(row["end_lng"]),
            distance_m=float(row["distance_m"]),
            avg_speed=float(row["avg_speed"]),
            stop_type=row["stop_type"],
            task_id=row["task_id"],
            id=row["id"],
        )


@dataclass
class Event:
    """一个异常事件。偏航和温度共用，用 event_type 区分，用不到的字段留 None。"""

    event_type: str        # EVENT_DEVIATION / EVENT_TEMPERATURE
    start_ts: int
    end_ts: int
    duration_s: int
    max_distance_m: float | None = None  # 偏航用：最远偏离米数
    temp_min: float | None = None        # 温度用：期间最低温
    temp_max: float | None = None        # 温度用：期间最高温
    latitude: float | None = None        # 事件在地图上的标记位置
    longitude: float | None = None
    during_stop: bool = False            # 是否发生在停留期间
    segment_id: int | None = None        # 关联的分段，便于反查停留类型
    task_id: int | None = None
    id: int | None = None

    @classmethod
    def from_row(cls, row):
        def _f(v):
            return None if v is None else float(v)

        return cls(
            event_type=row["event_type"],
            start_ts=row["start_ts"],
            end_ts=row["end_ts"],
            duration_s=row["duration_s"],
            max_distance_m=_f(row["max_distance_m"]),
            temp_min=_f(row["temp_min"]),
            temp_max=_f(row["temp_max"]),
            latitude=_f(row["latitude"]),
            longitude=_f(row["longitude"]),
            during_stop=bool(row["during_stop"]),
            segment_id=row["segment_id"],
            task_id=row["task_id"],
            id=row["id"],
        )


@dataclass
class Poi:
    """兴趣点。不属于某个任务，所有任务共用。"""

    name: str
    poi_type: str          # POI_GAS_STATION / POI_SERVICE_AREA / ...
    latitude: float
    longitude: float
    radius_m: int = 200    # 围栏半径，落在这个圈里就算在该 POI 内
    source: str = "amap"   # amap 高德搜索 / manual 手写
    amap_id: str | None = None
    id: int | None = None

    @classmethod
    def from_row(cls, row):
        return cls(
            name=row["name"],
            poi_type=row["poi_type"],
            latitude=float(row["latitude"]),
            longitude=float(row["longitude"]),
            radius_m=row["radius_m"],
            source=row["source"],
            amap_id=row["amap_id"],
            id=row["id"],
        )
