# -*- coding: utf-8 -*-
"""wechatphone autodial web API + mini UI.

Usage:  python autodial_app.py [--port 8767]
Endpoints:
  GET  /                 简易拨号表单页
  POST /api/dial         {"contact": str, "task": str, "note": str}  -> 单个拨号
  POST /api/batch        {"items": [{"contact","task","note"}], ...} -> 批量(后台线程)
  GET  /api/jobs         批量任务状态
  GET  /api/calib        当前校准信息
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
input[type=text], textarea { width:100%; box-sizing:border-box; background:#0f1216; color:#e6e6e6;
       border:1px solid #2a2f36; border-radius:6px; padding:8px 10px; font-size:14px; }
textarea { min-height:70px; resize:vertical; }
button { background:#2f6feb; color:#fff; border:0; border-radius:6px; padding:9px 18px;
       font-size:14px; cursor:pointer; margin-top:12px; }
button.gray { background:#3a4048; }
pre { background:#0f1216; padding:10px; border-radius:6px; white-space:pre-wrap;
      font-size:12px; color:#c9c9c9; max-height:260px; overflow:auto; }
.hint { font-size:12px; color:#8a919b; margin-top:8px; }
</style></head><body>
<h1>微信自动拨号</h1>
<div class="hint" id="calib">校准状态: 加载中...</div>
<div class="card">
  <b>单个拨号</b>
  <label>联系人 (微信备注名/昵称, 搜索框能搜到的名字)</label>
  <input type="text" id="contact" placeholder="例如: 张三">
  <label>任务内容 (注入给 AI, 可空)</label>
  <textarea id="task" placeholder="例如: 回访确认是否收到货, 引导好评"></textarea>
  <label>备注 (可空)</label>
  <input type="text" id="note">
  <br><button onclick="doDial()">拨打</button>
  <button class="gray" onclick="doDial(true)">试跑(不实际点击)</button>
</div>
<div class="card">
  <b>批量拨号</b>
  <label>每行一个: 联系人名|任务内容 (任务可省略)</label>
  <textarea id="batch" placeholder="张三|回访确认收货\n李四|通知明天活动\n王五"></textarea>
  <br><button onclick="doBatch()">开始批量</button>
</div>
<div class="card"><b>结果</b><pre id="out">(暂无)</pre></div>
<script>
async function j(url, body) {
  const r = await fetch(url, {method:'POST', headers:{'Content-Type':'application/json'},
    body: JSON.stringify(body)});
  return await r.json();
}
async function doDial(dry) {
  const out = document.getElementById('out');
  out.textContent = '拨号中...';
  const r = await j('/api/dial', {
    contact: document.getElementById('contact').value,
    task: document.getElementById('task').value,
    note: document.getElementById('note').value,
    dry_run: !!dry});
  out.textContent = JSON.stringify(r, null, 2);
}
async function doBatch() {
  const items = document.getElementById('batch').value.trim().split('\\n').map(l => {
    const [c, ...rest] = l.split('|');
    return {contact: c.trim(), task: rest.join('|').trim()};
  }).filter(x => x.contact);
  const out = document.getElementById('out');
  out.textContent = '提交中...';
  const r = await j('/api/batch', {items});
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
    out.textContent = `状态: ${job.status} (${job.done}/${job.total})\n` +
      JSON.stringify(job.results || [], null, 2);
    if (job.status !== 'running') return;
  }
}
fetch('/api/calib').then(r => r.json()).then(c => {
  document.getElementById('calib').textContent = c.calibrated_at
    ? `校准状态: 已校准 (${c.calibrated_at})`
    : '校准状态: 未校准! 请先运行 python -m autodial.cli calibrate';
});
</script></body></html>"""


@app.route("/")
def index():
    return PAGE


@app.route("/api/calib")
def api_calib():
    from autodial.taskfile import load_calib
    return jsonify(load_calib() or {})


@app.route("/api/dial", methods=["POST"])
def api_dial():
    from autodial.dialer import WeChatDialer, DialError
    body = request.get_json(force=True, silent=True) or {}
    contact = (body.get("contact") or "").strip()
    if not contact:
        return jsonify({"error": "contact 不能为空"}), 400
    try:
        d = WeChatDialer(dry_run=bool(body.get("dry_run")))
        info = d.dial(contact, task=body.get("task", ""), note=body.get("note", ""))
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
    if not items:
        return jsonify({"error": "items 为空"}), 400
    job_id = uuid.uuid4().hex[:8]
    with JOBS_LOCK:
        JOBS[job_id] = {"job_id": job_id, "status": "running",
                        "total": len(items), "done": 0, "results": [],
                        "created_at": time.strftime("%Y-%m-%d %H:%M:%S")}

    def run_job():
        try:
            b = BatchDialer(dry_run=bool(body.get("dry_run")))
            results = []
            for it in items:
                r = b.run([it])[0]
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
