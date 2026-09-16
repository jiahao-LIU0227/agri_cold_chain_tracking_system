"""接口层的测试。

这一层真正要守住的两件事：

    1. Web 服务 Key 只能留在后端。它带的是调用额度，一旦跟着接口发到前端，
       等于把额度送出去。JS Key 不一样，它本来就写在网页源码里，藏不住，
       靠高德控制台的域名白名单保护。
    2. 时间轴上任意一个位置，地图上的位置、温度、速度必须来自同一条记录。
       页面的回放是「按下标取第 i 个点」，然后拿这**一个**点去画车、填温度、
       填速度，所以只要每条记录自己是完整的，三者就不会错位。

接口返回的都是 JSON，不需要浏览器，用 Flask 的测试客户端就能跑。
需要数据库，和 test_storage.py 共用 cold_chain_test 库。
"""

import pytest

from app import create_app, db, storage
from conftest import (T0, make_event, make_points_at, make_stop_segment, make_task,
                      straight_route_coords)
from config import AMAP_WEB_KEY


@pytest.fixture
def client(test_db):
    app = create_app()
    app.config["TESTING"] = True
    return app.test_client()


@pytest.fixture(autouse=True)
def clean_tables(client):
    """每个测试开始前清空，测试之间不互相影响。"""
    with client.application.app_context():
        db.execute("DELETE FROM task")
        storage.delete_all_pois()
    yield


def in_app_context(client, func, *args, **kwargs):
    """在应用上下文里执行 storage / db 的操作（接口测试里要用到）。"""
    with client.application.app_context():
        return func(*args, **kwargs)


@pytest.fixture
def ready_task(client):
    """一个已经有路线和轨迹点的任务，很多测试都从它开始。"""
    def build(point_count=12):
        task = make_task(task_no="T-API-001")
        task.id = in_app_context(client, storage.insert_task, task)
        in_app_context(client, storage.insert_route_points, task.id,
                       straight_route_coords(east_end_m=4000, step_m=1000))
        points = make_points_at([T0 + 60 * i for i in range(point_count)],
                                [15.0] * point_count)
        in_app_context(client, storage.insert_track_points, task.id, points)
        return task
    return build


# ============================================================
# 页面
# ============================================================

def test_index_page_renders(client):
    """主页面要能渲染出来，里面得有个装地图的容器。"""
    response = client.get("/")

    assert response.status_code == 200
    assert "text/html" in response.content_type
    assert 'id="map"' in response.get_data(as_text=True)


def test_static_files_are_served(client):
    """前端那两个脚本要能取到，取不到页面就是个白板。"""
    for path in ("/static/app.js", "/static/fallback_map.js", "/static/style.css"):
        assert client.get(path).status_code == 200, path


# ============================================================
# 配置接口：Web Key 绝不能露面
# ============================================================

def test_config_only_returns_the_frontend_keys(client):
    """配置接口只返回 JS Key 和安全密钥，一个字段都不能多。

    多返回一个字段，就等于多一个可能泄密的入口，所以这里连键名一起断言。
    """
    body = client.get("/api/config").get_json()

    assert set(body) == {"amap_js_key", "amap_js_security_code"}


def test_config_never_leaks_the_web_key(client):
    """Web 服务 Key 的值不能出现在响应里。

    .env 里配了 AMAP_WEB_KEY 的话，这条测试是实打实的值比对；
    没配的话只能验到「字段名里没有 web key」，所以下面会说明。
    """
    response = client.get("/api/config")
    text = response.get_data(as_text=True)

    assert "web_key" not in text.lower()
    if AMAP_WEB_KEY:
        assert AMAP_WEB_KEY not in text
    else:
        pytest.skip("没有配置 AMAP_WEB_KEY，只能验到字段名这一层")


def test_web_key_is_not_in_the_page_either(client):
    """整页 HTML 里也不能有 Web Key —— 它可能被别的地方顺手渲染出去。"""
    response = client.get("/")
    text = response.get_data(as_text=True)

    assert "AMAP_WEB_KEY" not in text
    if AMAP_WEB_KEY:
        assert AMAP_WEB_KEY not in text


# ============================================================
# 健康检查
# ============================================================

def test_health_reports_the_database(client):
    """健康检查要报出数据库连得上、连的是哪个库。"""
    response = client.get("/api/health")
    body = response.get_json()

    assert response.status_code == 200
    assert body["status"] == "ok"
    assert body["database"]["ok"] is True
    assert "cold_chain_test" in body["database"]["message"]
    assert body["missing_settings"] == []


# ============================================================
# 任务列表
# ============================================================

def test_task_list_is_empty_at_first(client):
    """库里没有任务时返回空列表，不是报错。"""
    body = client.get("/api/tasks").get_json()

    assert body["tasks"] == []


def test_task_list_counts_track_points(client, ready_task):
    """列表里的轨迹点数量要和实际条数对得上，面板上要显示它。"""
    ready_task(point_count=12)

    tasks = client.get("/api/tasks").get_json()["tasks"]

    assert len(tasks) == 1
    assert tasks[0]["task_no"] == "T-API-001"
    assert tasks[0]["track_points"] == 12
    assert tasks[0]["temp_min"] == -25.0


# ============================================================
# 路线接口
# ============================================================

def test_route_returns_amap_ordered_coordinates(client, ready_task):
    """路线点按高德要的顺序给：[经度, 纬度]。

    顺序反了地图就画到地球另一边去了，所以这里明确验一下顺序。
    """
    task = ready_task()

    body = client.get(f"/api/tasks/{task.id}/route").get_json()

    assert body["coord_system"] == "gcj02"
    assert len(body["points"]) == 5
    first_lng, first_lat = body["points"][0]
    # 基准点纬度 39.9 左右、经度 116.4 左右，顺序反了一眼就能看出来
    assert 116.0 < first_lng < 117.0
    assert 39.0 < first_lat < 40.0
    assert first_lng == pytest.approx(task.origin_lng, abs=1e-6)


# ============================================================
# 轨迹接口：时间轴上三者同源
# ============================================================

def test_track_points_are_complete_records(client, ready_task):
    """每条记录都要自带时间、位置、速度、温度 —— 一次取全，不拆成几个数组。

    接口要是「位置一个数组、温度一个数组」，前端按下标取的时候错一位，
    就会画成「车在这儿、温度是别处的」，两个都对不上。
    """
    task = ready_task()
    fields = {"ts", "latitude", "longitude", "speed", "temperature",
              "refrigerator_on", "door_open"}

    body = client.get(f"/api/tasks/{task.id}/track").get_json()

    assert body["count"] == 12
    for point in body["points"]:
        assert fields <= set(point)
        assert isinstance(point["temperature"], float)
        assert isinstance(point["speed"], float)


def test_track_timeline_has_one_record_per_moment(client, ready_task):
    """时间戳严格递增，回放的「第 i 个点」才唯一对应一个时刻。

    时间戳有重复的话，下标往前一格可能还是同一时刻，
    回放看起来就是「卡住了一下」。
    """
    task = ready_task()

    points = client.get(f"/api/tasks/{task.id}/track").get_json()["points"]
    times = [p["ts"] for p in points]

    assert times == sorted(times)
    assert len(set(times)) == len(times)


def test_track_matches_what_is_in_the_database(client, ready_task):
    """接口给的数据要和库里那一行完全一致，不能自己加工过。

    接口和数据库对不上的话，页面显示的就不是真实数据了。
    """
    task = ready_task()

    points = client.get(f"/api/tasks/{task.id}/track").get_json()["points"]
    stored = in_app_context(client, storage.get_track_points, task.id)

    assert len(points) == len(stored)
    for api_point, db_point in zip(points, stored):
        assert api_point["ts"] == db_point.ts
        assert api_point["latitude"] == pytest.approx(db_point.latitude, abs=1e-6)
        assert api_point["longitude"] == pytest.approx(db_point.longitude, abs=1e-6)
        assert api_point["temperature"] == pytest.approx(db_point.temperature, abs=1e-6)
        assert api_point["speed"] == pytest.approx(db_point.speed, abs=1e-6)
        assert api_point["door_open"] == db_point.door_open


def test_each_record_carries_its_own_position_and_temperature(client, ready_task):
    """位置和温度必须是同一条记录里的。

    这里用「温度跟着位置走」的造法验证：让温度随里程递增，
    再检查取第 i 个点时，温度只能对应第 i 个位置，不能对应别的位置。
    """
    task = make_task(task_no="T-API-002")
    task.id = in_app_context(client, storage.insert_task, task)
    points = make_points_at([T0 + 60 * i for i in range(6)], [15.0] * 6)
    for i, point in enumerate(points):
        point.temperature = -20.0 + i * 0.5          # 温度随里程递增
    in_app_context(client, storage.insert_track_points, task.id, points)

    body = client.get(f"/api/tasks/{task.id}/track").get_json()["points"]
    stored = in_app_context(client, storage.get_track_points, task.id)

    for i, record in enumerate(body):
        assert record["temperature"] == pytest.approx(stored[i].temperature)
        assert record["latitude"] == pytest.approx(stored[i].latitude, abs=1e-6)


# ============================================================
# 分析接口
# ============================================================

def test_analysis_returns_segments_events_and_summary(client, ready_task):
    """分析接口要一次给全：分段、事件、统计。

    页面上画的停留点、偏航段、温度异常点和统计卡片都从这一次请求里来。
    """
    task = ready_task()
    segment = make_stop_segment(39.9, 116.4, 900)
    segment.stop_type = "rest"
    in_app_context(client, storage.insert_segment, task.id, segment)
    event = make_event()
    in_app_context(client, storage.insert_events, task.id, [event])

    body = client.get(f"/api/tasks/{task.id}/analysis").get_json()

    assert len(body["segments"]) == 1
    assert body["segments"][0]["stop_type"] == "rest"
    assert body["segments"][0]["duration_s"] == 900
    assert len(body["events"]) == 1
    assert body["events"][0]["event_type"] == "deviation"
    assert "duration_text" in body["summary"]
    assert "temp_compliance" in body["summary"]


def test_summary_numbers_survive_the_database_round_trip(client, ready_task):
    """统计里的数字要是 JSON 能认的类型，不能是数据库那种 Decimal。

    数据库读出来的 DECIMAL 是 Decimal，模型里转成了 float；
    漏掉这层转换的话 jsonify 会直接 500，这条测试就是守它的。
    """
    task = ready_task()
    # 走 SQL 改：接口是从数据库重新读点做分析的，改内存里那份没用
    in_app_context(client, db.execute,
                   "UPDATE track_point SET temperature = -30 WHERE task_id = %s",
                   (task.id,))

    response = client.get(f"/api/tasks/{task.id}/analysis")

    assert response.status_code == 200
    summary = response.get_json()["summary"]
    assert summary["temp_max"] == pytest.approx(-30.0)
    assert summary["temp_compliance"] == 0.0
    for key in ("distance_m", "avg_speed", "temp_avg", "temp_compliance"):
        assert isinstance(summary[key], (int, float)), key


def test_report_returns_conclusions(client, ready_task):
    """报告接口要有任务信息、统计和结论建议三块。"""
    task = ready_task()

    body = client.get(f"/api/tasks/{task.id}/report").get_json()

    assert body["task"]["task_no"] == "T-API-001"
    assert "summary" in body
    assert "conclusions" in body
    assert isinstance(body["conclusions"], list)
    assert "stops" in body


def test_report_survives_a_task_without_any_track(client):
    """还没跑模拟的任务去查报告，不能 500 —— 页面一打开就会调它。"""
    task = make_task(task_no="T-EMPTY")
    task.id = in_app_context(client, storage.insert_task, task)

    response = client.get(f"/api/tasks/{task.id}/report")

    assert response.status_code == 200
    assert response.get_json()["empty"] is True


# ============================================================
# 不存在的东西要老实报 404
# ============================================================

@pytest.mark.parametrize("suffix", ["route", "track", "analysis", "report"])
def test_missing_task_returns_404(client, suffix):
    response = client.get(f"/api/tasks/999999/{suffix}")

    assert response.status_code == 404
    assert "不存在" in response.get_json()["error"]


def test_non_numeric_task_id_returns_404(client):
    """任务 id 不是数字：路由直接匹配不上，也是 404，不能崩。"""
    assert client.get("/api/tasks/abc/track").status_code == 404


# ============================================================
# 模拟接口
# ============================================================

def test_simulate_without_any_task_gives_a_useful_error(client):
    """一条任务都没有的时候，要明确告诉用户先去跑示例数据脚本。"""
    response = client.post("/api/simulate", json={})

    assert response.status_code == 404
    assert "seed_example.py" in response.get_json()["error"]


def test_simulate_generates_a_track_and_analysis(client, ready_task):
    """一次请求要把轨迹、分段、事件全生成好，页面拿到就能直接画。"""
    task = ready_task(point_count=0)

    body = client.post("/api/simulate",
                       json={"task_id": task.id, "seed": 7}).get_json()

    assert body["task_id"] == task.id
    assert body["point_count"] > 0
    assert body["segment_count"] > 0
    assert "temp_compliance" in body["summary"]

    segments = in_app_context(client, storage.get_segments, task.id)
    assert any(s.segment_type == "stop" for s in segments), "一条轨迹不该一个停留都没有"


def test_simulate_is_deterministic_with_the_same_seed(client, ready_task):
    """同一个种子跑两次，结果必须一模一样。

    演示和截图要能复现，随机种子固定住是这个项目的基本要求。
    """
    task = ready_task(point_count=0)

    first = client.post("/api/simulate", json={"task_id": task.id, "seed": 42}).get_json()
    second = client.post("/api/simulate", json={"task_id": task.id, "seed": 42}).get_json()

    assert first["point_count"] == second["point_count"]
    assert first["segment_count"] == second["segment_count"]
    assert first["event_count"] == second["event_count"]


def test_simulate_twice_does_not_pile_up_data(client, ready_task):
    """重复点「生成示例数据」不能越积越多，旧数据要先清掉。"""
    task = ready_task(point_count=0)

    client.post("/api/simulate", json={"task_id": task.id, "seed": 42})
    count_once = in_app_context(client, storage.count_track_points, task.id)
    client.post("/api/simulate", json={"task_id": task.id, "seed": 42})
    count_twice = in_app_context(client, storage.count_track_points, task.id)

    assert count_once == count_twice


def test_simulate_without_task_id_uses_the_newest_task(client, ready_task):
    """不传 task_id 就默认给最新那条任务重新生成，页面上不用先选任务。"""
    task = ready_task(point_count=0)

    body = client.post("/api/simulate", json={"seed": 42}).get_json()

    assert body["task_id"] == task.id


def test_simulate_on_a_missing_task_returns_400(client):
    response = client.post("/api/simulate", json={"task_id": 999999})

    assert response.status_code == 400
    assert "不存在" in response.get_json()["error"]


def test_simulate_without_a_route_returns_400(client):
    """任务还没有规划路线时，要提示先去跑示例数据脚本，而不是抛异常。"""
    task = make_task(task_no="T-NO-ROUTE")
    task.id = in_app_context(client, storage.insert_task, task)

    response = client.post("/api/simulate", json={"task_id": task.id})

    assert response.status_code == 400
    assert "规划路线" in response.get_json()["error"]


def test_simulate_clears_the_previous_analysis(client, ready_task):
    """重新生成之后，上一次的分段和事件不能残留。"""
    task = ready_task(point_count=0)
    client.post("/api/simulate", json={"task_id": task.id, "seed": 1})
    first_segments = in_app_context(client, storage.get_segments, task.id)
    first_events = in_app_context(client, storage.get_events, task.id)

    client.post("/api/simulate", json={"task_id": task.id, "seed": 2})
    second_segments = in_app_context(client, storage.get_segments, task.id)
    second_events = in_app_context(client, storage.get_events, task.id)

    first_ids = {s.id for s in first_segments}
    second_ids = {s.id for s in second_segments}
    assert not (first_ids & second_ids), "上一次的分段没清掉，新旧数据混在一起了"
    # 事件要么是新的，要么被清空了，总之不能留着上一轮那些
    assert all(e.id not in {x.id for x in first_events} for e in second_events)
