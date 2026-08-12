import type { Status } from "../types";

const labels: Record<Status, string> = {
  uploaded: "已上传", analyzing: "分析中", awaiting_review: "待确认", ready: "已就绪", queued: "排队中",
  installing: "安装中", starting: "启动中", authenticating: "登录中", exploring: "探索中",
  review_ready: "待审核", generating: "生成中", completed: "已完成", paused: "已暂停",
  failed: "失败", cancelled: "已取消",
};

export function StatusBadge({ status }: { status: Status }) {
  return <span className={`status status-${status}`}>{labels[status]}</span>;
}
