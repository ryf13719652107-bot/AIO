"""仅用于本地预览启动 — 锁定 CWD 到 backend/ 以便 .env 与 sqlite 路径生效。"""
import os
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
os.chdir(ROOT)
sys.path.insert(0, ROOT)

import uvicorn

if __name__ == "__main__":
    uvicorn.run("app.main:app", host="127.0.0.1", port=3003, log_level="info")
