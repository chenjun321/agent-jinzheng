const state = {
  docId: null,
  docs: [],
  unresolvedFeedback: [],
  feedbackSummary: {
    total_qa: 0,
    unresolved: 0,
    resolved: 0,
    unmarked: 0,
  },
  sessionId: localStorage.getItem("agent_jinzheng_session_id") || crypto.randomUUID(),
  lastQaLogId: null,
};
localStorage.setItem("agent_jinzheng_session_id", state.sessionId);

const defaultQuestions = [
  "这个标准适用于什么范围？",
  "键的技术要求有哪些？",
  "表格中键的尺寸或公差是如何规定的？",
  "文档中对检验或验收有什么要求？",
  "这份标准是否规定了汽车发动机维修流程？",
];

function setStatus(text) {
  document.getElementById("status").textContent = text;
}

async function api(path, options = {}) {
  const res = await fetch(path, options);
  if (!res.ok) {
    const detail = await res.text();
    throw new Error(detail);
  }
  return res.json();
}

async function loadDocuments() {
  const data = await api("/api/documents");
  state.docs = data.documents;
  const box = document.getElementById("documents");
  box.innerHTML = "";
  data.documents.forEach((doc) => {
    const item = document.createElement("div");
    item.className = `doc-item ${doc.id === state.docId ? "active" : ""}`;
    item.innerHTML = `
      <div class="doc-title">
        <strong>${doc.file_name}</strong>
        <button class="icon-btn" title="删除文档" data-delete="${doc.id}">删除</button>
      </div>
      <span>${doc.pdf_type} · ${doc.page_count} 页 · ${doc.parse_status}</span><br>
      <span>${doc.chunk_count || 0} chunks · ${doc.table_count || 0} tables</span>
    `;
    item.addEventListener("click", () => selectDocument(doc.id));
    item.querySelector("[data-delete]").addEventListener("click", (event) => {
      event.stopPropagation();
      deleteDocument(doc.id, doc.file_name);
    });
    box.appendChild(item);
  });
}

async function loadUnresolvedFeedback() {
  const data = await api("/api/feedback/unresolved?limit=20");
  state.feedbackSummary = data.summary || state.feedbackSummary;
  state.unresolvedFeedback = data.items || [];
  renderUnresolvedFeedback();
}

function renderUnresolvedFeedback() {
  const summary = document.getElementById("feedbackSummary");
  const box = document.getElementById("unresolvedFeedback");
  const total = Number(state.feedbackSummary.total_qa || 0);
  const unresolved = Number(state.feedbackSummary.unresolved || 0);
  const resolved = Number(state.feedbackSummary.resolved || 0);
  const unmarked = Number(state.feedbackSummary.unmarked || 0);
  summary.innerHTML = `
    <span>QA 总数 ${total}</span>
    <span>未解决 ${unresolved}</span>
    <span>已解决 ${resolved}</span>
    <span>未标记 ${unmarked}</span>
  `;
  if (!state.unresolvedFeedback.length) {
    box.innerHTML = "";
    return;
  }
  box.innerHTML = state.unresolvedFeedback
    .map((item) => {
      const reason = item.self_check?.reason || "未返回原因";
      const answer = item.answer || "";
      const shortAnswer = answer.length > 120 ? `${answer.slice(0, 120)}...` : answer;
      return `
        <article class="feedback-item">
          <div class="feedback-item-head">
            <strong>${escapeHtml(item.file_name || item.doc_id || "未知文档")}</strong>
            <span>${formatDateTime(item.created_at)}</span>
          </div>
          <p class="feedback-question">${escapeHtml(item.question || "")}</p>
          <p class="feedback-reason">${escapeHtml(reason)}</p>
          <details>
            <summary>查看回答摘要</summary>
            <p>${escapeHtml(shortAnswer)}</p>
          </details>
          <button class="secondary feedback-detail-btn" data-qa-log-id="${escapeHtml(item.id)}">查看详情</button>
        </article>
      `;
    })
    .join("");
  box.querySelectorAll("[data-qa-log-id]").forEach((btn) => {
    btn.addEventListener("click", () => openQaLogDetail(btn.dataset.qaLogId));
  });
}

async function openQaLogDetail(qaLogId) {
  setStatus("Loading");
  const item = await api(`/api/qa-logs/${qaLogId}`);
  renderQaLogDetail(item);
  setStatus("Ready");
}

function renderQaLogDetail(item) {
  const box = document.getElementById("qaLogDetail");
  const check = formatSelfCheck(item.self_check || {});
  box.innerHTML = `
    <div class="qa-detail-head">
      <strong>${escapeHtml(item.file_name || item.doc_id || "未知文档")}</strong>
      <button class="icon-btn" type="button" data-close-detail>关闭</button>
    </div>
    <dl class="qa-detail-meta">
      <div><dt>时间</dt><dd>${escapeHtml(formatDateTime(item.created_at))}</dd></div>
      <div><dt>反馈</dt><dd>${escapeHtml(item.feedback || "未标记")}</dd></div>
      <div><dt>判断</dt><dd>${escapeHtml(check.label)}</dd></div>
      <div><dt>风险</dt><dd>${escapeHtml(check.risk)}</dd></div>
    </dl>
    <section>
      <h3>问题</h3>
      <p>${escapeHtml(item.question || "")}</p>
    </section>
    <section>
      <h3>回答</h3>
      <p class="answer-text">${escapeHtml(item.answer || "")}</p>
    </section>
    <section>
      <h3>自检原因</h3>
      <p>${escapeHtml(check.reason)}</p>
    </section>
    ${renderEvidenceList(item.citations || [], "回溯引用")}
  `;
  box.hidden = false;
  box.querySelector("[data-close-detail]").addEventListener("click", () => {
    box.hidden = true;
  });
}

async function selectDocument(docId) {
  state.docId = docId;
  setStatus("Loading");
  const data = await api(`/api/documents/${docId}`);
  renderDocument(data);
  await loadDocuments();
  setStatus("Ready");
}

function renderDocument(data) {
  const doc = data.document;
  document.getElementById("docMeta").textContent = `${doc.file_name} | ${doc.pdf_type} | ${doc.page_count} 页 | ${doc.parse_status}`;
  if (data.parse_report?.self_check?.requires_review) {
    document.getElementById("docMeta").textContent += ` | 建议复核：${data.parse_report.self_check.issues.join("、")}`;
  }
  const text = data.pages
    .slice(0, 3)
    .map((page) => `第 ${page.page_no} 页\n${page.text || ""}`)
    .join("\n\n---\n\n");
  document.getElementById("pagePreview").textContent = text || "暂无正文";

  const tableBox = document.getElementById("tablePreview");
  tableBox.innerHTML = "";
  if (!data.tables.length) {
    tableBox.textContent = "暂无表格候选结果";
  }
  data.tables.forEach((table) => {
    const card = document.createElement("div");
    card.className = "table-card";
    card.innerHTML = `
      <strong>第 ${table.page_no} 页：${escapeHtml(table.title || "表格")}</strong>
      <div class="table-html">${table.html || ""}</div>
      <details>
        <summary>查看 Markdown</summary>
        <pre>${escapeHtml(table.markdown || "")}</pre>
      </details>
    `;
    tableBox.appendChild(card);
  });
}

async function uploadAndParse(event) {
  event.preventDefault();
  const file = document.getElementById("pdfFile").files[0];
  if (!file) {
    alert("请选择 PDF");
    return;
  }
  setStatus("Parsing");
  const form = new FormData();
  form.append("file", file);
  const data = await api("/api/upload", { method: "POST", body: form });
  state.docId = data.document.id;
  await loadDocuments();
  await selectDocument(state.docId);
  setStatus("Ready");
}

async function askQuestion(question) {
  if (!state.docId) {
    alert("请先上传或选择文档");
    return;
  }
  const q = question || document.getElementById("question").value.trim();
  if (!q) {
    alert("请输入问题");
    return;
  }
  document.getElementById("question").value = q;
  setStatus("Asking");
  const data = await api("/api/ask", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ doc_id: state.docId, question: q, session_id: state.sessionId }),
  });
  state.sessionId = data.session_id || state.sessionId;
  localStorage.setItem("agent_jinzheng_session_id", state.sessionId);
  state.lastQaLogId = data.qa_log_id;
  renderAnswer(data);
  setStatus("Ready");
}

function renderAnswer(data) {
  const box = document.getElementById("answer");
  const check = formatSelfCheck(data.self_check || {});
  const citations = renderEvidenceList(data.citations || [], "引用依据");
  const nearest = !data.citations?.length && data.unanswered?.nearest_evidence?.length
    ? renderEvidenceList(data.unanswered.nearest_evidence, "最接近的检索结果")
    : "";
  const feedback = data.qa_log_id
    ? `<div class="feedback"><span>这次回答解决问题了吗？</span><button data-feedback="resolved">已解决</button><button class="secondary" data-feedback="unresolved">未解决</button></div>`
    : "";
  box.innerHTML = `
    <div class="answer-block ${check.className}">
      <div class="answer-status">
        <span class="status-pill">${check.label}</span>
        <span class="status-reason">${escapeHtml(check.reason)}</span>
      </div>
      <div class="answer-section">
        <strong>回答内容</strong>
        <p class="answer-text">${escapeHtml(data.answer)}</p>
      </div>
      <dl class="check-grid">
        <div>
          <dt>判断</dt>
          <dd>${check.answerable}</dd>
        </div>
        <div>
          <dt>风险</dt>
          <dd>${check.risk}</dd>
        </div>
        <div>
          <dt>依据</dt>
          <dd>${check.grounded}</dd>
        </div>
      </dl>
    </div>
    ${citations || nearest || '<div class="citation empty">未返回可引用证据</div>'}
    ${feedback}
  `;
  box.querySelectorAll("[data-feedback]").forEach((btn) => {
    btn.addEventListener("click", () => submitFeedback(btn.dataset.feedback));
  });
}

function formatSelfCheck(selfCheck) {
  const actionMap = {
    answer: {
      label: "可以回答",
      answerable: "是",
      className: "answer-ok",
    },
    refuse: {
      label: "不能回答",
      answerable: "否",
      className: "answer-refuse",
    },
    partial: {
      label: "部分回答",
      answerable: "部分",
      className: "answer-partial",
    },
  };
  const riskMap = {
    low: "低",
    medium: "中",
    high: "高",
  };
  const view = actionMap[selfCheck.action] || {
    label: selfCheck.action || "未知",
    answerable: "待确认",
    className: "answer-partial",
  };
  return {
    ...view,
    risk: riskMap[selfCheck.risk] || selfCheck.risk || "未知",
    grounded: selfCheck.grounded ? "有文档依据" : "依据不足",
    reason: selfCheck.reason || "未返回自检原因",
  };
}

function renderEvidenceList(items, title) {
  if (!items.length) return "";
  const evidenceItems = items
    .map((item, index) => {
      const score = formatMetric(item.score);
      const confidence = formatMetric(item.confidence);
      const rerank = formatMetric(item.rerank_score);
      return `
        <article class="citation">
          <div class="citation-head">
            <strong>来源 ${index + 1}：第 ${item.page_start}-${item.page_end} 页</strong>
            <span>${item.chunk_id ? escapeHtml(item.chunk_id) : "文档片段"}</span>
          </div>
          <p>${escapeHtml(item.snippet || "")}</p>
          <details>
            <summary>查看检索指标</summary>
            <div class="metric-row">
              <span>相关性 ${score}</span>
              <span>证据置信度 ${confidence}</span>
              <span>重排分 ${rerank}</span>
            </div>
          </details>
        </article>
      `;
    })
    .join("");
  return `<section class="evidence-list"><h3>${title}</h3>${evidenceItems}</section>`;
}

function formatMetric(value) {
  if (value === null || value === undefined || value === "") return "-";
  const number = Number(value);
  if (!Number.isFinite(number)) return String(value);
  return number.toFixed(3);
}

async function deleteDocument(docId, fileName) {
  if (!confirm(`确定删除 ${fileName}？这会同时删除本地解析结果和向量索引。`)) {
    return;
  }
  setStatus("Deleting");
  await api(`/api/documents/${docId}`, { method: "DELETE" });
  if (state.docId === docId) {
    state.docId = null;
    document.getElementById("docMeta").textContent = "尚未选择文档";
    document.getElementById("pagePreview").textContent = "";
    document.getElementById("tablePreview").textContent = "";
  }
  await loadDocuments();
  setStatus("Ready");
}

async function submitFeedback(value) {
  if (!state.lastQaLogId) return;
  await api("/api/feedback", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ qa_log_id: state.lastQaLogId, feedback: value }),
  });
  setStatus(value === "resolved" ? "Resolved" : "Marked");
  await loadUnresolvedFeedback();
}

function formatDateTime(value) {
  if (!value) return "";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function escapeHtml(text) {
  return String(text)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

function renderQuickQuestions() {
  const box = document.getElementById("quickQuestions");
  defaultQuestions.forEach((question) => {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.textContent = question;
    btn.addEventListener("click", () => askQuestion(question));
    box.appendChild(btn);
  });
}

document.getElementById("uploadForm").addEventListener("submit", uploadAndParse);
document.getElementById("loadDocs").addEventListener("click", loadDocuments);
document.getElementById("loadFeedback").addEventListener("click", loadUnresolvedFeedback);
document.getElementById("askBtn").addEventListener("click", () => askQuestion());
renderQuickQuestions();
loadDocuments().catch(console.error);
loadUnresolvedFeedback().catch(console.error);
