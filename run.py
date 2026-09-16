"""启动开发服务器。

    python run.py

启动后访问 http://127.0.0.1:5000/api/health 检查配置和数据库。
"""

from app import create_app
from config import FLASK_DEBUG, FLASK_PORT

app = create_app()

if __name__ == "__main__":
    app.run(host="127.0.0.1", port=FLASK_PORT, debug=FLASK_DEBUG)
