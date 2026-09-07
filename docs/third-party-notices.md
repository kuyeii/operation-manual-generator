# 软著流程参考与许可

本项目以普通 Python 服务和模型 API 独立实现软著生成，无需安装或运行 Agent Skill。

- Fokkyp/SoftwareCopyright-Skill，v1.3，2026-07-18：参考业务理解、登记字段、源码选择、人工确认、Word/TXT 材料工作流。项目：https://github.com/Fokkyp/SoftwareCopyright-Skill ，许可：https://github.com/Fokkyp/SoftwareCopyright-Skill/blob/main/LICENSE （MIT）。未引入其 vendor/docx-toolkit 或环境安装脚本。
- IvanCodesDev/software-certificate-skill：参考源码证据关联、统一登记事实、真实截图及 DOCX/PDF 质量门禁。项目：https://github.com/IvanCodesDev/software-certificate-skill ，许可：https://github.com/IvanCodesDev/software-certificate-skill/blob/main/LICENSE （MIT）。该项目未提供独立技术设计说明书生成器，本项目自行实现该能力。

以上为工作流参考，未复制上述仓库的源代码文件。后续如直接复用代码，须随文件保留对应版本的版权声明和 MIT 许可全文。

登记字段、交存材料与产品经验建议分别管理。现有规则快照记录于后端 `copyright_service.RULES`；推荐篇幅不作为法定最低要求。申请信息 TXT 是填报底稿，不是官方盖章申请表，登记系统的实时字段约束需在提交时核对。
