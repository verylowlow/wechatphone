# -*- coding: utf-8 -*-
"""wechatphone autodial web API + mini UI (multi-app).

Usage:  python autodial_app.py [--port 8767]
Endpoints:
  GET  /                 简易拨号表单页 (含应用选择器)
  POST /api/dial         {"app","contact","task","note","dry_run"} -> 单个拨号
  POST /api/batch        {"app","items":[...]} -> 批量(后台线程)
  GET  /api/jobs         批量任务状态
  GET  /api/calib?app=   指定应用的校准信息
"""
import argparse
import json
import os
import sys
import threading
import time
import uuid

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from flask import Flask, jsonify, request  # noqa: E402

app = Flask(__name__)

# 内存里的批量任务表 (重启丢失; 结果以 calllog 为准)
JOBS: dict[str, dict] = {}
JOBS_LOCK = threading.Lock()

PAGE = """<!doctype html><html><head><meta charset="utf-8"><title>自动拨号 - wechatphone</title>
<style>
:root { color-scheme: dark; }
body { background:#111418; color:#e6e6e6; font-family:"Segoe UI","Microsoft YaHei",sans-serif;
       margin:0; padding:24px; max-width:760px; margin-inline:auto; }
h1 { font-size:20px; }
.card { background:#181c22; border-radius:10px; padding:18px; margin:14px 0; }
label { display:block; font-size:13px; color:#8a919b; margin:10px 0 4px; }
input[type=text], textarea, select { width:100%; box-sizing:border-box; background:#0f1216; color:#e6e6e6;
       border:1px solid #2a2f36; border-radius:6px; padding:8px 10px; font-size:14px; }
textarea { min-height:70px; resize:vertical; }
button { background:#2f6feb; color:#fff; border:0; border-radius:6px; padding:9px 18px;
       font-size:14px; cursor:pointer; margin-top:12px; }
button.gray { background:#3a4048; }
pre { background:#0f1216; padding:10px; border-radius:6px; white-space:pre-wrap;
      font-size:12px; color:#c9c9c9; max-height:260px; overflow:auto; }
.hint { font-size:12px; color:#8a919b; margin-top:8px; }
</style></head><body>
<h1>语音自动拨号</h1>
<div class="card">
  <b>目标应用</b>
  <label>选择要拨打的应用端 (需先用 CLI 对对应应用完成校准)</label>
  <select id="app">
    <option value="wechat">微信</option>
    <option value="dingtalk">钉钉</option>
    <option value="wecom">企业微信</option>
  </select>
  <div class="hint" id="calib">校准状态: 加载中...</div>
</div>
<div class="card">
  <b>单个拨号</b>
  <label>联系人 (应用内备注名/昵称, 搜索框能搜到的名字)</label>
  <input type="text" id="contact" placeholder="例如: 张三">
  <label>任务内容 = 本次通话核心目的 (注入给 AI, 可空)</label>
  <textarea id="task" placeholder="例如: 回访确认是否收到货, 引导好评"></textarea>
  <label>开场白 (AI 接通后第一句话, 可空=用默认 OUTBOUND_DEFAULT_OPENING)</label>
  <input type="text" id="opening" placeholder="例如: 张总您好, 我是XX公司的小李">
  <label>备注 (可空)</label>
  <input type="text" id="note">
  <br><button onclick="doDial()">拨打</button>
  <button class="gray" onclick="doDial(true)">试跑(不实际点击)</button>
</div>
<div class="card">
  <b>批量拨号</b>
  <label>每行一个: 联系人名|任务内容|开场白 (任务和开场白可省略)</label>
  <textarea id="batch" placeholder="张三|回访确认收货|张总您好\\n李四|通知明天活动\\n王五"></textarea>
  <br><button onclick="doBatch()">开始批量</button>
</div>
<div class="card"><b>结果</b><pre id="out">(暂无)</pre></div>
<script>
function curApp() { return document.getElementById('app').value; }
async function j(url, body) {
  const r = await fetch(url, {method:'POST', headers:{'Content-Type':'application/json'},
    body: JSON.stringify(body)});
  return await r.json();
}
async function doDial(dry) {
  const out = document.getElementById('out');
  out.textContent = '拨号中...';
  const r = await j('/api/dial', {
    app: curApp(),
    contact: document.getElementById('contact').value,
    task: document.getElementById('task').value,
    opening: document.getElementById('opening').value,
    note: document.getElementById('note').value,
    dry_run: !!dry});
  out.textContent = JSON.stringify(r, null, 2);
}
async function doBatch() {
  const items = document.getElementById('batch').value.trim().split('\\n').map(l => {
    const parts = l.split('|');
    return {contact: (parts[0]||'').trim(),
            task: (parts[1]||'').trim(),
            opening: (parts[2]||'').trim()};
  }).filter(x => x.contact);
  const out = document.getElementById('out');
  out.textContent = '提交中...';
  const r = await j('/api/batch', {app: curApp(), items});
  out.textContent = JSON.stringify(r, null, 2);
  if (r.job_id) poll(r.job_id);
}
async function poll(jobId) {
  const out = document.getElementById('out');
  for (let i = 0; i < 600; i++) {
    await new Promise(r => setTimeout(r, 3000));
    const r = await (await fetch('/api/jobs')).json();
    const job = r.find(x => x.job_id === jobId);
    if (!job) return;
    out.textContent = `状态: ${job.status} (${job.done}/${job.total})\\n` +
      JSON.stringify(job.results || [], null, 2);
    if (job.status !== 'running') return;
  }
}
async function refreshCalib() {
  const c = await (await fetch('/api/calib?app=' + curApp())).json();
  document.getElementById('calib').textContent = c.calibrated_at
    ? `校准状态: 已校准 (${c.calibrated_at})`
    : `校准状态: 未校准! 请先运行 python -m autodial.cli calibrate --app ${curApp()}`;
}
document.getElementById('app').addEventListener('change', refreshCalib);
refreshCalib();
</script></body></html>"""


@app.route("/")
def index():
    return PAGE


@app.route("/api/calib")
def api_calib():
    from autodial.taskfile import load_calib
    app_key = (request.args.get("app") or "wechat").strip().lower()
    return jsonify(load_calib(app_key) or {})


@app.route("/api/dial", methods=["POST"])
def api_dial():
    from autodial.dialer import AppDialer, DialError
    body = request.get_json(force=True, silent=True) or {}
    contact = (body.get("contact") or "").strip()
    app_key = (body.get("app") or "wechat").strip().lower()
    if not contact:
        return jsonify({"error": "contact 不能为空"}), 400
    try:
        d = AppDialer(dry_run=bool(body.get("dry_run")), app=app_key)
        info = d.dial(contact, task=body.get("task", ""), note=body.get("note", ""),
                      opening=(body.get("opening") or "").strip())
        return jsonify({"ok": True, **info})
    except DialError as e:
        return jsonify({"ok": False, "error": str(e)}), 400
    except Exception as e:  # noqa: BLE001
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/batch", methods=["POST"])
def api_batch():
    from autodial.batch import BatchDialer
    body = request.get_json(force=True, silent=True) or {}
    items = [x for x in (body.get("items") or []) if (x.get("contact") or "").strip()]
    app_key = (body.get("app") or "wechat").strip().lower()
    if not items:
        return jsonify({"error": "items 为空"}), 400
    job_id = uuid.uuid4().hex[:8]
    with JOBS_LOCK:
        JOBS[job_id] = {"job_id": job_id, "status": "running",
                        "total": len(items), "done": 0, "results": [],
                        "app": app_key,
                        "created_at": time.strftime("%Y-%m-%d %H:%M:%S")}

    def run_job():
        try:
            b = BatchDialer(dry_run=bool(body.get("dry_run")), app=app_key)
            shared_opening = (body.get("opening") or "").strip()
            results = []
            for it in items:
                r = b.run([it], opening=shared_opening)[0]
                results.append(r)
                with JOBS_LOCK:
                    JOBS[job_id]["done"] += 1
                    JOBS[job_id]["results"] = list(results)
            with JOBS_LOCK:
                JOBS[job_id]["status"] = "done"
        except Exception as e:  # noqa: BLE001
            with JOBS_LOCK:
                JOBS[job_id]["status"] = f"error: {e}"

    threading.Thread(target=run_job, daemon=True).start()
    return jsonify({"ok": True, "job_id": job_id, "total": len(items)})


@app.route("/api/jobs")
def api_jobs():
    with JOBS_LOCK:
        return jsonify(list(JOBS.values()))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8767)
    ap.add_argument("--host", default="127.0.0.1")
    args = ap.parse_args()
    print(f"[AUTODIAL] web API: http://{args.host}:{args.port}", flush=True)
    app.run(host=args.host, port=args.port, debug=False)


if __name__ == "__main__":
    main()
