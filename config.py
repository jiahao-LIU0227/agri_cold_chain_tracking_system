"""配置。

所有值都从环境变量读，代码里不写死具体值。
环境变量清单见 .env.example，本地真实值放 .env（不提交）。
"""

import os

from dotenv import load_dotenv

load_dotenv()  # 把 .env 里的键值对读进 os.environ


def _get_int(name, default):
    return int(os.getenv(name, default))


def _get_float(name, default):
    return float(os.getenv(name, default))


def _get_bool(name, default):
    return os.getenv(name, default).strip().lower() in ("1", "true", "yes", "on")


# ---- MySQL ----
DB_CONFIG = {
    "host": os.getenv("DB_HOST", "127.0.0.1"),
    "port": _get_int("DB_PORT", "3306"),
    "user": os.getenv("DB_USER", "root"),
    "password": os.getenv("DB_PASSWORD", ""),
    "database": os.getenv("DB_NAME", "cold_chain"),
    "charset": os.getenv("DB_CHARSET", "utf8mb4"),
}

# ---- 高德地图 ----
# AMAP_JS_KEY 会注入到页面里给浏览器加载地图用，藏不住，靠控制台域名白名单保护
# AMAP_WEB_KEY 只在后端调接口用，绝对不能返回给前端
AMAP_JS_KEY = os.getenv("AMAP_JS_KEY", "")
AMAP_JS_SECURITY_CODE = os.getenv("AMAP_JS_SECURITY_CODE", "")
AMAP_WEB_KEY = os.getenv("AMAP_WEB_KEY", "")

# ---- 算法阈值 ----
# 先按经验设定，后面通过测试调整。改 .env 即可，不用动代码。

# 速度低于该值(m/s)视为「准停留」
V_STOP = _get_float("V_STOP", "0.5")
# 准停留连续超过该秒数才算正式停留，避免把红灯算成停留
T_MIN = _get_int("T_MIN", "180")
# 不在任何 POI 内且超过该秒数算异常停留
T_ABNORMAL = _get_int("T_ABNORMAL", "1800")
# 温度超限持续超过该秒数才生成事件
T_TEMP = _get_int("T_TEMP", "300")
# 偏离规划路线超过该米数算偏航
ROUTE_BUFFER = _get_float("ROUTE_BUFFER", "200")
# 起点/终点围栏半径(米)
GEOFENCE_RADIUS = _get_float("GEOFENCE_RADIUS", "200")

# ---- 采样与模拟 ----
SAMPLE_INTERVAL = _get_int("SAMPLE_INTERVAL", "10")
RANDOM_SEED = _get_int("RANDOM_SEED", "42")

# ---- Flask ----
FLASK_DEBUG = _get_bool("FLASK_DEBUG", "1")
FLASK_PORT = _get_int("FLASK_PORT", "5000")


def missing_settings():
    """返回没配好的配置项名称，供 /api/health 使用。

    数据库密码为空是常见情况，单独提示。
    """
    missing = []
    if not DB_CONFIG["password"]:
        missing.append("DB_PASSWORD")
    if not AMAP_JS_KEY:
        missing.append("AMAP_JS_KEY")
    if not AMAP_JS_SECURITY_CODE:
        missing.append("AMAP_JS_SECURITY_CODE")
    if not AMAP_WEB_KEY:
        missing.append("AMAP_WEB_KEY")
    return missing
