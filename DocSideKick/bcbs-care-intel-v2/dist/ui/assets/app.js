const state = {
  page: "chat",
  messages: [],
  isLoading: false,
  latestDebug: null,
  insightsCache: null,
  expandedSourcesByMessage: {}
};

const contentEl = document.getElementById("content");

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

function createElementFromHtml(html) {
  const template = document.createElement("template");
  template.innerHTML = html.trim();
  return template.content.firstElementChild;
}

function setActiveNav(page) {
  document.querySelectorAll(".nav-item").forEach((item) => {
    if (item.dataset.page === page) item.classList.add("is-active");
    else item.classList.remove("is-active");
  });
}

function render() {
  setActiveNav(state.page);
  if (state.page === "chat") renderChatPage();
  else if (state.page === "insights") renderInsightsPage("Member Insights");
  else renderInsightsPage("Population Trends");
}

function renderChatPage() {
  contentEl.innerHTML = "";
  const shell = createElementFromHtml(`
    <section class="chat-layout">
      <section class="chat-main">
        <div id="chat-stream" class="chat-stream"></div>
        <form id="chat-composer" class="composer">
          <textarea id="chat-input" placeholder="Which diabetic members are missing A1C tests and why?"></textarea>
          <div class="composer-actions">
            <p class="composer-hint">Supervisor-first mode: responses come from HLS_Payer_Supervisor.</p>
            <button id="send-btn" class="btn-primary" type="submit">Send</button>
          </div>
        </form>
      </section>
      <aside class="context-column">
        <div class="context-card">
          <h3>Context</h3>
          <p>Supervisor: HLS_Payer_Supervisor</p>
          <p>Endpoint: mas-30532bb0-endpoint</p>
          <p>Structured + unstructured routing handled by Supervisor</p>
        </div>
        <div class="context-card">
          <h3>Latest Query Metadata</h3>
          <p id="meta-endpoint">Endpoint: not resolved</p>
          <p id="meta-path">Path: n/a</p>
          <p id="meta-latency">Latency: n/a</p>
          <p id="meta-tool-calls">Tool calls: n/a</p>
          <p id="meta-request-id">Request ID: n/a</p>
          <p id="meta-error">Last error: none</p>
        </div>
      </aside>
    </section>
  `);
  contentEl.appendChild(shell);

  const form = document.getElementById("chat-composer");
  const input = document.getElementById("chat-input");
  const sendButton = document.getElementById("send-btn");

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const message = input.value.trim();
    if (!message || state.isLoading) return;
    input.value = "";
    await submitChat(message);
  });

  if (!state.messages.length) {
    input.value = "Which diabetic members are missing A1C tests and why?";
  }
  if (state.isLoading) {
    sendButton.disabled = true;
    sendButton.textContent = "Analyzing...";
  }

  renderChatStream();
  renderMetadata();
}

function renderChatStream() {
  const stream = document.getElementById("chat-stream");
  if (!stream) return;
  stream.innerHTML = "";

  if (!state.messages.length) {
    stream.appendChild(
      createElementFromHtml(`
        <div class="empty-state">
          <h3>Unified AI Copilot for Healthcare Data</h3>
          <p>Ask cohort, cost, gap-in-care, and barrier questions. You will get a narrative first, then table and chart when available.</p>
        </div>
      `)
    );
    return;
  }

  state.messages.forEach((message, index) => {
    const article = createElementFromHtml(
      `<article class="msg ${message.role}"><header class="msg-header">${message.role === "user" ? "You" : "BCBS Care Intelligence"}</header></article>`
    );

    if (!(message.role === "assistant" && message.response)) {
      article.appendChild(createElementFromHtml(`<p>${escapeHtml(message.content || "")}</p>`));
    }

    if (message.loading) {
      article.appendChild(createElementFromHtml(`<p class="loading">Analyzing data...</p>`));
    }

    if (message.response) {
      article.appendChild(renderResponseBody(message.response, index));
    }

    stream.appendChild(article);
  });
  stream.scrollTop = stream.scrollHeight;
}

function renderResponseBody(response, index) {
  const grid = createElementFromHtml(`<div class="msg-grid"></div>`);
  const main = createElementFromHtml(`<div class="main-stack"></div>`);
  const side = createElementFromHtml(`<aside class="panel"><h4>Sources</h4></aside>`);
  const sourceList = createElementFromHtml(`<ul class="sources"></ul>`);
  side.appendChild(sourceList);

  const textPanel = createElementFromHtml(`<section class="panel"></section>`);
  const paragraphs = String(response.response_text || "")
    .split("\n")
    .map((line) => line.trim())
    .filter(Boolean);
  if (!paragraphs.length) paragraphs.push("No narrative response returned.");
  paragraphs.forEach((paragraph) => {
    textPanel.appendChild(createElementFromHtml(`<p>${escapeHtml(paragraph)}</p>`));
  });
  main.appendChild(textPanel);

  if (Array.isArray(response.data_table) && response.data_table.length) {
    const tablePanel = createElementFromHtml(`<section class="panel"><h4>Structured Output</h4></section>`);
    tablePanel.appendChild(renderSortableTable(response.data_table));
    main.appendChild(tablePanel);
  }

  if (response.chart_spec && Array.isArray(response.chart_spec.data)) {
    const chartPanel = createElementFromHtml(`<section class="panel"><h4>${escapeHtml(response.chart_spec.title || "Visualization")}</h4></section>`);
    chartPanel.appendChild(renderChart(response.chart_spec));
    main.appendChild(chartPanel);
  }

  const sources = Array.isArray(response.sources) ? response.sources : [];
  const expanded = Boolean(state.expandedSourcesByMessage[index]);
  const visibleSources = expanded ? sources : sources.slice(0, 8);
  if (!visibleSources.length) {
    sourceList.innerHTML = "<li>No citations returned.</li>";
  } else {
    sourceList.innerHTML = visibleSources.map((source) => `<li>${escapeHtml(source)}</li>`).join("");
  }
  if (sources.length > 8) {
    const toggle = createElementFromHtml(
      `<button class="btn-primary" type="button">${expanded ? "Show less" : `Show more (${sources.length - 8})`}</button>`
    );
    toggle.style.marginTop = "8px";
    toggle.addEventListener("click", () => {
      state.expandedSourcesByMessage[index] = !expanded;
      renderChatStream();
    });
    side.appendChild(toggle);
  }

  grid.appendChild(main);
  grid.appendChild(side);
  return grid;
}

function renderSortableTable(rows) {
  const columns = Object.keys(rows[0] || {});
  let sortKey = null;
  let direction = "asc";
  const wrap = createElementFromHtml(`<div class="table-wrap"></div>`);
  const table = createElementFromHtml(`<table><thead><tr></tr></thead><tbody></tbody></table>`);
  const headRow = table.querySelector("thead tr");
  const body = table.querySelector("tbody");

  function comparator(a, b) {
    if (typeof a === "number" && typeof b === "number") {
      return direction === "asc" ? a - b : b - a;
    }
    return direction === "asc"
      ? String(a ?? "").localeCompare(String(b ?? ""))
      : String(b ?? "").localeCompare(String(a ?? ""));
  }

  function paintRows() {
    const sourceRows = sortKey ? [...rows].sort((left, right) => comparator(left[sortKey], right[sortKey])) : rows;
    body.innerHTML = sourceRows
      .map((row) => `<tr>${columns.map((column) => `<td>${escapeHtml(row[column])}</td>`).join("")}</tr>`)
      .join("");
  }

  columns.forEach((column) => {
    const th = createElementFromHtml(`<th><button type="button">${escapeHtml(column)}</button></th>`);
    th.querySelector("button").addEventListener("click", () => {
      if (sortKey === column) direction = direction === "asc" ? "desc" : "asc";
      else {
        sortKey = column;
        direction = "asc";
      }
      paintRows();
    });
    headRow.appendChild(th);
  });

  paintRows();
  wrap.appendChild(table);
  return wrap;
}

function renderChart(spec) {
  const container = createElementFromHtml(`<div class="chart"></div>`);
  if (!spec || !Array.isArray(spec.data) || !spec.data.length) return container;
  const width = 780;
  const height = 300;
  const padding = 38;
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("viewBox", `0 0 ${width} ${height}`);
  svg.setAttribute("width", "100%");
  svg.setAttribute("height", "100%");
  svg.style.background = "#fff";

  const xKey = spec.x;
  const yKey = spec.y;
  const data = spec.data;

  if (spec.type === "bar") {
    const maxY = Math.max(...data.map((row) => Number(row[yKey] || 0)), 1);
    const barWidth = (width - padding * 2) / data.length;
    data.forEach((row, index) => {
      const value = Number(row[yKey] || 0);
      const h = ((height - padding * 2) * value) / maxY;
      const x = padding + index * barWidth + 6;
      const y = height - padding - h;

      const rect = document.createElementNS("http://www.w3.org/2000/svg", "rect");
      rect.setAttribute("x", String(x));
      rect.setAttribute("y", String(y));
      rect.setAttribute("width", String(Math.max(barWidth - 10, 4)));
      rect.setAttribute("height", String(h));
      rect.setAttribute("fill", "#005EB8");
      rect.setAttribute("rx", "6");
      svg.appendChild(rect);

      const label = document.createElementNS("http://www.w3.org/2000/svg", "text");
      label.setAttribute("x", String(x + (barWidth - 10) / 2));
      label.setAttribute("y", String(height - 12));
      label.setAttribute("text-anchor", "middle");
      label.setAttribute("font-size", "11");
      label.setAttribute("fill", "#3c5678");
      label.textContent = String(row[xKey] ?? "");
      svg.appendChild(label);
    });
  } else if (spec.type === "line") {
    const maxY = Math.max(...data.map((row) => Number(row[yKey] || 0)), 1);
    const stepX = (width - padding * 2) / Math.max(data.length - 1, 1);
    const points = data.map((row, index) => {
      const value = Number(row[yKey] || 0);
      const x = padding + index * stepX;
      const y = height - padding - ((height - padding * 2) * value) / maxY;
      return {x, y};
    });
    const polyline = document.createElementNS("http://www.w3.org/2000/svg", "polyline");
    polyline.setAttribute("points", points.map((point) => `${point.x},${point.y}`).join(" "));
    polyline.setAttribute("fill", "none");
    polyline.setAttribute("stroke", "#003A8F");
    polyline.setAttribute("stroke-width", "3");
    svg.appendChild(polyline);

    points.forEach((point) => {
      const dot = document.createElementNS("http://www.w3.org/2000/svg", "circle");
      dot.setAttribute("cx", String(point.x));
      dot.setAttribute("cy", String(point.y));
      dot.setAttribute("r", "3");
      dot.setAttribute("fill", "#003A8F");
      svg.appendChild(dot);
    });
  } else {
    const total = data.reduce((sum, row) => sum + Number(row[yKey] || 0), 0) || 1;
    const cx = width / 2;
    const cy = height / 2;
    const radius = 90;
    const colors = ["#003A8F", "#005EB8", "#3D88CF", "#79ACE0", "#A7C8EB", "#D1E3F7"];
    let angle = -Math.PI / 2;
    data.forEach((row, index) => {
      const value = Number(row[yKey] || 0);
      const arc = (value / total) * Math.PI * 2;
      const x1 = cx + radius * Math.cos(angle);
      const y1 = cy + radius * Math.sin(angle);
      const x2 = cx + radius * Math.cos(angle + arc);
      const y2 = cy + radius * Math.sin(angle + arc);
      const largeArc = arc > Math.PI ? 1 : 0;
      const path = document.createElementNS("http://www.w3.org/2000/svg", "path");
      path.setAttribute("d", `M ${cx} ${cy} L ${x1} ${y1} A ${radius} ${radius} 0 ${largeArc} 1 ${x2} ${y2} Z`);
      path.setAttribute("fill", colors[index % colors.length]);
      svg.appendChild(path);
      angle += arc;
    });
  }

  container.appendChild(svg);
  return container;
}

function renderMetadata() {
  const endpointEl = document.getElementById("meta-endpoint");
  const pathEl = document.getElementById("meta-path");
  const latencyEl = document.getElementById("meta-latency");
  const toolCallsEl = document.getElementById("meta-tool-calls");
  const requestIdEl = document.getElementById("meta-request-id");
  const errorEl = document.getElementById("meta-error");
  const debug = state.latestDebug || {};
  if (endpointEl) endpointEl.textContent = `Endpoint: ${debug.supervisor_endpoint || "not resolved"}`;
  if (pathEl) pathEl.textContent = `Path: ${debug.path || "n/a"}`;
  if (latencyEl) latencyEl.textContent = `Latency: ${debug.latency_ms != null ? `${debug.latency_ms} ms` : "n/a"}`;
  if (toolCallsEl) toolCallsEl.textContent = `Tool calls: ${debug.tool_call_count != null ? String(debug.tool_call_count) : "n/a"}`;
  if (requestIdEl) requestIdEl.textContent = `Request ID: ${debug.request_id || "n/a"}`;
  if (errorEl) errorEl.textContent = `Last error: ${debug.error || "none"}`;
}

async function submitChat(message) {
  state.messages.push({role: "user", content: message});
  state.messages.push({
    role: "assistant",
    content: "Running supervisor analysis...",
    loading: true
  });
  state.isLoading = true;
  renderChatStream();

  try {
    const response = await fetch("/api/chat", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({message})
    });
    const responseText = await response.text();
    if (!response.ok) {
      throw new Error(`HTTP ${response.status}: ${responseText.slice(0, 1000)}`);
    }
    const payload = responseText ? JSON.parse(responseText) : {};
    state.latestDebug = payload.debug || null;
    state.messages = state.messages.slice(0, -1);
    state.messages.push({
      role: "assistant",
      content: payload.response_text || "No narrative text returned.",
      response: payload
    });
  } catch (error) {
    const errorText = String(error && error.message ? error.message : error);
    state.latestDebug = {error: errorText, supervisor_endpoint: "mas-30532bb0-endpoint", path: "live"};
    state.messages = state.messages.slice(0, -1);
    state.messages.push({
      role: "assistant",
      content: "Live request failed.",
      response: {
        response_text: `Live request failed: ${errorText}`,
        sources: ["system:error"],
        debug: {error: errorText, supervisor_endpoint: "mas-30532bb0-endpoint", path: "live"}
      }
    });
  } finally {
    state.isLoading = false;
    renderChatStream();
    renderMetadata();
    const sendButton = document.getElementById("send-btn");
    if (sendButton) {
      sendButton.disabled = false;
      sendButton.textContent = "Send";
    }
  }
}

async function loadInsights() {
  if (state.insightsCache) return state.insightsCache;
  const response = await fetch("/api/insights/snapshot");
  if (!response.ok) {
    const errorText = await response.text();
    throw new Error(`HTTP ${response.status}: ${errorText.slice(0, 800)}`);
  }
  state.insightsCache = await response.json();
  return state.insightsCache;
}

async function renderInsightsPage(title) {
  contentEl.innerHTML = `
    <section class="insights-page">
      <header class="insights-header">
        <h2>${escapeHtml(title)}</h2>
        <p>Loading population analytics snapshot...</p>
      </header>
    </section>
  `;

  try {
    const data = await loadInsights();
    contentEl.innerHTML = "";
    const root = createElementFromHtml(`
      <section class="insights-page">
        <header class="insights-header">
          <h2>${escapeHtml(title)}</h2>
          <p>${escapeHtml(data.summary || "")}</p>
        </header>
        <section class="cards"></section>
        <section class="insights-grid"></section>
        <section class="panel"><h4>Underlying Data</h4></section>
      </section>
    `);

    const cards = root.querySelector(".cards");
    (data.cards || []).forEach((card) => {
      cards.appendChild(
        createElementFromHtml(`
          <article class="card">
            <p class="label">${escapeHtml(card.title)}</p>
            <p class="value">${escapeHtml(card.value)}</p>
            <p class="detail">${escapeHtml(card.detail)}</p>
          </article>
        `)
      );
    });

    const grid = root.querySelector(".insights-grid");
    (data.chart_specs || []).forEach((chart) => {
      const panel = createElementFromHtml(`<section class="panel"><h4>${escapeHtml(chart.title || "Visualization")}</h4></section>`);
      panel.appendChild(renderChart(chart));
      grid.appendChild(panel);
    });

    const tablePanel = root.querySelector(".panel:last-of-type");
    if (Array.isArray(data.data_table) && data.data_table.length) {
      tablePanel.appendChild(renderSortableTable(data.data_table));
    }

    contentEl.appendChild(root);
  } catch (error) {
    contentEl.innerHTML = `<section class="panel"><h4>Insights unavailable</h4><p>${escapeHtml(String(error))}</p></section>`;
  }
}

document.querySelectorAll(".nav-item").forEach((button) => {
  button.addEventListener("click", () => {
    state.page = button.dataset.page;
    render();
  });
});

render();
