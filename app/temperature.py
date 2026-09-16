"""温度异常识别和轨迹关联。

车载制冷机不是万能的：停机、开门、长时间堵车都会让车厢温度飘出允许范围。
这个模块负责找出温度超限的时段，并说清楚当时车在哪儿、在干什么。

规则见 README 6.4：

    - 温度超出 [temp_min, temp_max] 算超限
    - 超限持续超过 T_TEMP(300 秒) 才生成事件，瞬时抖动不算
    - 用「峰值时刻」的轨迹点确定事件在地图上的位置
    - 再看那个时刻是不是落在某个停留段里，补上「停留期间/行驶中」
      和具体的停留类型（加油、装卸……）

最后一个「关联」是这个模块最有用的地方：光说「温度超了」没用，
要能回答「是在哪儿超的、当时在停车还是在开」。
"""

from app.geo import haversine
from app.models import EVENT_TEMPERATURE, SEGMENT_STOP, Event
from app.segmentation import find_segment
from config import T_TEMP


def _excess(task, temperature):
    """超出允许范围多少度。没超限返回 0。

    上下限两侧都要看：冷冻货怕不够冷（低于 temp_min），
    但更常见的是制冷不足导致偏高（高于 temp_max）。
    """
    if temperature > task.temp_max:
        return temperature - task.temp_max
    if temperature < task.temp_min:
        return task.temp_min - temperature
    return 0.0


def _build_event(task, entries, segments):
    """把一串连续超限的轨迹点合成一个温度事件。"""
    # 峰值 = 超出范围最多的那个时刻，它能代表这次事件有多严重
    peak = max(entries, key=lambda p: _excess(task, p.temperature))
    temperatures = [p.temperature for p in entries]

    start_ts = entries[0].ts
    end_ts = entries[-1].ts
    segment = find_segment(segments, peak.ts) if segments else None

    return Event(
        event_type=EVENT_TEMPERATURE,
        start_ts=start_ts,
        end_ts=end_ts,
        duration_s=end_ts - start_ts,
        temp_min=round(min(temperatures), 2),
        temp_max=round(max(temperatures), 2),
        latitude=peak.latitude,
        longitude=peak.longitude,
        during_stop=bool(segment and segment.segment_type == SEGMENT_STOP),
        segment_id=segment.id if segment else None,
    )


def detect_temperature_events(task, points, segments=None):
    """找出所有温度异常事件，按时间升序返回 Event 列表。

    task 里的 temp_min / temp_max 是这次运输的允许温度范围，
    冷冻和冷藏用同一套逻辑，只是阈值不同，所以不用分支处理。
    """
    if not points:
        return []

    events = []
    current = []
    for point in sorted(points, key=lambda p: p.ts):
        if _excess(task, point.temperature) > 0:
            current.append(point)
        elif current:
            _flush(task, current, segments, events)
            current = []

    # 轨迹末尾还超着限，别漏掉
    if current:
        _flush(task, current, segments, events)

    return events


def _flush(task, entries, segments, events):
    """一段超限结束了：够长就记成事件，太短就丢掉。"""
    duration = entries[-1].ts - entries[0].ts
    if duration > T_TEMP:
        events.append(_build_event(task, entries, segments))
