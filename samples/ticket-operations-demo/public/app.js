const state = { tickets: [], selectedId: 101, query: "", priority: "全部" };

const api = {
  get(path) { return request(path); },
  post(path, body) { return request(path, { method: "POST", body: JSON.stringify(body) }); },
  patch(path, body) { return request(path, { method: "PATCH", body: JSON.stringify(body) }); },
};

async function request(path, options = {}) {
  const response = await fetch(path, { headers: { "Content-Type": "application/json" }, ...options });
  const payload = await response.json();
  if (!response.ok) throw new Error(payload.error || "请求失败");
  return payload;
}

async function loadTickets() {
  const params = new URLSearchParams({ query: state.query, priority: state.priority });
  const payload = await api.get(`/api/tickets?${params}`);
  state.tickets = payload.tickets;
  renderTickets();
}

async function refreshStats(showResult = false) {
  const stats = await api.get("/api/stats");
  document.querySelector("#stats").innerHTML = [
    ["工单总数", stats.total], ["待处理", stats.待处理], ["处理中", stats.处理中], ["已解决", stats.已解决], ["高优先级", stats.highPriority],
  ].map(([label, value]) => `<article><span>${label}</span><strong>${value}</strong></article>`).join("");
  if (showResult) showNotice(`运营统计刷新成功，共 ${stats.total} 张工单`);
}

function renderTickets() {
  const list = document.querySelector("#ticket-list");
  document.querySelector("#result-count").textContent = `共 ${state.tickets.length} 张工单`;
  if (!state.tickets.length) {
    list.innerHTML = '<p class="empty-result">没有符合条件的工单，请调整查询条件。</p>';
    return;
  }
  list.innerHTML = state.tickets.map((ticket) => `
    <article class="ticket-row ${ticket.id === state.selectedId ? "selected" : ""}" data-ticket-id="${ticket.id}" tabindex="0" role="button" aria-label="选择工单 ${ticket.id}">
      <div class="ticket-identity"><span>#${ticket.id}</span><div><strong>${escapeHtml(ticket.title)}</strong><small>${escapeHtml(ticket.customer)}</small></div></div>
      <span class="priority priority-${ticket.priority}">${ticket.priority}优先级</span>
      <span class="status">${ticket.status}</span>
      <span class="owner">${escapeHtml(ticket.owner)}</span>
      <span class="note-count">${ticket.notes.length} 条备注</span>
    </article>`).join("");
  list.querySelectorAll(".ticket-row").forEach((row) => {
    const select = () => selectTicket(Number(row.dataset.ticketId));
    row.addEventListener("click", select);
    row.addEventListener("keydown", (event) => { if (event.key === "Enter" || event.key === " ") select(); });
  });
}

function selectTicket(id) {
  state.selectedId = id;
  document.querySelector("#selected-ticket").textContent = `#${id}`;
  renderTickets();
  showNotice(`已选择工单 #${id}`);
}

document.querySelector("#create-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  await runAction(async () => {
    const form = new FormData(event.currentTarget);
    const payload = await api.post("/api/tickets", Object.fromEntries(form));
    state.selectedId = payload.ticket.id;
    document.querySelector("#selected-ticket").textContent = `#${payload.ticket.id}`;
    await Promise.all([loadTickets(), refreshStats()]);
    showNotice(payload.message);
  });
});

document.querySelector("#search-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  state.query = document.querySelector("#query").value.trim();
  await runAction(async () => {
    await loadTickets();
    showNotice(`搜索完成，找到 ${state.tickets.length} 张工单`);
  });
});

document.querySelector("#apply-filter").addEventListener("click", () => runAction(async () => {
  state.priority = document.querySelector("#priority-filter").value;
  await loadTickets();
  showNotice(`优先级筛选成功，显示 ${state.tickets.length} 张工单`);
}));

document.querySelector("#refresh-stats").addEventListener("click", () => runAction(() => refreshStats(true)));

bindTicketAction("#status-form", "status", (form) => ({ status: form.get("status") }));
bindTicketAction("#owner-form", "owner", (form) => ({ owner: form.get("owner") }));
bindTicketAction("#note-form", "notes", (form) => ({ note: form.get("note") }));

function bindTicketAction(selector, endpoint, values) {
  document.querySelector(selector).addEventListener("submit", async (event) => {
    event.preventDefault();
    await runAction(async () => {
      const payload = await api.patch(`/api/tickets/${state.selectedId}/${endpoint}`, values(new FormData(event.currentTarget)));
      await Promise.all([loadTickets(), refreshStats()]);
      showNotice(`${payload.message}：工单 #${payload.ticket.id}`);
    });
  });
}

async function runAction(action) {
  try { await action(); } catch (error) { showNotice(error.message, true); }
}

let noticeTimer;
function showNotice(message, isError = false) {
  const notice = document.querySelector("#notice");
  notice.textContent = message;
  notice.classList.toggle("error", isError);
  notice.hidden = false;
  clearTimeout(noticeTimer);
  noticeTimer = setTimeout(() => { notice.hidden = true; }, 8000);
}

function escapeHtml(value) {
  return String(value).replace(/[&<>"]/g, (character) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[character]);
}

await Promise.all([loadTickets(), refreshStats()]);
