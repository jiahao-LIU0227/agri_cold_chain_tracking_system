-- ============================================================
--  农产品冷链途迹监测系统 - 数据库初始化脚本
--
--  执行方式（在项目根目录）：
--      mysql -u root -p < sql/schema.sql
--
--  ⚠️ 警告：本脚本会先 DROP 掉已有的同名表，已有数据会被清空。
--     只在「第一次初始化」或「需要重置数据库」时运行。
--     平时不要执行。
--
--  约定：
--    1. 所有时间统一用 BIGINT 存 Unix 秒，不用 DATETIME，避免时区问题。
--    2. 所有经纬度都是 GCJ-02（高德坐标系），不是 WGS-84，详见 README 6.5。
--    3. 布尔值用 TINYINT，0 表示否，1 表示是。
-- ============================================================

-- 排序规则跟着服务器默认值走（本机 my.ini 里是 utf8mb4_0900_ai_ci），
-- 全库统一，避免以后 JOIN 时报 collation 冲突
CREATE DATABASE IF NOT EXISTS cold_chain
    DEFAULT CHARACTER SET utf8mb4
    DEFAULT COLLATE utf8mb4_0900_ai_ci;

USE cold_chain;

-- 先删子表再删主表，否则外键会拦着不让删
DROP TABLE IF EXISTS event;
DROP TABLE IF EXISTS segment;
DROP TABLE IF EXISTS track_point;
DROP TABLE IF EXISTS route_point;
DROP TABLE IF EXISTS poi;
DROP TABLE IF EXISTS task;


-- ------------------------------------------------------------
-- 运输任务：一行代表一次完整的冷链运输
-- ------------------------------------------------------------
CREATE TABLE task (
    id            INT AUTO_INCREMENT PRIMARY KEY,
    task_no       VARCHAR(32)   NOT NULL COMMENT '任务编号，对外展示用，如 T20260916001',
    origin_name   VARCHAR(64)   NOT NULL COMMENT '起点名称',
    dest_name     VARCHAR(64)   NOT NULL COMMENT '终点名称',
    origin_lat    DECIMAL(10,7) NOT NULL COMMENT '起点纬度 GCJ-02',
    origin_lng    DECIMAL(10,7) NOT NULL COMMENT '起点经度 GCJ-02',
    dest_lat      DECIMAL(10,7) NOT NULL COMMENT '终点纬度 GCJ-02',
    dest_lng      DECIMAL(10,7) NOT NULL COMMENT '终点经度 GCJ-02',
    cargo_type    VARCHAR(16)   NOT NULL COMMENT '货物类型：frozen冷冻 / chilled冷藏',
    temp_min      DECIMAL(5,2)  NOT NULL COMMENT '允许温度下限 ℃',
    temp_max      DECIMAL(5,2)  NOT NULL COMMENT '允许温度上限 ℃',
    planned_start BIGINT        NOT NULL COMMENT '计划开始时间 Unix秒',
    planned_end   BIGINT        NOT NULL COMMENT '计划结束时间 Unix秒',
    coord_system  VARCHAR(16)   NOT NULL DEFAULT 'gcj02' COMMENT '坐标系，固定 gcj02',
    created_at    BIGINT        NOT NULL COMMENT '创建时间 Unix秒',
    UNIQUE KEY uk_task_no (task_no)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='运输任务';


-- ------------------------------------------------------------
-- 规划路线：从高德路径规划接口拿到的折线点，按 seq 顺序连起来
-- ------------------------------------------------------------
CREATE TABLE route_point (
    id        INT AUTO_INCREMENT PRIMARY KEY,
    task_id   INT           NOT NULL COMMENT '所属任务',
    seq       INT           NOT NULL COMMENT '第几个点，从 0 开始，越小越靠近起点',
    latitude  DECIMAL(10,7) NOT NULL COMMENT '纬度 GCJ-02',
    longitude DECIMAL(10,7) NOT NULL COMMENT '经度 GCJ-02',
    UNIQUE KEY uk_task_seq (task_id, seq),
    CONSTRAINT fk_route_task FOREIGN KEY (task_id)
        REFERENCES task(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='规划路线折线点';


-- ------------------------------------------------------------
-- 轨迹点：车辆上报的原始数据，全库数据量最大的表
--
-- 注意：这里故意不加 (task_id, ts) 的唯一约束。
-- 验收要求「时间戳重复时程序不能崩溃」，所以重复数据要能存进来，
-- 由分析算法负责处理，而不是靠数据库直接拒绝。
-- ------------------------------------------------------------
CREATE TABLE track_point (
    id               BIGINT AUTO_INCREMENT PRIMARY KEY,
    task_id          INT           NOT NULL COMMENT '所属任务',
    ts               BIGINT        NOT NULL COMMENT '上报时间 Unix秒',
    latitude         DECIMAL(10,7) NOT NULL COMMENT '纬度 GCJ-02',
    longitude        DECIMAL(10,7) NOT NULL COMMENT '经度 GCJ-02',
    speed            DECIMAL(6,2)  NOT NULL DEFAULT 0 COMMENT '速度 米/秒',
    heading          DECIMAL(6,2)  NOT NULL DEFAULT 0 COMMENT '航向角 0~360',
    temperature      DECIMAL(5,2)  NOT NULL COMMENT '车厢温度 ℃',
    refrigerator_on  TINYINT       NOT NULL DEFAULT 0 COMMENT '制冷机是否运行 0否1是',
    door_open        TINYINT       NOT NULL DEFAULT 0 COMMENT '车门是否打开 0否1是',
    position_quality TINYINT       NOT NULL DEFAULT 1 COMMENT '定位质量 0差 1好',
    KEY idx_task_ts (task_id, ts) COMMENT '查轨迹都是按任务取点、按时间排序，必须有这个索引',
    CONSTRAINT fk_track_task FOREIGN KEY (task_id)
        REFERENCES task(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='车辆上报的原始轨迹点';


-- ------------------------------------------------------------
-- 分段：停走分段的输出结果
-- ------------------------------------------------------------
CREATE TABLE segment (
    id           INT AUTO_INCREMENT PRIMARY KEY,
    task_id      INT           NOT NULL COMMENT '所属任务',
    segment_type VARCHAR(8)    NOT NULL COMMENT 'move行驶 / stop停留',
    stop_type    VARCHAR(16)   NULL COMMENT '停留类型：load装货 unload卸货 refuel加油 rest休息 abnormal异常停留 unknown未知；行驶段为 NULL',
    start_ts     BIGINT        NOT NULL COMMENT '开始时间 Unix秒',
    end_ts       BIGINT        NOT NULL COMMENT '结束时间 Unix秒',
    duration_s   INT           NOT NULL COMMENT '持续秒数',
    start_lat    DECIMAL(10,7) NOT NULL COMMENT '起点纬度',
    start_lng    DECIMAL(10,7) NOT NULL COMMENT '起点经度',
    end_lat      DECIMAL(10,7) NOT NULL COMMENT '终点纬度',
    end_lng      DECIMAL(10,7) NOT NULL COMMENT '终点经度',
    distance_m   DECIMAL(10,2) NOT NULL DEFAULT 0 COMMENT '该段行驶距离 米',
    avg_speed    DECIMAL(6,2)  NOT NULL DEFAULT 0 COMMENT '平均速度 米/秒',
    KEY idx_task_start (task_id, start_ts),
    CONSTRAINT fk_segment_task FOREIGN KEY (task_id)
        REFERENCES task(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='停走分段结果';


-- ------------------------------------------------------------
-- 异常事件：偏航和温度共用一张表，靠 event_type 区分
-- 两种事件的字段不完全一样，用不到的那部分留 NULL
-- ------------------------------------------------------------
CREATE TABLE event (
    id             INT AUTO_INCREMENT PRIMARY KEY,
    task_id        INT           NOT NULL COMMENT '所属任务',
    event_type     VARCHAR(16)   NOT NULL COMMENT 'deviation偏航 / temperature温度异常',
    start_ts       BIGINT        NOT NULL COMMENT '事件开始时间 Unix秒',
    end_ts         BIGINT        NOT NULL COMMENT '事件结束时间 Unix秒',
    duration_s     INT           NOT NULL COMMENT '持续秒数',
    max_distance_m DECIMAL(10,2) NULL COMMENT '【偏航】最远偏离规划路线 米',
    temp_min       DECIMAL(5,2)  NULL COMMENT '【温度】事件期间最低温 ℃',
    temp_max       DECIMAL(5,2)  NULL COMMENT '【温度】事件期间最高温 ℃',
    latitude       DECIMAL(10,7) NULL COMMENT '事件在地图上的标记位置纬度',
    longitude      DECIMAL(10,7) NULL COMMENT '事件在地图上的标记位置经度',
    during_stop    TINYINT       NOT NULL DEFAULT 0 COMMENT '是否发生在停留期间 0否1是',
    segment_id     INT           NULL COMMENT '关联的分段 id，便于反查停留类型',
    KEY idx_task_type (task_id, event_type),
    CONSTRAINT fk_event_task FOREIGN KEY (task_id)
        REFERENCES task(id) ON DELETE CASCADE,
    CONSTRAINT fk_event_segment FOREIGN KEY (segment_id)
        REFERENCES segment(id) ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='异常事件（偏航 / 温度）';


-- ------------------------------------------------------------
-- POI 兴趣点：加油站、服务区、中途装卸点等
-- 不属于某个任务，所有任务共用
-- ------------------------------------------------------------
CREATE TABLE poi (
    id        INT AUTO_INCREMENT PRIMARY KEY,
    name      VARCHAR(128)  NOT NULL COMMENT 'POI 名称',
    poi_type  VARCHAR(16)   NOT NULL COMMENT 'gas_station加油站 / service_area服务区 / loading_dock装卸点 / other其他',
    latitude  DECIMAL(10,7) NOT NULL COMMENT '纬度 GCJ-02',
    longitude DECIMAL(10,7) NOT NULL COMMENT '经度 GCJ-02',
    radius_m  INT           NOT NULL DEFAULT 200 COMMENT '围栏半径 米，落在这个圈里就算在该 POI 内',
    source    VARCHAR(16)   NOT NULL DEFAULT 'amap' COMMENT '来源：amap高德搜索 / manual手写',
    amap_id   VARCHAR(64)   NULL COMMENT '高德返回的 POI id，用来去重，避免重复插入同一个点',
    UNIQUE KEY uk_amap_id (amap_id),
    KEY idx_type (poi_type)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='POI 兴趣点';


-- ------------------------------------------------------------
-- 建好之后可以用这些语句确认一下
-- ------------------------------------------------------------
-- SHOW TABLES;
-- DESC track_point;
-- SHOW INDEX FROM track_point;
