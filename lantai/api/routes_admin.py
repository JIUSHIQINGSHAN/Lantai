import asyncio
import json
import os
import platform
import time

import psutil
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import HTMLResponse, StreamingResponse

from lantai.core.auth import Principal, get_current_user
from lantai.core.settings import settings

router = APIRouter()

def require_admin(principal: Principal = Depends(get_current_user)) -> Principal:
    if principal.role != "admin":
        raise HTTPException(status_code=403, detail="Admin privileges required")
    return principal

ADMIN_HTML = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <title>Lantai Admin Dashboard</title>
    <style>
        body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif; margin: 0; padding: 20px; background: #f5f7fa; color: #333; }
        .container { max-width: 1000px; margin: 0 auto; }
        h1 { color: #2c3e50; }
        .card-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 20px; margin-top: 20px; }
        .card { background: #fff; border-radius: 8px; padding: 20px; box-shadow: 0 2px 4px rgba(0,0,0,0.1); }
        .card h3 { margin-top: 0; font-size: 14px; color: #7f8c8d; text-transform: uppercase; }
        .card .value { font-size: 28px; font-weight: bold; color: #34495e; }
        .sys-info { margin-top: 20px; font-size: 14px; color: #7f8c8d; }
    </style>
</head>
<body>
    <div class="container">
        <h1>Lantai Admin Dashboard</h1>
        <div id="sys-info" class="sys-info">加载系统信息中...</div>
        <div class="card-grid">
            <div class="card">
                <h3>CPU 使用率</h3>
                <div class="value" id="cpu-value">-- %</div>
            </div>
            <div class="card">
                <h3>内存使用率</h3>
                <div class="value" id="mem-value">-- %</div>
            </div>
            <div class="card">
                <h3>系统负载</h3>
                <div class="value" id="load-value">--</div>
            </div>
        </div>
    </div>
    
    <script>
        // 加载静态系统信息
        fetch('/admin/api/sysinfo')
            .then(res => res.json())
            .then(data => {
                document.getElementById('sys-info').innerText = 
                    `OS: ${data.system} ${data.release} | Python: ${data.python_version} | 运行时间: ${data.uptime}`;
            });

        // 监听 SSE 数据流
        const evtSource = new EventSource('/admin/api/stream');
        evtSource.onmessage = function(event) {
            const data = JSON.parse(event.data);
            if (data.type === 'stats') {
                document.getElementById('cpu-value').innerText = data.cpu_percent + ' %';
                document.getElementById('mem-value').innerText = data.memory_percent + ' %';
                document.getElementById('load-value').innerText = data.load_avg;
            }
        };
    </script>
</body>
</html>
"""

@router.get("/admin/dashboard", response_class=HTMLResponse)
async def admin_dashboard(principal: Principal = Depends(require_admin)):
    """返回管理员监控面板的前端页面"""
    return HTMLResponse(content=ADMIN_HTML)

_START_TIME = time.time()

@router.get("/admin/api/sysinfo")
async def get_sysinfo(principal: Principal = Depends(require_admin)):
    """返回静态系统信息"""
    uptime_seconds = int(time.time() - _START_TIME)
    hours, remainder = divmod(uptime_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    uptime_str = f"{hours}h {minutes}m {seconds}s"
    
    return {
        "system": platform.system(),
        "release": platform.release(),
        "python_version": platform.python_version(),
        "uptime": uptime_str,
        "pid": os.getpid()
    }

@router.get("/admin/api/stream")
async def admin_stream(principal: Principal = Depends(require_admin)):
    """SSE 流推送服务器实时指标"""
    async def event_stream():
        while True:
            cpu_percent = psutil.cpu_percent(interval=None)
            mem = psutil.virtual_memory()
            
            # 负载特征，Windows 不支持 getloadavg
            try:
                load = os.getloadavg()
                load_str = f"{load[0]:.2f}, {load[1]:.2f}, {load[2]:.2f}"
            except AttributeError:
                load_str = "N/A (Windows)"
                
            data = {
                "type": "stats",
                "cpu_percent": cpu_percent,
                "memory_percent": mem.percent,
                "load_avg": load_str,
                "timestamp": time.time()
            }
            yield f"data: {json.dumps(data)}\n\n"
            await asyncio.sleep(2)
            
    return StreamingResponse(event_stream(), media_type="text/event-stream")
