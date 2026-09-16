"""MySQL 连接管理。

两种用法：

    get_db()   —— 在 Flask 视图里用。同一个请求内复用一条连接，
                  请求结束由 close_db 自动关闭（见 init_app）。
    connect()  —— 在脚本和测试里用。自己开自己关，推荐配 with 语句。

本文件里的 query_* / execute / executemany 都用 get_db()，
所以它们必须在 Flask 应用上下文里调用。
"""

import pymysql
from flask import g

from config import DB_CONFIG


def connect():
    """开一条新的数据库连接。调用方负责关闭。"""
    return pymysql.connect(
        **DB_CONFIG,
        cursorclass=pymysql.cursors.DictCursor,  # 每行返回 dict，取值靠字段名而不是下标
        autocommit=False,                        # 手动提交，一批插入要么全成功要么全回滚
    )


def get_db():
    """取当前请求的连接，没有就新建。"""
    if "db" not in g:
        g.db = connect()
    return g.db


def close_db(exc=None):
    """请求结束时关掉连接。参数 exc 是 Flask 传进来的异常，这里用不到。"""
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_app(app):
    app.teardown_appcontext(close_db)


def query_all(sql, params=None):
    """执行 SELECT，返回所有行（每行是 dict）。"""
    with get_db().cursor() as cur:
        cur.execute(sql, params or ())
        return cur.fetchall()


def query_one(sql, params=None):
    """执行 SELECT，返回第一行；没有则返回 None。"""
    with get_db().cursor() as cur:
        cur.execute(sql, params or ())
        return cur.fetchone()


def insert(sql, params=None):
    """执行 INSERT，提交并返回新行的自增 id。"""
    conn = get_db()
    with conn.cursor() as cur:
        cur.execute(sql, params or ())
        new_id = cur.lastrowid
    conn.commit()
    return new_id


def execute(sql, params=None):
    """执行 UPDATE/DELETE，提交并返回受影响行数。"""
    conn = get_db()
    with conn.cursor() as cur:
        cur.execute(sql, params or ())
        affected = cur.rowcount
    conn.commit()
    return affected


def executemany(sql, seq_of_params):
    """批量执行同一条 INSERT，提交并返回插入行数。

    轨迹点一次几千条，逐条 insert 会明显变慢，必须用这个。
    """
    conn = get_db()
    with conn.cursor() as cur:
        affected = cur.executemany(sql, seq_of_params)
    conn.commit()
    return affected


def ping():
    """检查数据库是否连得上。返回 (是否正常, 说明文字)。"""
    try:
        conn = get_db()
        conn.ping(reconnect=True)
        row = query_one("SELECT VERSION() AS v, DATABASE() AS d")
        return True, f"MySQL {row['v']}，当前库 {row['d']}"
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}"
