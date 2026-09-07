# 28 服务器运行能力验收

## 验证结果

- 任务：`fe9024ff-bfac-4f05-9577-9ef7a7e16047`
- 最终运行批次：`bc85860f-46c6-425e-86eb-911f1602af82`
- 业务任务 ID：`ded41085-329c-4ee0-81a5-2af6d7639ff1`
- 使用部署环境配置的真实模型完成医保上传、银行加密文件上传、计算、下载，四项均验证完成。
- 三个接口均返回 HTTP 200、`ok=true`，返回的业务任务 ID 一致。
- 下载列名为 `bank_user_id, psn_name, result`，三行依次为 BANK-A / Synthetic Alpha / 4、BANK-B / Synthetic Beta / 5、BANK-C / Synthetic Gamma / 8。
- 数据为专用合成样例，不包含真实个人资料；密钥通过任务专属卷只读挂载，不进入源码或正式材料。
- 原始 ZIP、历史步骤和截图保留；每次新运行将历史截图排除，只默认纳入本次验证通过的截图。
- 最终批次记录 `cleanup.complete=true`；任务容器、网络和私密卷均已清理。

## 自动化与视觉检查

- 生成器测试 123 项通过；Ruff 和前端生产构建通过。
- 目标项目 `go test ./...` 通过，包含银行标识字段优先级及同任务上传、导出内容回归。
- Linux Docker 实测 Go + Vite、FastAPI + Vite、Express + Vite：页面标识、前端渲染、代理接口均通过，各服务没有宿主机端口映射。
- 1440×1000 与 390×844 检查配置、功能、运行页，无页面横向溢出；未保存草稿在自动刷新后保留。
- 人工查看真实业务截图，页面显示两个上传文件、相同任务 ID 和三行计算结果，不再是目录列表。
- DOCX/软著流程运行原有自动化回归；本次未重新申请正式软著材料，PDF 保持关闭。
- Django、Flask、Next.js、NestJS 为确定性识别模板，本次未单独部署这些框架；复杂 Compose 需人工明确配置，不支持宿主机绑定、外部卷或环境插值。

## 可复验文件

- `scripts/bank_fixture.go`：复制到目标后端模块的临时命令目录运行；输出目录必须位于源码之外。复用项目加密函数，随机密钥不打印。
- `scripts/bank_acceptance.py`：通过公开接口保存、绑定、确认并启动该专用任务。
- `scripts/verify_bank_result.py`：独立校验实际下载结果和接口关联。
- `scripts/patches/bank-runtime.patch`：记录目标工作副本修复。原直接依赖版本和评分算法未变。
- 本地最终结果：`.runtime/bank-acceptance-final/result.xlsx`、`verification.json`；临时目录不进入 Git。

## 部署与回滚

部署目录：`/home/nanhu/operation-manual-generator`。

更新前已确认无运行任务，并保存：

- 备份目录：`/home/nanhu/manual-runtime-backup-9xIsAU`
- 数据库：该目录内 `database.sqlite3`
- 原部署源码与前端：`deployment.tgz`
- 目标任务原工作副本：`original-task-source.tgz`
- 旧镜像：`operation-manual-generator:before-runtime-20260907`

回滚前再次确认无运行任务并备份最新数据库。停止生成器，恢复旧镜像标签及部署源码，再启动原 Compose 服务。新增表不影响旧代码，优先保留当前数据库与新运行证据；只有确有数据库问题且已备份当前文件时，才考虑恢复旧数据库，否则会丢失本次新记录。不要清空 `data/`。

未创建分支或 worktree，未提交或推送。原有生成器与其他平台服务保持运行，临时验收服务已关闭。
