# 农产品冷链途迹监测系统

## 1. 项目定位

本项目是小组课程设计，目标是用 Python 实现一个“可演示、可测试、能解释”的冷链运输监测系统。系统不追求真正接入 GPS 硬件，而是先用模拟器生成车辆上报数据，再完成轨迹分析、异常识别和可视化。

建议先完成最小可行版本（MVP），确保一辆车的一次运输任务能够完整跑通：

1. 生成或导入一条运输轨迹。
2. 识别行驶段、停留段和停留类型。
3. 识别偏航和温度异常。
4. 在网页地图上查看轨迹并按时间回放。
5. 输出一份运输分析报告。

扩展功能（多车对比、准点率、合规评分等）在 MVP 稳定后再做。

> **两条底线，先记住**
> 1. **断网也能演示**：地图、规划路线、POI 都要有本地缓存兜底，网络不通不能影响算法演示。
> 2. **密钥不进仓库**：数据库密码和高德 API Key 只写在 `.env` 里，`.env` 永远不提交。

## 2. 推荐技术方案

### 运行环境

- **Python 3.14.6**：全组统一这个版本，不要每人一个版本，否则依赖装不上的问题很难排查
- **MySQL 8.0+**：本机安装 MySQL，或者直接用 XAMPP / WAMP 自带的 MySQL

### 后端与算法

- **Flask**：提供网页和 REST API，学习成本低
- **PyMySQL**：连接 MySQL 的驱动，纯 Python 实现，Windows 上 `pip install` 不用编译，最省事
- **python-dotenv**：启动时把 `.env` 里的内容读进环境变量
- **requests**：调用高德 Web 服务 API
- `dataclasses`：定义轨迹点、停留段等数据对象
- **shapely**：进行路线缓冲区、圆形/多边形 POI 和距离判断
- **pandas**：用于统计和生成报告（如果安装困难，可先用 Python 列表和 `statistics`）
- **pytest**：编写单元测试

> 为什么不直接用 SQLAlchemy 这类 ORM：本项目 SQL 不复杂，手写 `SELECT`/`INSERT` 反而更容易看懂、更容易调试，答辩时也讲得清楚。想学 ORM 可以等 MVP 跑通以后再换。

### 前端与可视化

- HTML + CSS + 原生 JavaScript，不引入 React 等大型框架
- **高德地图 JS API 2.0**：显示地图、规划路线、实际轨迹、停留点、偏航段和温度异常点
- 一个时间轴滑块控制当前回放时刻；车辆图标、温度和速度都从同一条轨迹记录读取
- **简图模式兜底**：如果高德地图脚本没加载出来（断网、Key 失效），自动切换到 canvas 平面轨迹图，保证演示不中断

## 3. 系统总体结构

```text
        高德地图 Web 服务 API
        (路径规划 / 周边 POI 搜索)
                  |
                  | 首次联网拉取，结果写入本地缓存
                  v
          模拟器 simulator
                  |
                  v
        数据层 storage  <-->  分析层 analysis
                  |                    |
           +------ Flask API ----------+
                        |
                        v
              高德地图 JS API 前端页面
```

数据流按以下顺序实现：

```text
获取规划路线(高德API 或 本地JSON) -> 加入时间维度生成轨迹
        -> 保存原始上报点 -> 停走分段 -> 停留分类
        -> 偏航检测、温度检测 -> 统计汇总 -> 页面/报告展示
```

注意：规划路线只拉一次就缓存到本地。高德个人开发者的 Key 有每日调用量和每秒请求数限制，反复调用会把额度耗光，而且演示现场断网就直接崩了。

## 4. 建议的目录结构

```text
agri_cold_chain_tracking_system/
├─ README.md
├─ requirements.txt
├─ .env.example              # 环境变量清单，提交到仓库
├─ .env                      # 真实密钥，不提交（自己复制 .env.example 再改）
├─ .gitignore
├─ run.py                    # 启动 Flask 服务
├─ config.py                 # 从环境变量读配置，带默认值
├─ sql/
│  └─ schema.sql             # 建库建表语句，方便一键初始化
├─ scripts/
│  └─ seed_example.py        # 拉一条示例路线和 POI，写进 MySQL
├─ app/
│  ├─ __init__.py
│  ├─ routes.py              # 页面和 API 路由
│  ├─ models.py              # dataclass 数据对象
│  ├─ db.py                  # MySQL 连接的获取和释放
│  ├─ storage.py             # 增删查改（手写 SQL，建表在 sql/schema.sql）
│  ├─ amap.py                # 高德 Web 服务 API 封装 + 本地缓存
│  ├─ geo.py                 # 距离计算和坐标系转换
│  ├─ simulator.py           # 运输过程和温度变化模拟
│  ├─ segmentation.py        # 停走分段
│  ├─ classification.py      # POI 和停留类型分类
│  ├─ deviation.py           # 偏航检测
│  ├─ temperature.py         # 温度异常识别和轨迹关联
│  ├─ report.py              # 汇总统计和报告数据
│  └─ pipeline.py            # 把「模拟 -> 分析 -> 入库」串成一条流程
├─ templates/
│  └─ index.html             # 主页面骨架，地图容器在这儿
├─ static/
│  ├─ app.js                 # 地图、回放和图表交互
│  ├─ fallback_map.js        # 断网时的简图模式
│  └─ style.css
├─ tests/
│  ├─ conftest.py            # 测试数据的构造工具，都按固定基准时刻造
│  ├─ test_geo.py            # 距离和坐标换算（不需要数据库）
│  ├─ test_segmentation.py   # 停走分段（不需要数据库）
│  ├─ test_classification.py # 停留分类（不需要数据库）
│  ├─ test_deviation.py      # 偏航检测（不需要数据库）
│  ├─ test_temperature.py    # 温度异常（不需要数据库）
│  ├─ test_simulator.py      # 模拟器（不需要数据库）
│  ├─ test_storage.py        # 数据库读写（需要 MySQL）
│  └─ test_api.py            # 接口（需要 MySQL）
├─ data/
│  └─ cache/                 # 高德接口返回的路线和 POI，缓存文件可以提交
└─ examples/
   └─ sample_route.json      # 示例路线和 POI，断网时兜底
```

## 5. 核心数据对象

先用 `dataclass` 定义，字段稳定后再落成 MySQL 表。

### 轨迹点 `TrackPoint`

- `task_id`：运输任务编号
- `timestamp`：Unix 秒
- `latitude`、`longitude`：经纬度（**GCJ-02 坐标系，见 6.5**）
- `speed`：米/秒
- `heading`：航向角
- `temperature`：车厢温度
- `refrigerator_on`：制冷机是否运行
- `door_open`：车门是否打开
- `position_quality`：定位质量

### 运输任务 `TransportTask`

- 起点、终点及计划路线（路线是一串经纬度点，来自高德路径规划）
- 计划开始/结束时间
- 货物类型及温度上下限

### 分段 `Segment`

- `segment_type`：`move` 或 `stop`
- 起止时间、持续秒数
- 起止位置、距离、平均速度
- `stop_type`：装货、卸货、加油、休息、异常停留或未知

### 异常事件

- 偏航事件：起止时间、持续时间、最远偏离距离
- 温度事件：开始/结束时间、温度范围、发生位置、是否处于停留段

### 对应的 MySQL 表

| 表名 | 存什么 |
| --- | --- |
| `task` | 运输任务，一行一次运输 |
| `route_point` | 规划路线的折线点，按 `seq` 排序 |
| `track_point` | 车辆上报的原始轨迹点，量最大 |
| `segment` | 分段结果，`move` / `stop` |
| `event` | 异常事件，偏航和温度共用一张表，用 `event_type` 区分 |
| `poi` | 加油站、服务区等兴趣点 |

两个建表时容易忽略但很重要的点：

1. `track_point` 上建联合索引 `(task_id, timestamp)`。轨迹查询几乎都是“按任务取点、按时间排序”，没这个索引几千行就会明显变慢。
2. 时间统一用 `BIGINT` 存 Unix 秒，不要用 `DATETIME`。省掉时区转换的麻烦，也和 Python 里的时间戳直接对应。

## 6. 关键规则和算法

### 6.1 停走分段

先使用固定参数，后续通过测试调整：

- `V_STOP = 0.5 m/s`：速度低于该值视为“准停留”
- `T_MIN = 180 s`：准停留连续超过 180 秒才算正式停留
- 轨迹点按时间排序；连续满足条件的点组成候选停留段
- 候选段长度不足 `T_MIN` 时合并回行驶段，避免把 40 秒红灯算成停留
- 对 GPS 漂移造成的单个低速点使用前后点连续性过滤

输出每个分段的起止时间、持续时间、距离和平均速度。

### 6.2 停留分类

按照“起点/终点优先，其次 POI，最后异常”的顺序判断：

1. 起点围栏内：起点装货。
2. 终点围栏内：终点卸货。
3. 加油站 POI 内：加油。
4. 中途装卸 POI 内且车门开启：中途装卸。
5. 其他合规时长停留：正常休息。
6. 不在任何 POI 且超过 `T_ABNORMAL = 1800 s`：异常停留。
7. 无法判断：未知。

围栏半径和 POI 类型全部写进 `config.py`（值从环境变量读，带默认值），不能直接散落在代码中。

POI 从哪来：优先用高德“周边搜索”接口（`/v3/place/around`）拉取，拉到的结果存进 `poi` 表和 `data/cache/`；拉不到就用 `examples/sample_route.json` 里手写的几个。

### 6.3 偏航识别

- 将规划路线表示为经纬度折线。
- 使用 Shapely 计算轨迹点到路线的距离。**Shapely 算的是平面上的度数，不是米**，所以要先换算：在纬度 φ 处，1 度纬度约等于 111320 米，1 度经度约等于 `111320 × cos(φ)` 米。把经纬度乘上这两个系数，就得到一个近似等距的平面坐标，再交给 Shapely 算距离。
- 距离超过 `ROUTE_BUFFER = 200 m` 的点标记为偏航点。
- 连续偏航点合并为一个偏航段，记录起止时间和最远距离。

换算函数放在 `app/geo.py` 里，偏航检测和 POI 围栏判断都复用它，不要各写一份。

### 6.4 温度异常与轨迹关联

- 冷冻货物示例阈值：上限 `-18℃`；冷藏货物示例阈值：`0~4℃`。
- 温度超限并持续超过 `T_TEMP = 300 s` 才生成事件。
- 用事件开始或峰值时刻的轨迹点确定地图位置。
- 根据该时刻是否落在停留段内，补充“停留期间/行驶中”和具体停留类型。
- 模拟器中制冷机开启时温度缓慢下降，关闭时缓慢上升，车门开启时上升更快；参数要在 README 或报告中说明。

### 6.5 坐标系（必须统一，否则地图会整体偏移）

这是接高德地图时最容易踩的坑，写在前面省得后面查半天：

- 高德地图用的是 **GCJ-02**（俗称火星坐标系）。
- 手机 GPS 输出的原始经纬度是 **WGS-84**。
- 两者在国内相差 300~600 米。这不是小误差——它会让整条轨迹在地图上整体平移，而且**偏航检测会把每一个点都判成偏航**，因为所有点到规划路线的距离都超过 200 米。

本项目采用最简单的处理方式：**全系统统一使用 GCJ-02**。

- 模拟器直接生成 GCJ-02 坐标，因为规划路线就是从高德拿的，本来就是 GCJ-02。
- `TrackPoint` 和 `task` 上各留一个 `coord_system` 字段（值为 `'gcj02'`），万一以后要接真实 GPS 数据，能一眼看出是不是混了坐标系。
- 如果以后确实要导入 WGS-84 的真实数据，转换公式放在 `app/geo.py`，转换后再入库，不要留到分析阶段再处理。
- 测试时不要拿真实 GPS 轨迹直接和高德路线比对，一定会全部偏航，那是坐标系问题不是算法问题。

## 7. 页面和接口的最小范围

### 页面

主页面包含：

- 任务选择和“生成示例数据”按钮
- 地图：规划路线、实际轨迹、偏航段、停留点、温度异常点
- 播放、暂停、倍速和时间轴拖动
- 当前时刻的时间、位置、速度、温度、制冷机和车门状态
- 统计卡片：运输时长、里程、平均速度、停留次数、温度达标率
- 异常列表和结论建议

地图初始化失败时（脚本加载不出来、Key 报错），自动切到简图模式，页面顶部给一条提示，其余功能照常。

### API

- `POST /api/simulate`：生成一次模拟任务（请求体可带 `task_id` / `seed` / `duration_s`）
- `GET /api/tasks`：查询任务列表（带每个任务的轨迹点数）
- `GET /api/tasks/<id>/route`：获取规划路线的折线点，地图上那条蓝线
- `GET /api/tasks/<id>/track`：获取轨迹点
- `GET /api/tasks/<id>/analysis`：获取分段、偏航、温度事件和统计
- `GET /api/tasks/<id>/report`：获取报告所需 JSON 数据
- `GET /api/config`：返回前端需要的高德 JS Key 和安全密钥
- `GET /api/health`：检查 MySQL 是否连得上、高德 Key 是否配好，出问题时第一个查它

路线和轨迹是两个接口，不能合并：规划路线来自高德，是用来做对比的基准线；
轨迹是车实际走的。地图上要同时画出这两条线，偏航才看得懂。

先保证 API 能返回 JSON，再做页面；这样前后端可以并行开发和调试。

## 8. 分阶段实施计划

### 阶段一：项目骨架和数据格式（1~2 天）

- 建立虚拟环境，固定 Python 3.14.6，写好 `requirements.txt`。
- 装好 MySQL，注册高德开放平台账号，申请好 Key。
- 复制 `.env.example` 为 `.env`，把数据库密码和 Key 填进去，确认 `.env` 已被 `.gitignore` 忽略。
- 定义数据类、示例路线和 3~5 个 POI。
- 执行 `sql/schema.sql` 建库建表，导入一条示例数据。

### 阶段二：模拟器（2~3 天）

- 先用高德路径规划接口取一条真实路线并缓存；取不到就用 `examples/sample_route.json`。
- 生成沿路线移动的轨迹点，采样周期建议 10 秒。
- 注入 5 分钟、25 分钟、40 秒停留。
- 增加偏航、制冷机关闭、车门开启等异常开关。
- 输出 JSON，并检查温度变化方向合理。
- 轨迹点多，入库用批量插入（`executemany`），不要一个点一条 `INSERT`。

### 阶段三：分析算法（4~5 天）

- 完成停走分段和单元测试。
- 完成 POI 停留分类。
- 完成偏航检测和温度事件关联。
- 生成统计结果。

### 阶段四：Flask API 和前端（4~5 天）

- 先完成四个核心 API，再加 `/api/config` 和 `/api/health`。
- 用高德地图 JS API 显示路线和轨迹。
- 实现时间轴拖动，再实现播放/暂停和调速。
- 保证地图上的位置、温度、速度来自同一条记录。
- 最后补简图模式兜底。

### 阶段五：验收、报告和演示（3~4 天）

- 按 T1~T7 验收用例逐条运行并保存结果。
- 整理截图、测试曲线、异常案例和参数论证。
- 完成 18~20 页报告、PPT 和演示录屏。
- 演示前**断网跑一遍**，确认简图模式和本地缓存都正常。

## 9. 测试重点

必须覆盖以下边界：

- 40 秒红灯不应生成停留。
- 恰好 180 秒和 181 秒的候选段结果要明确。
- 轨迹只有一个点、时间戳重复、定位点缺失时程序不能崩溃。
- POI 边界上的点分类一致。
- 偏离约 500 米 8 分钟时能生成一个偏航段。
- 温度恰好等于阈值时的判断规则要统一。
- 时间轴任意位置的地图、温度和速度必须来自同一条记录。
- 经纬度换算成米之后，同一个点算两次距离结果一致；向北移动 1000 米，算出来的距离应该在 1000 米附近。

每个算法函数先写测试，再接入 Flask；测试数据应固定随机种子，确保每次结果一致。

**让测试变简单的一个关键点**：`segmentation`、`classification`、`deviation`、`temperature` 这四块都是纯函数——输入一堆轨迹点，输出一堆分段/事件，不碰数据库、不调高德接口。所以这几块（还有 `geo` 和 `simulator`）的测试不需要 MySQL 也不需要网络，`pytest` 直接就能跑。只有 `test_storage.py` 和 `test_api.py` 需要连数据库，给它们单独准备一个 `cold_chain_test` 库，跑完删除。

### 上面每条边界对应的测试

跑 `pytest -q`，下面每一条都应该是绿的。报告里可以直接引这张表。

| 边界情况 | 测试写在哪 |
| --- | --- |
| 40 秒红灯不应生成停留 | `test_segmentation.py::test_red_light_40s_is_not_a_stop`（另有一条连遇 8 次红灯的） |
| 恰好 180 秒和 181 秒 | `test_segmentation.py::test_stop_of_exactly_T_MIN_counts`、`test_stop_one_second_shorter_does_not_count`、`test_stop_one_second_longer_counts` |
| 单点、时间戳重复、定位点缺失 | `test_segmentation.py::test_single_point_track_does_not_crash`、`test_duplicate_timestamps_do_not_crash`、`test_bad_position_quality_does_not_crash` |
| POI 边界上的点分类一致 | `test_classification.py::test_poi_boundary_is_inclusive`、`test_fence_radius_is_the_flip_point` |
| 偏 500 米 8 分钟生成一个偏航段 | `test_deviation.py::test_500m_off_route_for_8_minutes_is_one_event` |
| 温度恰好等于阈值 | `test_temperature.py::test_temperature_exactly_at_the_upper_limit_is_ok`、`test_temperature_exactly_at_the_lower_limit_is_ok`、`test_excursion_of_exactly_T_TEMP_is_ignored` |
| 位置、温度、速度来自同一条记录 | `test_api.py::test_track_points_are_complete_records`、`test_each_record_carries_its_own_position_and_temperature` |
| 同一距离算两次一致；向北 1000 米 ≈ 1000 米 | `test_geo.py::test_same_distance_computed_twice_is_identical`、`test_north_1000m_is_about_1000m` |

还有两条值得单独说的：

- `test_simulator.py::test_simulator_plants_cases_the_classifier_rediscovers`：模拟器知道标准答案（埋了红灯、加油、中途装卸、路边久停），分类算法只能看轨迹点、不许偷看那张表。两边独立算出来还能一一对上，才说明分析是真的成立——这是报告里最该写的一条验证。
- `test_api.py::test_config_never_leaks_the_web_key`：守住「Web 服务 Key 只能留在后端」这条硬约束。

## 10. 小组分工建议

- 1 人：模拟器、数据格式和 MySQL 建表
- 1 人：停走分段、停留分类、偏航算法
- 1 人：温度分析、统计报告和测试
- 1 人：Flask API、高德地图页面和最终联调

每个人都需要提交代码和测试，不要只负责截图或文档。使用 Git 的小分支开发，合并前先运行全部测试。

高德 Key 由一个人申请后统一发给大家，各自填进本机的 `.env`，谁都不许直接写死在代码里或提交上去。

## 11. 实现边界和优先级

### 必须完成

单车单任务、可重复模拟、四类核心分析、地图轨迹回放、统计报告、T1~T7 测试证据。

### 有余力再完成

自定义路线缓冲区、温度异常归因、准点率、多车对比、合规评分、历史批次回放、地图匹配、把 PyMySQL 换成 SQLAlchemy ORM、报告导出 PDF/Excel。

不要一开始就做登录、消息队列、微服务或真实硬件接入，这些内容会增加复杂度，却不是本次课程设计的验收重点。

## 12. 环境变量和密钥

所有配置都从环境变量读，`config.py` 只负责读，不写死具体值。

`.env.example`（**这个文件提交到仓库**，作为模板）：

```dotenv
# ---- MySQL ----
DB_HOST=127.0.0.1
DB_PORT=3306
DB_USER=root
DB_PASSWORD=
DB_NAME=cold_chain
DB_CHARSET=utf8mb4

# ---- 高德地图 ----
# JS API Key，给前端页面用的
AMAP_JS_KEY=
# JS API 2.0 的安全密钥，和上面的 Key 配对使用
AMAP_JS_SECURITY_CODE=
# Web 服务 Key，只给后端调接口用，绝对不能出现在网页里
AMAP_WEB_KEY=

# ---- 其他 ----
FLASK_DEBUG=1
FLASK_PORT=5000
```

`.env`（**不提交**，每个人本地复制一份改）：

```bash
cp .env.example .env
```

`.gitignore` 至少包含：

```gitignore
.venv/
__pycache__/
*.pyc
.env
.pytest_cache/
```

### 四个必须说清楚的安全点

1. **`.env` 绝对不能提交。** 密钥一旦进了 Git 历史，就算后面删掉文件也还留在历史里，得用 `git filter-repo` 之类的手段才能清干净，很麻烦。**提交前先跑 `git status`，看到 `.env` 就停下来。**

2. **`AMAP_WEB_KEY` 只能在后端使用。** 它带着你的配额，泄露出去别人可以拿去刷接口。它只出现在 `app/amap.py` 里，不经过任何接口返回给前端。

3. **`AMAP_JS_KEY` 藏不住，也不需要藏。** 它最终一定会进到浏览器里（不然地图加载不出来），打开开发者工具就能看到，这是官方设计如此。正确做法是去高德控制台给它配置**域名白名单**（本地开发填 `localhost`，上线填你的域名），这样别人拿到 Key 也用不了。

> **本地开发最容易卡住的一步**：`run.py` 绑的是 `127.0.0.1`，而白名单里通常只填了 `localhost`——这两个在高德那边算**不同的域名**。所以要么用 `http://localhost:5000` 打开页面，要么去白名单里把 `127.0.0.1` 也加上。不匹配的表现是页面顶部出现「地图加载失败，已切换简图模式」，功能都还在，但地图出不来。

4. **前端通过 `/api/config` 拿 JS Key，不要写死在 `app.js` 里。** 这样换 Key 不用改前端代码，而且 `AMAP_WEB_KEY` 不会被误带出去。

安全密钥必须在 JS API 脚本加载**之前**挂到 `window` 上，顺序反了会鉴权失败。本项目是在 `static/app.js` 里动态加载高德脚本的——先取配置，挂上安全密钥，再插入 `<script>`：

```javascript
window._AMapSecurityConfig = { securityJsCode: cfg.amap_js_security_code };

var script = document.createElement("script");
script.src = "https://webapi.amap.com/maps?v=2.0&key=" +
             encodeURIComponent(cfg.amap_js_key);
document.head.appendChild(script);
```

之所以不在 `index.html` 里用 Jinja 直接渲染，是因为 Key 只在 `/api/config` 这一个地方出，页面模板里看不到任何密钥，改 Key 也不用动模板。加载失败（断网、Key 过期、域名不在白名单）时 `app.js` 会捕获并切到简图模式，不会白屏。

## 13. 开发运行约定

```bash
# 1. 建虚拟环境并装依赖
python -m venv .venv
\.venv\Scripts\activate
pip install -r requirements.txt

# 2. 配置环境变量（第一次做，之后不用重复）
cp .env.example .env
# 然后用编辑器打开 .env，填上数据库密码和高德 Key

# 3. 初始化数据库
mysql -u root -p < sql/schema.sql

# 4. 导入示例数据（路线 + POI + 一条任务）
python scripts/seed_example.py

# 5. 启动
python run.py

# 6. 跑测试
pytest -q
```

`pytest -q` 能用的前提是虚拟环境已经激活。Git Bash 里如果 `activate` 没生效，
直接写全路径也一样，还能顺手把编码问题一起解决：

```bash
PYTHONIOENCODING=utf-8 PYTHONUTF8=1 ./.venv/Scripts/python.exe -m pytest -q
```

跑完应该看到 `168 passed`。这里面 52 条要连 MySQL（`test_storage.py` 和
`test_api.py`），它们会自己建一个 `cold_chain_test` 库、跑完删掉，不碰开发库
`cold_chain`。MySQL 没启动的话这两个文件会被跳过，其余测试照样跑完——
算法部分的测试本来就不需要数据库。

Windows 上用 `cp` 而不是 `copy`：`copy` 是 cmd 的内部命令，Git Bash 里没有。
Git Bash 里 `cp` 和 PowerShell 里 `cp` 都能用，记一个就够。

跑脚本时控制台中文变乱码，是 Windows 控制台默认用 GBK 编码显示，
Python 输出的是 UTF-8。加个环境变量就好，代码本身没问题：

```bash
PYTHONIOENCODING=utf-8 python scripts/seed_example.py
```

`config.py` 读取环境变量的写法（用 `python-dotenv` 加载 `.env`）：

```python
import os
from dotenv import load_dotenv

load_dotenv()  # 把 .env 里的键值对读进 os.environ

DB_CONFIG = {
    "host": os.getenv("DB_HOST", "127.0.0.1"),
    "port": int(os.getenv("DB_PORT", "3306")),
    "user": os.getenv("DB_USER", "root"),
    "password": os.getenv("DB_PASSWORD", ""),
    "database": os.getenv("DB_NAME", "cold_chain"),
    "charset": os.getenv("DB_CHARSET", "utf8mb4"),
}
```

最后：

- 演示前准备一键生成演示数据的命令，没有网络、没有真实 GPS 设备也能完整运行。
- 演示前先访问一次 `/api/health`，它会把 MySQL 连接和高德 Key 的问题直接报出来。
- 每人提交前跑一次 `git status`，确认没有 `.env`、没有 `__pycache__`。
