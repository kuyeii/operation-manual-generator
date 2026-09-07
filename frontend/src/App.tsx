import { Navigate, Route, Routes } from "react-router-dom";
import { FileText } from "lucide-react";
import { TaskList } from "./pages/TaskList";
import { TaskWorkspace } from "./pages/TaskWorkspace";

export default function App() {
  return (
    <div className="app-shell">
      <header className="app-header">
        <a className="brand" href="/"><span className="brand-mark"><FileText size={19} /></span>软著与操作手册</a>
        <span className="header-meta">本地工作台</span>
      </header>
      <main><Routes>
        <Route path="/" element={<TaskList />} />
        <Route path="/tasks/:taskId/*" element={<TaskWorkspace />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes></main>
    </div>
  );
}
