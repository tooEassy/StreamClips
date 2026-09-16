import { Link, Route, Routes } from "react-router-dom";
import Home from "./pages/Home";
import JobPage from "./pages/JobPage";

export default function App() {
  return (
    <div className="shell">
      <header className="top">
        <Link to="/" className="brand">
          Нарезки <span>стримов</span>
        </Link>
        <div className="hint">Twitch VOD → моменты → TikTok 9:16</div>
      </header>
      <Routes>
        <Route path="/" element={<Home />} />
        <Route path="/jobs/:id" element={<JobPage />} />
      </Routes>
    </div>
  );
}
