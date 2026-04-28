from flask import Flask
from flask_cors import CORS
from config import Config
from db import init_db
from routes.patients import patients_bp
from routes.monitors import monitors_bp
from routes.evaluations import evaluations_bp
from routes.traces import traces_bp

def create_app():
    app = Flask(__name__)
    CORS(app, resources={r"/api/*": {"origins": "*"}})  # 允许所有前端访问
    # CORS(app, supports_credentials=True)  # 允许所有跨域请求
    # app.config.from_object(Config)
    # CORS(app)  # 允许跨域


    # 注册蓝图
    app.register_blueprint(patients_bp)
    app.register_blueprint(monitors_bp)
    app.register_blueprint(evaluations_bp)
    app.register_blueprint(traces_bp)

    # 数据库初始化：先建库/建表，再初始化连接池（顺序关键）
    init_db()  # 优先执行：创建数据库+执行init.sql建表

    # init_pool() 无需单独调用，init_db() 内部最后已调用 init_pool()

    @app.route('/')
    def index():
        return "心胸围术期肺康复护理行为监测平台 API (Python Flask)"

    return app

if __name__ == '__main__':
    app = create_app()
    app.run(host='0.0.0.0', port=3000, debug=True)