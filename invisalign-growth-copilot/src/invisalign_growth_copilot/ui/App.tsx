import { Navigate, Route, Routes } from "react-router-dom";
import { SidebarRouteShell } from "@/routes/_sidebar/route";
import { AboutPage } from "@/routes/_sidebar/about";
import { ChatPage } from "@/routes/_sidebar/chat";

export default function App() {
  return (
    <Routes>
      <Route element={<SidebarRouteShell />}>
        <Route path="/" element={<Navigate to="/chat" replace />} />
        <Route path="/chat" element={<ChatPage />} />
        <Route path="/about" element={<AboutPage />} />
      </Route>
    </Routes>
  );
}
