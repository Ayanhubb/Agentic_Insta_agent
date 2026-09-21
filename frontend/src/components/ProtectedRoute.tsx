import type { ReactNode } from "react";
import { Navigate, useLocation } from "react-router-dom";
import { useAuth } from "../context/AuthContext";
import { LoadingState } from "./ui/States";
import { AppShell } from "./layout/AppShell";
import { ChangePasswordForm } from "./ChangePasswordForm";

export function ProtectedRoute({
  children,
  admin,
}: {
  children: ReactNode;
  admin?: boolean;
}) {
  const { user, loading } = useAuth();
  const location = useLocation();

  if (loading) {
    return <LoadingState label="Checking your session…" />;
  }

  if (!user) {
    return <Navigate to="/login" replace state={{ from: location.pathname }} />;
  }

  if (user.must_change_password && location.pathname !== "/settings") {
    return (
      <AppShell>
        <div className="page">
          <div className="page-header">
            <div>
              <h1>Change your password</h1>
              <p className="lede">
                This account requires a new password before you can use the studio.
              </p>
            </div>
          </div>
          <div className="card" style={{ maxWidth: 480 }}>
            <ChangePasswordForm forced />
          </div>
        </div>
      </AppShell>
    );
  }

  if (admin && !user.is_admin) {
    return (
      <AppShell>
        <div className="page">
          <div className="error-box" role="alert">
            Admin access is required for this page.
          </div>
        </div>
      </AppShell>
    );
  }

  return <AppShell>{children}</AppShell>;
}
