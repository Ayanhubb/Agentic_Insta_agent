import { useEffect, useState, type ReactNode } from "react";
import { NavLink } from "react-router-dom";
import { useAuth } from "../../context/AuthContext";
import { Button } from "../ui/Button";

const NAV = [
  { section: "Studio", items: [
    { to: "/dashboard", label: "Dashboard" },
    { to: "/generate", label: "AI Generator" },
    { to: "/images", label: "Images" },
    { to: "/posts", label: "Posts" },
  ]},
  { section: "Automation", items: [
    { to: "/automation", label: "Automation" },
    { to: "/festivals", label: "Festivals" },
  ]},
  { section: "Account", items: [
    { to: "/business", label: "Business" },
    { to: "/instagram", label: "Instagram" },
    { to: "/settings", label: "Settings" },
  ]},
];

export function AppShell({ children }: { children: ReactNode }) {
  const { user, logout } = useAuth();
  const [open, setOpen] = useState(false);

  useEffect(() => {
    const onResize = () => {
      if (window.innerWidth > 820) setOpen(false);
    };
    window.addEventListener("resize", onResize);
    return () => window.removeEventListener("resize", onResize);
  }, []);

  return (
    <div className="app-shell">
      {open ? <div className="backdrop" onClick={() => setOpen(false)} /> : null}
      <aside className={`sidebar ${open ? "open" : ""}`} aria-label="Primary">
        <NavLink to="/dashboard" className="brand" onClick={() => setOpen(false)}>
          <span className="brand-mark">Y</span>
          <span>
            <div className="brand-name">Yotto Labs</div>
            <div className="brand-sub">Content studio</div>
          </span>
        </NavLink>
        <nav className="nav-list">
          {NAV.map((group) => (
            <div key={group.section}>
              <div className="nav-section">{group.section}</div>
              {group.items.map((item) => (
                <NavLink
                  key={item.to}
                  to={item.to}
                  className={({ isActive }) => `nav-link ${isActive ? "active" : ""}`}
                  onClick={() => setOpen(false)}
                >
                  {item.label}
                </NavLink>
              ))}
            </div>
          ))}
          {user?.is_admin ? (
            <div>
              <div className="nav-section">Admin</div>
              <NavLink
                to="/admin"
                className={({ isActive }) => `nav-link ${isActive ? "active" : ""}`}
                onClick={() => setOpen(false)}
              >
                Admin
              </NavLink>
            </div>
          ) : null}
        </nav>
        <div className="sidebar-footer">Asia/Kolkata · Instagram Agent</div>
      </aside>
      <div className="workspace">
        <header className="topbar">
          <Button className="menu-toggle" variant="secondary" type="button" onClick={() => setOpen(true)} aria-label="Open menu">
            Menu
          </Button>
          <span className="user-chip">{user?.email}</span>
          <Button variant="ghost" type="button" onClick={() => void logout()}>
            Sign out
          </Button>
        </header>
        <main id="main">{children}</main>
      </div>
    </div>
  );
}
