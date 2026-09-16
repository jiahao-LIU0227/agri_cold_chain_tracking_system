"""汇总统计和结论建议。

把分段、偏航、温度三块结果收拢成页面要用的数字，再给一句人话结论。
页面上那排统计卡片（运输时长、里程、平均速度、停留次数、温度达标率）
和最后的「结论建议」都是这个模块出的。

这里只做汇总，不重新判断任何东西——所有结论都来自上游三个模块的结果，
这样一处的规则改动不会在三处出现不一致。
"""

from app.models import (EVENT_DEVIATION, EVENT_TEMPERATURE, SEGMENT_MOVE,
                        SEGMENT_STOP, STOP_ABNORMAL, STOP_TYPE_NAMES)

# 温度达标率低于这个数就在结论里点出来
COMPLIANCE_WARN_PERCENT = 95.0


def format_duration(seconds):
    """把秒数写成人话，比如 2 小时 47 分钟。"""
    seconds = int(seconds)
    hours, remainder = divmod(seconds, 3600)
    minutes = remainder // 60
    if hours:
        return f"{hours} 小时 {minutes} 分钟"
    if minutes:
        return f"{minutes} 分钟"
    return f"{seconds} 秒"


def _temperature_compliance(task, points):
    """温度达标率，百分比。在允许范围内就算达标。"""
    if not points:
        return 100.0
    good = sum(1 for p in points
               if task.temp_min <= p.temperature <= task.temp_max)
    return round(good / len(points) * 100, 1)


def _stop_breakdown(segments):
    """各类停留各几次、共多少秒。"""
    breakdown = {}
    for segment in segments:
        if segment.segment_type != SEGMENT_STOP:
            continue
        name = STOP_TYPE_NAMES.get(segment.stop_type, segment.stop_type or "未知")
        item = breakdown.setdefault(name, {"count": 0, "duration_s": 0})
        item["count"] += 1
        item["duration_s"] += segment.duration_s
    return breakdown


def _conclusions(task, points, segments, events):
    """根据结果给出人话结论和建议。"""
    conclusions = []
    temperatures = [p.temperature for p in points]

    deviations = [e for e in events if e.event_type == EVENT_DEVIATION]
    temperature_events = [e for e in events if e.event_type == EVENT_TEMPERATURE]

    compliance = _temperature_compliance(task, points)

    if deviations:
        worst = max(deviations, key=lambda e: e.max_distance_m or 0)
        total = sum(e.duration_s for e in deviations)
        conclusions.append(
            f"共 {len(deviations)} 次偏航，累计 {format_duration(total)}，"
            f"最远偏离规划路线 {worst.max_distance_m:.0f} 米，建议核对当时的行驶路线。"
        )
    else:
        conclusions.append("全程未偏离规划路线。")

    if temperature_events:
        worst = max(temperature_events, key=lambda e: e.temp_max or 0)
        total = sum(e.duration_s for e in temperature_events)
        where = "停留期间" if worst.during_stop else "行驶途中"
        conclusions.append(
            f"共 {len(temperature_events)} 次温度超限，累计 {format_duration(total)}，"
            f"最高 {worst.temp_max:.1f}℃，发生在{where}，建议检查制冷机组和车门密封。"
        )
    else:
        conclusions.append("全程温度都在允许范围内。")

    abnormal = [s for s in segments
                if s.segment_type == SEGMENT_STOP and s.stop_type == STOP_ABNORMAL]
    if abnormal:
        total = sum(s.duration_s for s in abnormal)
        conclusions.append(
            f"有 {len(abnormal)} 次不在任何站点附近的长时间停留，"
            f"累计 {format_duration(total)}，建议核实原因。"
        )

    if compliance < COMPLIANCE_WARN_PERCENT:
        conclusions.append(
            f"温度达标率 {compliance}%，低于 {COMPLIANCE_WARN_PERCENT}%，"
            f"这次运输的冷链保障不合格。"
        )
    elif not temperature_events:
        conclusions.append(f"温度达标率 {compliance}%，冷链保障合格。")

    return conclusions


def build_report(task, points, segments, events):
    """生成报告数据。返回的 dict 可以直接 jsonify 给前端。"""
    if not points:
        return {"empty": True, "message": "这个任务还没有轨迹数据"}

    ordered = sorted(points, key=lambda p: p.ts)
    temperatures = [p.temperature for p in ordered]

    moves = [s for s in segments if s.segment_type == SEGMENT_MOVE]
    stops = [s for s in segments if s.segment_type == SEGMENT_STOP]

    total_distance = sum(s.distance_m for s in moves)
    total_duration = ordered[-1].ts - ordered[0].ts

    deviations = [e for e in events if e.event_type == EVENT_DEVIATION]
    temperature_events = [e for e in events if e.event_type == EVENT_TEMPERATURE]

    return {
        "empty": False,
        "task": {
            "task_no": task.task_no,
            "origin_name": task.origin_name,
            "dest_name": task.dest_name,
            "cargo_type": task.cargo_type,
            "temp_min": task.temp_min,
            "temp_max": task.temp_max,
            "planned_start": task.planned_start,
            "planned_end": task.planned_end,
        },
        "summary": {
            "start_ts": ordered[0].ts,
            "end_ts": ordered[-1].ts,
            "duration_s": total_duration,
            "duration_text": format_duration(total_duration),
            "distance_m": round(total_distance, 1),
            "distance_text": f"{total_distance / 1000:.1f} km",
            # 全程平均速度含停留时间，比只算行驶段更贴近「这趟车跑得快不快」
            "avg_speed": round(total_distance / total_duration, 2) if total_duration else 0,
            "point_count": len(ordered),
            "stop_count": len(stops),
            "stop_duration_s": sum(s.duration_s for s in stops),
            "move_duration_s": sum(s.duration_s for s in moves),
            "temp_min": round(min(temperatures), 2),
            "temp_max": round(max(temperatures), 2),
            "temp_avg": round(sum(temperatures) / len(temperatures), 2),
            "temp_compliance": _temperature_compliance(task, ordered),
            "deviation_count": len(deviations),
            "deviation_duration_s": sum(e.duration_s for e in deviations),
            "deviation_max_m": round(max((e.max_distance_m or 0 for e in deviations),
                                         default=0.0), 1),
            "temperature_event_count": len(temperature_events),
            "temperature_event_duration_s": sum(e.duration_s for e in temperature_events),
        },
        "stops": _stop_breakdown(segments),
        "conclusions": _conclusions(task, ordered, segments, events),
    }
