import { Activity, MessageSquare, Users } from "lucide-react";
import { NavLink, Outlet } from "react-router-dom";

const navItems = [
  {
    to: "/chat",
    label: "Chat",
    icon: MessageSquare
  },
  {
    to: "/insights",
    label: "Member Insights",
    icon: Users
  },
  {
    to: "/population-trends",
    label: "Population Trends",
    icon: Activity
  }
];

export function SidebarRouteShell() {
  return (
    <div className="app-shell">
      <aside className="left-nav">
        <div className="brand-block">
          <p className="brand-kicker">Healthcare Analytics</p>
          <h1>BCBS Care Intelligence</h1>
        </div>
        <nav aria-label="Primary navigation">
          {navItems.map((item) => (
            <NavLink key={item.to} to={item.to} className={({ isActive }) => (isActive ? "nav-item is-active" : "nav-item")}>
              <item.icon size={16} />
              <span>{item.label}</span>
            </NavLink>
          ))}
        </nav>
      </aside>

      <main className="content-shell">
        <Outlet />
      </main>
    </div>
  );
}

