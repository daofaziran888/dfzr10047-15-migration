"""业务路由汇总：固定路径的清单路由先注册，通用资源路由兜底动态模块路径。"""
from __future__ import annotations

from app.routers import normalize, resource

ROUTERS = [normalize, resource]
