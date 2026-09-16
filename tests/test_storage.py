"""数据库读写的测试。

和前面几组不一样，这个文件需要 MySQL —— 读写 SQL 对不对，只有真的连库跑一遍
才知道，拿假数据糊弄过去没有意义。

用单独的 cold_chain_test 库（在 conftest.py 里建好、跑完删掉），
不碰开发库 cold_chain 里的数据。连不上 MySQL 的话这一整个文件会被跳过，
其它测试不受影响 —— 算法部分的测试本来就不需要数据库。
"""

import pymysql
import pytest

from app import create_app, db, storage
from app.models import Event
from conftest import (T0, make_event, make_points_at, make_poi, make_stop_segment,
                      make_task, straight_route_coords)


@pytest.fixture
def app_ctx(test_db):
    """在应用上下文里跑测试：storage 里的函数都要用 db.get_db()。

    同时把数据库指向测试库 —— conftest 的 test_db 已经改好了 DB_CONFIG，
    这里只需要让 Flask 也用上那个配置。
    """
    app = create_app()
    with app.app_context():
        yield app


@pytest.fixture(autouse=True)
def clean_tables(app_ctx):
    """每个测试跑之前先清空。

    task 是本项目所有数据的根：轨迹点、路线点、分段、事件都挂在它下面，
    而且是 ON DELETE CASCADE，所以删 task 一张表就够了。
    """
    db.execute("DELETE FROM task")
    storage.delete_all_pois()
    yield


def some_track_points(count=5, interval=60):
    """一串正常行驶的轨迹点，位置沿正东匀速前进。"""
    times = [T0 + interval * i for i in range(count)]
    return make_points_at(times, [15.0] * count)


# ============================================================
# 运输任务
# ============================================================

def test_task_round_trip(app_ctx):
    """写进去再读出来，字段要一一对上（含中文和 Decimal 转 float）。"""
    task = make_task(task_no="T-TEST-001")
    task.id = storage.insert_task(task)

    loaded = storage.get_task(task.id)

    assert loaded.task_no == "T-TEST-001"
    assert loaded.origin_name == "测试起点"
    assert loaded.dest_name == "测试终点"
    assert loaded.cargo_type == task.cargo_type
    # DECIMAL 字段读出来是 Decimal，模型里统一转成 float，否则算温度会报类型错
    assert isinstance(loaded.temp_min, float)
    assert isinstance(loaded.temp_max, float)
    assert loaded.temp_min == task.temp_min
    assert loaded.temp_max == task.temp_max
    assert loaded.planned_start == task.planned_start
    assert loaded.coord_system == "gcj02"


def test_get_missing_task_returns_none(app_ctx):
    assert storage.get_task(999999) is None


def test_list_tasks_returns_newest_first(app_ctx):
    """列表按 id 倒序：面板默认要显示最新那条任务。"""
    first = make_task(task_no="T-A")
    second = make_task(task_no="T-B")
    first.id = storage.insert_task(first)
    second.id = storage.insert_task(second)

    tasks = storage.list_tasks()

    assert [t.task_no for t in tasks] == ["T-B", "T-A"]
    assert tasks[0].id == second.id


def test_task_no_must_be_unique(app_ctx):
    """任务编号有唯一约束，重复插要报错而不是静静写两条。"""
    task = make_task(task_no="T-SAME")
    storage.insert_task(task)

    with pytest.raises(pymysql.err.IntegrityError):
        storage.insert_task(make_task(task_no="T-SAME"))


# ============================================================
# 规划路线
# ============================================================

def test_route_points_keep_their_order(app_ctx):
    """路线点带 seq，读出来要按 seq 升序，不能靠数据库默认顺序。"""
    task = make_task()
    task.id = storage.insert_task(task)
    coords = straight_route_coords(east_end_m=2000, step_m=500)

    storage.insert_route_points(task.id, coords)
    loaded = storage.get_route_points(task.id)

    assert len(loaded) == len(coords)
    assert [p.seq for p in loaded] == list(range(len(coords)))
    assert loaded[0].latitude == pytest.approx(coords[0][0], abs=1e-6)
    assert loaded[-1].longitude == pytest.approx(coords[-1][1], abs=1e-6)


# ============================================================
# 轨迹点
# ============================================================

def test_track_points_round_trip(app_ctx):
    """批量写入再读出来，写入顺序和读出顺序无关（读出按时间排）。"""
    task = make_task()
    task.id = storage.insert_task(task)
    points = some_track_points(20)

    inserted = storage.insert_track_points(task.id, points)
    loaded = storage.get_track_points(task.id)

    assert inserted == 20
    assert storage.count_track_points(task.id) == 20
    assert [p.ts for p in loaded] == sorted(p.ts for p in points)
    assert loaded[0].speed == pytest.approx(points[0].speed)
    assert loaded[0].temperature == pytest.approx(points[0].temperature)
    # TINYINT 存布尔，读出来要还成 True/False
    assert loaded[0].refrigerator_on is True
    assert loaded[0].door_open is False


def test_track_points_are_read_back_in_time_order(app_ctx):
    """故意倒着写进去，读出来必须是按时间升序的。"""
    task = make_task()
    task.id = storage.insert_task(task)
    points = some_track_points(10)

    storage.insert_track_points(task.id, list(reversed(points)))
    loaded = storage.get_track_points(task.id)

    assert [p.ts for p in loaded] == sorted(p.ts for p in points)


def test_duplicate_timestamps_can_be_stored(app_ctx):
    """时间戳重复的数据要能存进来。

    表上故意没加 (task_id, ts) 的唯一约束：验收要求「时间戳重复时程序不能崩溃」，
    那就得让重复数据进得来，由分析算法负责处理，而不是靠数据库直接拒绝。
    """
    task = make_task()
    task.id = storage.insert_task(task)
    points = make_points_at([T0, T0, T0 + 60], [15.0, 15.0, 15.0])

    storage.insert_track_points(task.id, points)

    assert storage.count_track_points(task.id) == 3


def test_delete_track_points_clears_only_that_task(app_ctx):
    """重跑模拟前要清掉上一次的轨迹点，但别把别的任务也清了。"""
    keep = make_task(task_no="T-KEEP")
    drop = make_task(task_no="T-DROP")
    keep.id = storage.insert_task(keep)
    drop.id = storage.insert_task(drop)
    storage.insert_track_points(keep.id, some_track_points(3))
    storage.insert_track_points(drop.id, some_track_points(3))

    storage.delete_track_points(drop.id)

    assert storage.count_track_points(keep.id) == 3
    assert storage.count_track_points(drop.id) == 0


# ============================================================
# 分段和事件：靠 segment_id 串起来
# ============================================================

def test_insert_segment_returns_its_id(app_ctx):
    """分段要一条一条插，因为后面事件表要用它的自增 id。"""
    task = make_task()
    task.id = storage.insert_task(task)
    segment = make_stop_segment(39.9, 116.4, 900)

    segment.id = storage.insert_segment(task.id, segment)

    assert segment.id is not None
    loaded = storage.get_segments(task.id)
    assert len(loaded) == 1
    assert loaded[0].id == segment.id
    assert loaded[0].duration_s == 900


def test_event_can_point_back_to_its_segment(app_ctx):
    """事件带上 segment_id，就能从事件反查「当时停在干什么」。"""
    task = make_task()
    task.id = storage.insert_task(task)
    segment = make_stop_segment(39.9, 116.4, 900)
    segment.id = storage.insert_segment(task.id, segment)

    event = make_event()
    event.segment_id = segment.id
    storage.insert_events(task.id, [event])

    loaded = storage.get_events(task.id)[0]
    assert loaded.segment_id == segment.id

    # 顺着 segment_id 能找回那段停留
    stop = [s for s in storage.get_segments(task.id) if s.id == loaded.segment_id][0]
    assert stop.duration_s == 900


def test_get_events_can_filter_by_type(app_ctx):
    """偏航和温度共用一张表，按类型筛要能筛干净。"""
    task = make_task()
    task.id = storage.insert_task(task)

    deviation = make_event()
    temperature = Event(event_type="temperature", start_ts=T0, end_ts=T0 + 400,
                        duration_s=400, temp_min=-10.0, temp_max=-8.0)
    storage.insert_events(task.id, [deviation, temperature])

    assert len(storage.get_events(task.id)) == 2
    assert len(storage.get_events(task.id, "deviation")) == 1
    assert len(storage.get_events(task.id, "temperature")) == 1
    assert storage.get_events(task.id, "deviation")[0].max_distance_m == pytest.approx(520.5)


def test_deleting_a_segment_keeps_the_event(app_ctx):
    """删掉分段，事件要留下来，只是 segment_id 变成 NULL。

    外键写的是 ON DELETE SET NULL。重新分析时事件先删、分段后删，
    但万一顺序反了，也不能把事件连带删掉——异常本身是算出来的结论，
    不该因为一次清理动作就丢了。
    """
    task = make_task()
    task.id = storage.insert_task(task)
    segment = make_stop_segment(39.9, 116.4, 900)
    segment.id = storage.insert_segment(task.id, segment)
    event = make_event()
    event.segment_id = segment.id
    storage.insert_events(task.id, [event])

    storage.delete_segments(task.id)

    events = storage.get_events(task.id)
    assert len(events) == 1
    assert events[0].segment_id is None


def test_delete_events_clears_only_that_task(app_ctx):
    keep = make_task(task_no="T-KEEP")
    drop = make_task(task_no="T-DROP")
    keep.id = storage.insert_task(keep)
    drop.id = storage.insert_task(drop)
    storage.insert_events(keep.id, [make_event()])
    storage.insert_events(drop.id, [make_event()])

    storage.delete_events(drop.id)

    assert len(storage.get_events(keep.id)) == 1
    assert storage.get_events(drop.id) == []


# ============================================================
# 级联删除
# ============================================================

def test_deleting_a_task_removes_everything_below_it(app_ctx):
    """删任务要把它名下的数据一起带走，不能留孤儿行。

    这是外键 ON DELETE CASCADE 的作用。之前 segment 表漏了这条外键，
    删任务会留下一堆没有归属的分段，查 information_schema 才发现的。
    """
    task = make_task()
    task.id = storage.insert_task(task)
    storage.insert_route_points(task.id, straight_route_coords(east_end_m=1000, step_m=500))
    storage.insert_track_points(task.id, some_track_points(5))
    segment = make_stop_segment(39.9, 116.4, 900)
    segment.id = storage.insert_segment(task.id, segment)
    event = make_event()
    event.segment_id = segment.id
    storage.insert_events(task.id, [event])

    storage.delete_task(task.id)

    assert storage.get_task(task.id) is None
    assert storage.get_route_points(task.id) == []
    assert storage.get_track_points(task.id) == []
    assert storage.get_segments(task.id) == []
    assert storage.get_events(task.id) == []


# ============================================================
# POI
# ============================================================

def test_poi_round_trip(app_ctx):
    """POI 带中文名，写进去读出来不能乱码。"""
    poi = make_poi(name="武清中转仓", east_m=5000.0)
    storage.upsert_pois([poi])

    loaded = storage.list_pois()

    assert len(loaded) == 1
    assert loaded[0].name == "武清中转仓"
    assert loaded[0].poi_type == poi.poi_type
    assert loaded[0].radius_m == 200


def test_poi_upsert_updates_instead_of_duplicating(app_ctx):
    """同一个高德 POI 反复写，应该更新已有那一条，不能越写越多。

    靠 amap_id 的唯一约束去重：搜索一次加油站会来一次，跑十次就是十条重复数据。
    """
    poi = make_poi(name="中石化加油站", east_m=5000.0)
    poi.amap_id = "B00000TEST"
    storage.upsert_pois([poi])

    moved = make_poi(name="中石化加油站(改)", east_m=5100.0)
    moved.amap_id = "B00000TEST"
    storage.upsert_pois([moved])

    loaded = storage.list_pois()
    assert len(loaded) == 1
    assert loaded[0].name == "中石化加油站(改)"


def test_manual_pois_are_not_deduplicated(app_ctx):
    """手写 POI 的 amap_id 是 NULL，同名同位置也可以有多条，不受唯一约束管。"""
    storage.upsert_pois([make_poi(name="手写点"), make_poi(name="手写点")])

    assert len(storage.list_pois()) == 2


def test_list_pois_can_filter_by_type(app_ctx):
    storage.upsert_pois([make_poi(name="加油站", east_m=1000.0),
                         make_poi(name="中转仓", poi_type="loading_dock", east_m=2000.0)])

    assert len(storage.list_pois()) == 2
    assert len(storage.list_pois("gas_station")) == 1
    assert storage.list_pois("loading_dock")[0].name == "中转仓"


def test_delete_all_pois(app_ctx):
    """示例数据脚本每次重跑前要清空 POI，否则会越积越多。"""
    storage.upsert_pois([make_poi(name="加油站", east_m=1000.0),
                         make_poi(name="中转仓", poi_type="loading_dock", east_m=2000.0)])

    deleted = storage.delete_all_pois()

    assert deleted == 2
    assert storage.list_pois() == []


# ============================================================
# 连接本身
# ============================================================

def test_ping_reports_the_test_database(app_ctx):
    """连通性检查要报出连的是哪个库 —— 测试跑在测试库上，别糊里糊涂连到开发库。"""
    ok, message = db.ping()

    assert ok is True
    assert "cold_chain_test" in message


def test_writes_are_committed(app_ctx):
    """插入要即时提交，换个连接也该看得见（不能只活在当前连接的事务里）。"""
    task = make_task(task_no="T-COMMIT")
    task.id = storage.insert_task(task)

    with db.connect() as other:
        with other.cursor() as cur:
            cur.execute("SELECT COUNT(*) AS n FROM task WHERE id = %s", (task.id,))
            assert cur.fetchone()["n"] == 1
