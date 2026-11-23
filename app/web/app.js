// SSE 前端：按帧解析，token 追加，citation 收集，done 收尾。
// 内容一律先转义再插入，避免模型输出里的 HTML 被执行。

const $ = (id) => document.getElementById(id);
let controller = null;
let sessionId = null;

const STATUS_LABEL = {
  credit: "学分/绩点", course: "选课", material: "办事材料",
  window: "办理窗口", general: "规定查询", out_of_scope: "范围外",
};

function esc(s) {
  return String(s).replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  })[c]);
}

function renderBody(text) {
  // 先转义，再把 [1] [2] 标记渲染成上标
  return esc(text).replace(/\[(\d+)\]/g, "<sup>[$1]</sup>");
}

function flow(line) {
  const box = $("flow");
  box.hidden = false;
  box.textContent += (box.textContent ? "\n" : "") + line;
}

function reset() {
  $("flow").hidden = true;
  $("flow").textContent = "";
  $("answer-box").hidden = true;
  $("answer-body").textContent = "";
  $("answer-meta").textContent = "";
  $("answer-tag").className = "tag";
  $("citations").hidden = true;
  $("citation-list").innerHTML = "";
  $("guidance").hidden = true;
  $("guidance").textContent = "";
  $("hint").textContent = "";
}

async function loadHealth() {
  try {
    const r = await fetch("/api/health");
    const d = await r.json();
    const llm = Object.entries(d.llm).filter(([, v]) => v).map(([k]) => k).join(" / ") || "未配置";
    $("kb-status").textContent =
      `状态 ${d.status} ｜ 文件 ${d.db.docs} 份（现行 ${d.db.docs_current}）｜ 知识块 ${d.db.chunks} ｜ 模型 ${llm}`;
  } catch {
    $("kb-status").textContent = "知识库状态获取失败";
  }
}

function handleEvent(event, data, ui) {
  switch (event) {
    case "route":
      flow(`意图：${STATUS_LABEL[data.qtype] || data.qtype}（${data.by}，置信度 ${data.confidence}）`
        + (data.file_types?.length ? `，检索范围：${data.file_types.join("、")}` : ""));
      break;
    case "retrieval":
      flow(`召回：${data.recalled} 个候选块，最高相似度 ${data.top_score}`
        + (data.docs?.length ? `，涉及《${data.docs.join("》《")}》` : ""));
      break;
    case "rerank":
      flow(`重排：保留 ${data.kept} 块，剔除 ${data.dropped?.length || 0} 块，向量前 ${data.forced_keep} 名强制入围`);
      break;
    case "tool_call":
      flow(`调用工具：${data.name}`);
      break;
    case "tool_result":
      flow(`工具返回：${data.status}（${data.latency_ms} ms）`);
      break;
    case "token":
      $("answer-box").hidden = false;
      ui.answer += data.text;
      $("answer-body").innerHTML = renderBody(ui.answer);
      break;
    case "citation":
      $("citations").hidden = false;
      ui.citations.push(data);
      $("citation-list").innerHTML = ui.citations
        .map((c) => `<li><strong>《${esc(c.doc_title)}》${esc(c.article_no)}</strong>`
          + `（${c.effective_year} 年生效）</li>`)
        .join("");
      break;
    case "done":
      finish(data);
      break;
    case "error":
      $("hint").textContent = `出错：${data.message}`;
      break;
  }
}

function finish(d) {
  const box = $("answer-box");
  box.hidden = false;
  const tag = $("answer-tag");
  if (d.refused) {
    tag.className = "tag refused";
    tag.textContent = "未查到明确规定";
  } else {
    tag.className = "tag";
    tag.textContent = "已依据条款回答";
  }
  $("answer-meta").textContent =
    `耗时 ${(d.elapsed_ms / 1000).toFixed(1)}s ｜ 知识库版本 ${d.knowledge_version}`;

  if (d.guidance) {
    const g = $("guidance");
    g.hidden = false;
    const lines = [];
    for (const w of d.guidance.windows || []) {
      lines.push(`${w.name}｜${w.location}｜${w.open_hours}${w.phone ? "｜" + w.phone : ""}`);
    }
    if (d.guidance.note) lines.push(d.guidance.note);
    g.textContent = lines.join("\n");
  }
  sessionId = d.session_id || sessionId;
}

async function ask() {
  const q = $("question").value.trim();
  if (!q) return;

  reset();
  $("send").disabled = true;
  $("cancel").hidden = false;

  controller = new AbortController();
  const ui = { answer: "", citations: [] };

  try {
    const resp = await fetch("/api/chat/stream", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ question: q, session_id: sessionId }),
      signal: controller.signal,
    });

    if (!resp.ok) {
      const text = await resp.text();
      $("hint").textContent = `请求失败 ${resp.status}：${text.slice(0, 120)}`;
      return;
    }

    const reader = resp.body.getReader();
    const decoder = new TextDecoder();
    let buf = "";

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buf += decoder.decode(value, { stream: true });

      let idx;
      while ((idx = buf.indexOf("\n\n")) >= 0) {
        const frame = buf.slice(0, idx);
        buf = buf.slice(idx + 2);

        let event = "message";
        const dataLines = [];
        for (const line of frame.split("\n")) {
          if (line.startsWith("event:")) event = line.slice(6).trim();
          else if (line.startsWith("data:")) dataLines.push(line.slice(5).trim());
        }
        if (!dataLines.length) continue;
        try {
          handleEvent(event, JSON.parse(dataLines.join("")), ui);
        } catch (e) {
          console.warn("事件解析失败", event, e);
        }
      }
    }
  } catch (e) {
    if (e.name !== "AbortError") $("hint").textContent = `连接中断：${e.message}`;
    else $("hint").textContent = "已停止";
  } finally {
    $("send").disabled = false;
    $("cancel").hidden = true;
    controller = null;
  }
}

$("send").addEventListener("click", ask);
$("cancel").addEventListener("click", () => controller?.abort());
$("question").addEventListener("keydown", (e) => {
  if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) ask();
});
document.querySelectorAll(".chip").forEach((c) => {
  c.addEventListener("click", () => {
    $("question").value = c.dataset.q;
    ask();
  });
});

loadHealth();
