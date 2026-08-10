# -*- coding: utf-8 -*-
"""wechatphone call records web UI.

Usage:  python calllog_app.py [--port 8766]
Pages:
  /                通话列表 (时间 / 时长 / 轮次 / 摘要)
  /call/<call_id>  通话详情 (对话时间线: 对方说 / AI说 / 工具调用)
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def _load_dotenv():
    env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    if not os.path.exists(env_path):
        return
    with open(env_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())


_load_dotenv()

from flask import Flask, jsonify, request  # noqa: E402

from calllog.store import CallStore  # noqa: E402

store = CallStore()
app = Flask(__name__)

PAGE_CSS = """
<style>
:root { color-scheme: dark; }
body { background:#111418; color:#e6e6e6; font-family:"Segoe UI","Microsoft YaHei",sans-serif;
       margin:0; padding:24px; max-width:860px; margin-inline:auto; }
h1 { font-size:20px; margin:0 0 16px; }
h1 a { color:#e6e6e6; text-decoration:none; }
.muted { color:#8a919b; font-size:13px; }
table { width:100%; border-collapse:collapse; }
th, td { text-align:left; padding:9px 10px; border-bottom:1px solid #2a2f36; font-size:14px; }
th { color:#8a919b; font-weight:600; }
tr:hover td { background:#181c22; }
a { color:#6fb7ff; text-decoration:none; }
a:hover { text-decoration:underline; }
.badge { display:inline-block; padding:2px 9px; border-radius:10px; font-size:12px; margin-right:6px; }
.badge.remote { background:#1d3b57; color:#8ecbff; }
.badge.ai { background:#2d4a2f; color:#9fe0a4; }
.badge.tool { background:#4a3a1d; color:#ecc37f; }
.badge.note { background:#333; color:#aaa; }
.bubble { padding:9px 13px; border-radius:10px; margin:7px 0; max-width:80%; font-size:14px;
          line-height:1.55; word-break:break-all; }
.bubble.remote { background:#1d3b57; }
.bubble.ai { background:#22301f; margin-left:auto; }
.bubble.note { background:#262626; font-size:12px; color:#999; max-width:60%; }
.toolbox { background:#26221a; border-left:3px solid #ecc37f; padding:8px 12px; margin:7px 0;
           font-size:13px; }
.toolbox code { color:#ecc37f; }
.stats { display:flex; gap:18px; margin-bottom:18px; }
.stat { background:#181c22; border-radius:10px; padding:12px 18px; }
.stat .n { font-size:22px; font-weight:700; }
pre { white-space:pre-wrap; word-break:break-all; margin:4px 0 0; color:#c9c9c9; font-size:12px; }
</style>"""


def fmt_duration(sec, ended=True):
    if not ended:
        return "进行中"
    sec = int(sec or 0)
    m, s = divmod(sec, 60)
    h, m = divmod(m, 60)
    return f"{h}h{m}m{s}s" if h else f"{m}m{s}s"


@app.route("/")
def index():
    calls = store.list_calls(limit=200)
    total = len(calls)
    done = sum(1 for c in calls if c["ended_at"])
    total_events = sum(c["event_count"] for c in calls)
    rows = []
    for c in calls:
        rows.append(f"""
        <tr>
          <td><a href="/call/{c['call_id']}">{c['call_id']}</a></td>
          <td>{c.get('app') or '-'}</td>
          <td>{c.get('contact') or '-'}</td>
          <td>{c['started_at']}</td>
          <td>{fmt_duration(c['duration_sec'], ended=bool(c['ended_at']))}</td>
          <td>{c['event_count']}</td>
          <td class="muted">{c['summary'] or ''}</td>
        </tr>""")
    return f"""<!doctype html><html><head><meta charset="utf-8">
<title>通话记录 - wechatphone</title>{PAGE_CSS}</head><body>
<h1><a href="/">通话记录</a></h1>
<div class="stats">
  <div class="stat"><div class="n">{total}</div><div class="muted">通话总数</div></div>
  <div class="stat"><div class="n">{done}</div><div class="muted">已结束</div></div>
  <div class="stat"><div class="n">{total_events}</div><div class="muted">事件总数</div></div>
</div>
<table><thead><tr><th>通话ID</th><th>应用</th><th>联系人</th><th>开始时间</th><th>时长</th><th>事件数</th><th>摘要</th></tr></thead>
<tbody>{''.join(rows) or '<tr><td colspan="7" class="muted">暂无通话记录。启动 bridge.py 打电话后, 记录会自动出现。</td></tr>'}</tbody></table>
</body></html>"""


@app.route("/call/<call_id>")
def call_detail(call_id):
    call = store.get_call(call_id)
    if not call:
        return "通话不存在", 404
    events = store.get_events(call_id)
    items = []
    for e in events:
        if e["kind"] == "remote":
            items.append(f'<div class="bubble remote"><span class="badge remote">对方</span> '
                         f'<span class="muted">{e["ts"][11:]}</span><br>{e["text"]}</div>')
        elif e["kind"] == "ai":
            items.append(f'<div class="bubble ai"><span class="badge ai">AI</span> '
                         f'<span class="muted">{e["ts"][11:]}</span><br>{e["text"]}</div>')
        elif e["kind"] == "tool":
            extra = e.get("extra") or {}
            args = extra.get("arguments", "")
            try:
                args = json.dumps(json.loads(args), ensure_ascii=False)
            except Exception:
                pass
            cnt = extra.get("result_count")
            cnt_txt = f" → 返回 {cnt} 条" if cnt is not None else ""
            items.append(f'<div class="toolbox"><span class="badge tool">工具</span> '
                         f'<code>{extra.get("name","")}</code> {args}{cnt_txt}'
                         f'<span class="muted"> {e["ts"][11:]}</span></div>')
        else:
            items.append(f'<div class="bubble note">{e["text"]} <span class="muted">{e["ts"][11:]}</span></div>')
    return f"""<!doctype html><html><head><meta charset="utf-8">
<title>通话 {call_id} - wechatphone</title>{PAGE_CSS}</head><body>
<h1><a href="/">通话记录</a> / {call_id}</h1>
<div class="muted" style="margin-bottom:16px">应用 {call.get('app') or '-'} · 联系人 {call.get('contact') or '-'} ·
开始 {call['started_at']} · 结束 {call['ended_at'] or '进行中'} ·
时长 {fmt_duration(call['duration_sec'], ended=bool(call['ended_at']))} · 摘要: {call['summary'] or '-'}</div>
{''.join(items) or '<div class="muted">该通话暂无事件。</div>'}
</body></html>"""


# ---------- JSON API ----------

@app.route("/api/calls")
def api_calls():
    return jsonify(store.list_calls(limit=int(request.args.get("limit", 200))))


@app.route("/api/calls/<call_id>")
def api_call(call_id):
    call = store.get_call(call_id)
    if not call:
        return jsonify({"error": "not found"}), 404
    call["events"] = store.get_events(call_id)
    return jsonify(call)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8766)
    ap.add_argument("--host", default="127.0.0.1")
    args = ap.parse_args()
    print(f"[CALLLOG] web UI: http://{args.host}:{args.port}", flush=True)
    app.run(host=args.host, port=args.port, debug=False)


if __name__ == "__main__":
    main()
