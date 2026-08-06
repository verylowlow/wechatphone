"""Minimal Flask admin for the local knowledge base.

Routes:
  GET  /                single-page UI (upload / list / pin / delete / test query)
  POST /api/upload      multipart file upload -> ingest
  GET  /api/documents   list documents
  POST /api/pin         {doc_id, pinned} -> toggle pinned
  POST /api/delete      {doc_id} -> delete document and its chunks
  GET  /api/stats       backend stats
  POST /api/query       {query, top_k} -> test retrieval

Usage:  python kb_app.py   (default http://127.0.0.1:8765)
"""
from __future__ import annotations

import os
import sys
import tempfile
import traceback

from flask import Flask, jsonify, request, send_from_directory

# ensure project root on path so `knowledge` package resolves when run directly
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def _load_dotenv():
    """Lightweight .env loader (same as bridge.py) — must run before create_knowledge()."""
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

from knowledge import create_knowledge  # noqa: E402
from knowledge.parsers import ParseError, SUPPORTED_EXTENSIONS  # noqa: E402

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 32 * 1024 * 1024  # 32MB

kb = create_knowledge()

PAGE = """<!doctype html>
<html lang="zh"><head><meta charset="utf-8">
<title>wechatphone 知识库</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>
:root{--bg:#0f1420;--card:#1a2233;--line:#2a3550;--txt:#e8ecf4;--sub:#8b96ad;--acc:#4c8dff;--ok:#3ecf8e;--warn:#f5a623;--err:#ff5c5c}
*{box-sizing:border-box}body{margin:0;font-family:"Segoe UI","Microsoft YaHei",sans-serif;background:var(--bg);color:var(--txt);padding:24px}
h1{font-size:20px;margin:0 0 4px}.sub{color:var(--sub);font-size:13px;margin-bottom:20px}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:16px;margin-bottom:16px}
.card h2{font-size:15px;margin:0 0 12px}
.drop{border:2px dashed var(--line);border-radius:8px;padding:28px;text-align:center;color:var(--sub);cursor:pointer;transition:.15s}
.drop:hover,.drop.over{border-color:var(--acc);color:var(--txt)}
table{width:100%;border-collapse:collapse;font-size:13px}
th,td{padding:8px 10px;text-align:left;border-bottom:1px solid var(--line)}
th{color:var(--sub);font-weight:500}
.btn{border:1px solid var(--line);background:#232e45;color:var(--txt);border-radius:6px;padding:5px 12px;cursor:pointer;font-size:13px}
.btn:hover{border-color:var(--acc)}.btn.danger:hover{border-color:var(--err);color:var(--err)}
.badge{display:inline-block;padding:1px 8px;border-radius:10px;font-size:11px;background:#233252;color:var(--acc)}
.badge.pin{background:#3a2f16;color:var(--warn)}
input[type=text]{width:100%;background:#0f1420;border:1px solid var(--line);color:var(--txt);border-radius:6px;padding:8px 10px;font-size:14px}
.stats{display:flex;gap:18px;flex-wrap:wrap;color:var(--sub);font-size:13px}
.stats b{color:var(--txt)}
.msg{margin-top:10px;font-size:13px}.msg.ok{color:var(--ok)}.msg.err{color:var(--err)}
pre{background:#0f1420;border:1px solid var(--line);border-radius:6px;padding:10px;font-size:12px;white-space:pre-wrap;max-height:260px;overflow:auto}
</style></head><body>
<h1>wechatphone 知识库</h1>
<div class="sub">上传原始文件 → 解析入库 → 通话中 AI 通过检索/注入使用。支持: <span id="exts"></span></div>
<div class="card"><h2>上传统计</h2><div class="stats" id="stats"></div></div>
<div class="card"><h2>上传文件</h2>
  <div class="drop" id="drop">点击选择文件，或拖拽到此处（支持多文件）</div>
  <input type="file" id="file" multiple style="display:none">
  <div class="msg" id="upmsg"></div></div>
<div class="card"><h2>文档列表</h2><table><thead><tr>
  <th>文件名</th><th>字数</th><th>分块</th><th>置顶</th><th>入库时间</th><th>操作</th>
</tr></thead><tbody id="docs"></tbody></table></div>
<div class="card"><h2>检索测试</h2>
  <div style="display:flex;gap:8px"><input type="text" id="q" placeholder="输入问题，测试知识库召回效果…">
  <button class="btn" onclick="doQuery()">查询</button></div>
  <pre id="qres" style="display:none"></pre></div>
<script>
async function api(url, opt){const r=await fetch(url, opt);return r.json()}
function el(id){return document.getElementById(id)}
async function loadStats(){const s=await api('/api/stats');
 el('stats').innerHTML=`后端 <b>${s.backend}</b> · 向量 <b>${s.embedding}</b> · 文档 <b>${s.documents}</b> · 分块 <b>${s.chunks}</b> · 置顶 <b>${s.pinned_documents}</b>`}
async function loadDocs(){const d=await api('/api/documents');
 el('docs').innerHTML=d.map(x=>`<tr><td>${x.filename}${x.pinned?' <span class="badge pin">置顶</span>':''}</td>
  <td>${x.char_count}</td><td>${x.chunk_count}</td><td>${x.pinned?'✓':'—'}</td><td>${x.created_at}</td>
  <td><button class="btn" onclick="pin('${x.doc_id}',${!x.pinned})">${x.pinned?'取消置顶':'置顶'}</button>
  <button class="btn danger" onclick="del('${x.doc_id}')">删除</button></td></tr>`).join('')}
async function pin(id,v){await api('/api/pin',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({doc_id:id,pinned:v})});refresh()}
async function del(id){if(!confirm('确认删除该文档及其全部索引?'))return;await api('/api/delete',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({doc_id:id})});refresh()}
async function doQuery(){const q=el('q').value.trim();if(!q)return;
 const r=await api('/api/query',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({query:q,top_k:5})});
 const p=el('qres');p.style.display='block';
 p.textContent=(r.snippets||[]).map((s,i)=>`[${i+1}] (${s.score.toFixed(3)}) [${s.source}] ${s.text.slice(0,200)}…`).join('\\n\\n')||'无结果'}
async function refresh(){await loadStats();await loadDocs()}
async function upload(files){const msg=el('upmsg');msg.className='msg';msg.textContent=`上传中… ${files.length} 个文件`;
 const fd=new FormData();for(const f of files)fd.append('files',f);
 try{const r=await api('/api/upload',{method:'POST',body:fd});
  if(r.error){msg.className='msg err';msg.textContent='失败: '+r.error}
  else{msg.className='msg ok';msg.textContent='成功: '+r.ingested.map(x=>x.filename+'('+x.chunk_count+'块)').join(', ')+(r.failed.length?('；失败: '+r.failed.join(', ')):'')}
 }catch(e){msg.className='msg err';msg.textContent='上传出错: '+e}
 refresh()}
const drop=el('drop'),fi=el('file');
drop.onclick=()=>fi.click();
fi.onchange=()=>{if(fi.files.length)upload([...fi.files]);fi.value=''};
drop.ondragover=e=>{e.preventDefault();drop.classList.add('over')};
drop.ondragleave=()=>drop.classList.remove('over');
drop.ondrop=e=>{e.preventDefault();drop.classList.remove('over');if(e.dataTransfer.files.length)upload([...e.dataTransfer.files])};
el('exts').textContent='txt md html csv pdf docx';
refresh();
</script></body></html>"""


@app.get("/")
def index():
    return PAGE, 200, {"Content-Type": "text/html; charset=utf-8"}


@app.post("/api/upload")
def upload():
    files = request.files.getlist("files")
    if not files:
        return jsonify({"error": "no files"}), 400
    ingested, failed = [], []
    tmp_dir = tempfile.mkdtemp(prefix="kb_upload_")
    try:
        for f in files:
            fname = os.path.basename(f.filename or "upload.bin")
            ext = os.path.splitext(fname)[1].lower()
            if ext not in SUPPORTED_EXTENSIONS:
                failed.append(f"{fname}(不支持的类型)")
                continue
            tmp_path = os.path.join(tmp_dir, fname)
            f.save(tmp_path)
            try:
                info = kb.ingest_file(tmp_path)
                ingested.append({"doc_id": info.doc_id, "filename": info.filename,
                                   "chunk_count": info.chunk_count})
            except (ParseError, Exception) as e:  # noqa: BLE001
                failed.append(f"{fname}({e})")
            finally:
                try:
                    os.remove(tmp_path)
                except OSError:
                    pass
    finally:
        try:
            os.rmdir(tmp_dir)
        except OSError:
            pass
    return jsonify({"ingested": ingested, "failed": failed})


@app.get("/api/documents")
def documents():
    return jsonify([vars(d) for d in kb.list_documents()])


@app.post("/api/pin")
def pin():
    data = request.get_json(force=True)
    ok = False
    if hasattr(kb, "set_pinned"):
        ok = kb.set_pinned(data["doc_id"], bool(data.get("pinned", True)))
    return jsonify({"ok": ok})


@app.post("/api/delete")
def delete():
    data = request.get_json(force=True)
    return jsonify({"ok": kb.delete_document(data["doc_id"])})


@app.get("/api/stats")
def stats():
    return jsonify(kb.get_stats())


@app.post("/api/query")
def query():
    data = request.get_json(force=True)
    q = (data.get("query") or "").strip()
    top_k = int(data.get("top_k", 5))
    snippets = kb.query(q, top_k=top_k) if q else []
    return jsonify({
        "count": len(snippets),
        "snippets": [{"text": s.text, "score": s.score, "source": s.source,
                      "pinned": s.pinned} for s in snippets],
    })


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="wechatphone knowledge base admin")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--reload", action="store_true")
    args = parser.parse_args()
    print(f"[KB] admin UI: http://{args.host}:{args.port}")
    app.run(host=args.host, port=args.port, debug=args.reload)
