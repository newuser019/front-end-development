import os
import mysql.connector
from mysql.connector import Error, pooling
from config import Config

# 全局变量：连接池 + 初始化标记（避免重复初始化）
db_pool = None
db_initialized = False  # 新增：标记是否已完成初始化


def init_pool():
    """初始化数据库连接池（仅执行一次）"""
    global db_pool
    if db_pool is not None:
        return  # 已初始化则直接返回，避免重复创建
    try:
        db_pool = pooling.MySQLConnectionPool(
            pool_name="mypool",
            pool_size=20,  # 并发高可调至20-30（不超过MySQL max_connections）
            pool_reset_session=True,  # 归还连接时重置会话（避免事务残留）
            host=Config.MYSQL_HOST,
            user=Config.MYSQL_USER,
            password=Config.MYSQL_PASSWORD,
            database=Config.MYSQL_DATABASE,
            autocommit=False,
            charset='utf8mb4'  # 统一字符集
        )
    except Error as e:
        print(f"❌ 连接池初始化失败: {e}")
        raise  # 主动抛出异常，让上层感知


def init_db():
    global db_initialized
    if db_initialized:
        return

    # 第一步：创建数据库（独立连接）
    try:
        with mysql.connector.connect(
                host=Config.MYSQL_HOST,
                user=Config.MYSQL_USER,
                password=Config.MYSQL_PASSWORD
        ) as conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    f"CREATE DATABASE IF NOT EXISTS {Config.MYSQL_DATABASE} "
                    f"DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"
                )
        print("✅ 数据库创建/已存在")
    except Error as e:
        print(f"❌ 创建数据库失败: {e}")
        raise

    # 第二步：连接目标库执行建表（独立连接，事务原子性）
    conn = get_db_connection()
    if not conn:
        raise Exception("无法连接到目标数据库")

    try:
        conn.autocommit = False
        cursor = conn.cursor()

        sql_file_path = os.path.join(os.path.dirname(__file__), 'init.sql')
        if not os.path.exists(sql_file_path):
            print(f"⚠️  init.sql文件不存在，跳过建表")
            return

        with open(sql_file_path, 'r', encoding='utf-8') as f:
            sql_script = f.read()

        for statement in sql_script.split(';'):
            stmt = statement.strip()
            if stmt:
                cursor.execute(stmt)

        conn.commit()
        cursor.close()
        conn.close()
        print("✅ MySQL 数据库初始化完成 ✅")
        db_initialized = True
    except Error as e:
        print(f"❌ 建表失败: {e}")
        if conn:
            conn.rollback()
            conn.close()
        raise


def get_db_connection():
    """获取数据库连接（优化版：简化逻辑+减少冗余）"""
    global db_pool
    try:
        # 优先使用连接池
        if db_pool is None:
            init_pool()
        conn = db_pool.get_connection()
        return conn
    except Error as e:
        print(f"❌ 连接池获取连接失败，尝试兜底连接: {e}")
        # 兜底创建单次连接
        try:
            conn = mysql.connector.connect(
                host=Config.MYSQL_HOST,
                database=Config.MYSQL_DATABASE,
                user=Config.MYSQL_USER,
                password=Config.MYSQL_PASSWORD,
                charset='utf8mb4'
            )
            return conn
        except Error as e2:
            print(f"❌ 兜底连接也失败: {e2}")
            return None