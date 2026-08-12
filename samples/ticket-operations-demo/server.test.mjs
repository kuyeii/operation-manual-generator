import assert from "node:assert/strict";
import { after, before, test } from "node:test";
import { createAppServer } from "./server.mjs";

let server;
let baseUrl;

before(async () => {
  server = createAppServer();
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  baseUrl = `http://127.0.0.1:${server.address().port}`;
});

after(async () => new Promise((resolve, reject) => server.close((error) => error ? reject(error) : resolve())));

async function json(path, options) {
  const response = await fetch(`${baseUrl}${path}`, { headers: { "Content-Type": "application/json" }, ...options });
  return { response, payload: await response.json() };
}

test("前端首页可访问并暴露七项操作", async () => {
  const response = await fetch(baseUrl);
  const html = await response.text();
  assert.equal(response.status, 200);
  for (const label of ["创建工单", "搜索工单", "筛选优先级", "更新工单状态", "分配负责人", "添加处理备注", "刷新运营统计"]) {
    assert.match(html, new RegExp(label));
  }
});

test("工单创建、查询和筛选形成真实 API 闭环", async () => {
  const created = await json("/api/tickets", { method: "POST", body: JSON.stringify({ title: "测试扫码故障", customer: "测试客户", priority: "高" }) });
  assert.equal(created.response.status, 201);
  assert.match(created.payload.message, /创建成功/);

  const searched = await json("/api/tickets?query=扫码&priority=高");
  assert.equal(searched.payload.total, 1);
  assert.equal(searched.payload.tickets[0].id, created.payload.ticket.id);
});

test("状态、负责人和备注均可更新", async () => {
  const cases = [
    ["status", { status: "已解决" }, "状态更新成功"],
    ["owner", { owner: "测试工程师" }, "负责人分配成功"],
    ["notes", { note: "测试备注" }, "处理备注添加成功"],
  ];
  for (const [endpoint, body, message] of cases) {
    const result = await json(`/api/tickets/101/${endpoint}`, { method: "PATCH", body: JSON.stringify(body) });
    assert.equal(result.response.status, 200);
    assert.equal(result.payload.message, message);
  }
});

test("运营统计反映当前工单数据", async () => {
  const { response, payload } = await json("/api/stats");
  assert.equal(response.status, 200);
  assert.equal(payload.total, 4);
  assert.equal(typeof payload.highPriority, "number");
});
