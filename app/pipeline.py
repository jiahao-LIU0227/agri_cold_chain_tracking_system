"""把「模拟 -> 分析 -> 入库」串成一条流程。

页面上的「生成示例数据」按钮背后就是这个模块：

    1. 模拟器沿规划路线造一条轨迹
    2. 轨迹点批量入库
    3. 停走分段
    4. 给停留段分类（装货/加油/休息/异常……）
    5. 偏航检测
    6. 温度异常检测
    7. 事件入库

几个顺序问题，写错了会出错：

    - 分段必须在分类前面，分类是给已有的停留段贴标签，不是自己找停留
    - 分段要先入库拿到自增 id，偏航和温度事件才能用 segment_id 指回去
    - 删数据要先删事件再删分段：外键是 ON DELETE SET NULL，
      反过来的话分段被删掉，事件的 segment_id 会被悄悄置空

分析用的轨迹点是从数据库里重新读出来的，不是直接用内存里那份。
这样「页面上看到的」和「算出来的」一定是同一批数据。
"""

from app import storage
from app.classification import classify_segments
from app.deviation import detect_deviations
from app.segmentation import segment_track
from app.simulator import simulate_task
from app.temperature import detect_temperature_events


def analyze_task(task_id):
    """对已经入库的轨迹做一次完整分析，结果写回数据库。

    返回 (task, points, segments, events)，调用方要拿去做报告就省得再查一遍。
    """
    task = storage.get_task(task_id)
    if task is None:
        raise ValueError(f"任务 {task_id} 不存在")

    points = storage.get_track_points(task_id)
    if not points:
        raise ValueError(f"任务 {task_id} 还没有轨迹数据，先跑一次模拟")

    # 先清上次的分析结果，保证重复分析不会越积越多
    storage.delete_events(task_id)
    storage.delete_segments(task_id)

    route_points = storage.get_route_points(task_id)
    pois = storage.list_pois()

    segments = segment_track(points)
    classify_segments(segments, points, task, pois)

    # 先入库才有 id，后面的事件要指回来
    for segment in segments:
        segment.id = storage.insert_segment(task_id, segment)

    events = detect_deviations(points, route_points, segments)
    events += detect_temperature_events(task, points, segments)
    events.sort(key=lambda e: e.start_ts)
    for event in events:
        event.task_id = task_id
    storage.insert_events(task_id, events)

    # 批量插入拿不到自增 id，重新读一遍。这样返回的对象和库里的完全一致，
    # 调用方不用再查一次，也不会出现「内存里有、库里有、但 id 是空」的怪状态。
    return task, points, segments, storage.get_events(task_id)


def simulate_task_data(task_id, duration_s=None, seed=None):
    """重新生成一个任务的轨迹，并立刻分析入库。

    duration_s 是高德给的纯行驶耗时，用来反推平均车速；不传就用默认值。
    重复调用是安全的：旧的轨迹点和分析结果都会先被清掉。
    """
    task = storage.get_task(task_id)
    if task is None:
        raise ValueError(f"任务 {task_id} 不存在")

    route_points = storage.get_route_points(task_id)
    if len(route_points) < 2:
        raise ValueError(f"任务 {task_id} 还没有规划路线，先跑 scripts/seed_example.py")

    storage.delete_track_points(task_id)

    points = simulate_task(task, route_points, storage.list_pois(),
                           duration_s=duration_s, seed=seed)
    storage.insert_track_points(task_id, points)

    return analyze_task(task_id)
