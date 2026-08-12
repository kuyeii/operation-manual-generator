import { createServer } from "node:http";

const html = `<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><title>示例管理系统</title></head>
<body><nav><a href="/">首页</a><a href="/users">用户管理</a></nav><main><h1>示例管理系统</h1><button>新建用户</button></main></body></html>`;
createServer((_request, response) => { response.writeHead(200, { "content-type": "text/html; charset=utf-8" }); response.end(html); }).listen(4177, "127.0.0.1");
