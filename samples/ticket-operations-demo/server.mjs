import { createServer } from "node:http";
import { readFile } from "node:fs/promises";
import { extname, join } from "node:path";
import { fileURLToPath } from "node:url";

const publicDirectory = fileURLToPath(new URL("./public/", import.meta.url));
const configuredPort = Number.parseInt(process.env.PORT || "3000", 10);

export function createAppServer() {
  let nextId = 104;
  const tickets = [
    { id: 101, title: "门店打印机无法出单", customer: "云杉便利店", priority: "高", status: "处理中", owner: "林晓", notes: ["已联系门店确认设备在线"] },
    { id: 102, title: "会员积分显示异常", customer: "北辰生活馆", priority: "中", status: "待处理", owner: "未分配", notes: [] },
    { id: 103, title: "夜间报表生成延迟", customer: "远航餐饮", priority: "低", status: "已解决", owner: "周越", notes: ["已优化报表任务队列"] },
  ];

  return createServer(async (request, response) => {
    try {
      const url = new URL(request.url || "/", "http://127.0.0.1");
      if (url.pathname.startsWith("/api/")) {
        await handleApi(request, response, url, tickets, () => nextId++);
        return;
      }
      await serveAsset(response, url.pathname);
    } catch (error) {
      sendJson(response, 500, { error: error instanceof Error ? error.message : "服务器错误" });
    }
  });
}

async function handleApi(request, response, url, tickets, allocateId) {
  if (request.method === "GET" && url.pathname === "/api/tickets") {
    const query = (url.searchParams.get("query") || "").trim().toLowerCase();
    const priority = url.searchParams.get("priority") || "全部";
    const filtered = tickets.filter((ticket) => {
      const matchesQuery = !query || `${ticket.title} ${ticket.customer} ${ticket.owner}`.toLowerCase().includes(query);
      return matchesQuery && (priority === "全部" || ticket.priority === priority);
    });
    sendJson(response, 200, { tickets: filtered, total: filtered.length });
    return;
  }

  if (request.method === "GET" && url.pathname === "/api/stats") {
    const counts = Object.fromEntries(["待处理", "处理中", "已解决"].map((status) => [status, tickets.filter((ticket) => ticket.status === status).length]));
    sendJson(response, 200, { total: tickets.length, ...counts, highPriority: tickets.filter((ticket) => ticket.priority === "高").length, updatedAt: new Date().toISOString() });
    return;
  }

  if (request.method === "POST" && url.pathname === "/api/tickets") {
    const body = await readJson(request);
    if (!body.title?.trim() || !body.customer?.trim()) {
      sendJson(response, 400, { error: "工单标题和客户名称不能为空" });
      return;
    }
    const ticket = { id: allocateId(), title: body.title.trim(), customer: body.customer.trim(), priority: body.priority || "中", status: "待处理", owner: "未分配", notes: [] };
    tickets.unshift(ticket);
    sendJson(response, 201, { ticket, message: `工单 #${ticket.id} 创建成功` });
    return;
  }

  const ticketMatch = url.pathname.match(/^\/api\/tickets\/(\d+)\/(status|owner|notes)$/);
  if (ticketMatch && request.method === "PATCH") {
    const ticket = tickets.find((item) => item.id === Number(ticketMatch[1]));
    if (!ticket) {
      sendJson(response, 404, { error: "未找到工单" });
      return;
    }
    const body = await readJson(request);
    if (ticketMatch[2] === "status") ticket.status = body.status;
    if (ticketMatch[2] === "owner") ticket.owner = body.owner?.trim() || "未分配";
    if (ticketMatch[2] === "notes") {
      if (!body.note?.trim()) {
        sendJson(response, 400, { error: "备注内容不能为空" });
        return;
      }
      ticket.notes.push(body.note.trim());
    }
    const actionNames = { status: "状态更新成功", owner: "负责人分配成功", notes: "处理备注添加成功" };
    sendJson(response, 200, { ticket, message: actionNames[ticketMatch[2]] });
    return;
  }

  sendJson(response, 404, { error: "接口不存在" });
}

async function serveAsset(response, pathname) {
  const requested = pathname === "/" ? "index.html" : pathname.slice(1);
  const allowed = new Set(["index.html", "app.js", "styles.css"]);
  const filename = allowed.has(requested) ? requested : "index.html";
  const content = await readFile(join(publicDirectory, filename));
  const contentTypes = { ".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8", ".css": "text/css; charset=utf-8" };
  response.writeHead(200, { "content-type": contentTypes[extname(filename)] });
  response.end(content);
}

function readJson(request) {
  return new Promise((resolve, reject) => {
    let body = "";
    request.on("data", (chunk) => { body += chunk; });
    request.on("end", () => {
      try { resolve(body ? JSON.parse(body) : {}); } catch (error) { reject(error); }
    });
    request.on("error", reject);
  });
}

function sendJson(response, status, payload) {
  response.writeHead(status, { "content-type": "application/json; charset=utf-8", "cache-control": "no-store" });
  response.end(JSON.stringify(payload));
}

if (process.argv[1] === fileURLToPath(import.meta.url)) {
  createAppServer().listen(configuredPort, "127.0.0.1", () => {
    console.log(`星桥工单中心已启动：http://127.0.0.1:${configuredPort}`);
  });
}
