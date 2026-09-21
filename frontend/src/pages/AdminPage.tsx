import { useEffect, useState } from "react";
import { ErrorState, LoadingState } from "../components/ui/States";
import { adminApi, healthApi, isNotImplemented, userMessageFor } from "../services/api";
import type { HealthResponse, User } from "../types/api";

export function AdminPage() {
  const [users, setUsers] = useState<User[] | null>(null);
  const [health, setHealth] = useState<HealthResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    Promise.all([
      adminApi.users().catch((err) => {
        if (isNotImplemented(err)) return [] as User[];
        throw err;
      }),
      healthApi.get(),
    ])
      .then(([nextUsers, nextHealth]) => {
        setUsers(nextUsers);
        setHealth(nextHealth);
      })
      .catch((err) => setError(userMessageFor(err)));
  }, []);

  if (error) return <ErrorState message={error} />;
  if (!users) return <LoadingState label="Loading admin…" />;

  return (
    <div className="page">
      <div className="page-header">
        <div>
          <h1>Admin</h1>
          <p className="lede">Users and API health. Passwords are never returned.</p>
        </div>
      </div>
      <div className="card">
        <p className="muted">Health: {health?.status || "unknown"}</p>
        {users.length ? (
          <ul className="stack" style={{ marginTop: 12 }}>
            {users.map((item) => (
              <li key={item.id}>
                {item.email} {item.is_admin ? "(admin)" : ""}
              </li>
            ))}
          </ul>
        ) : (
          <p className="muted" style={{ marginTop: 12 }}>
            No users returned. GET /api/v1/admin/users is empty or not mounted.
          </p>
        )}
      </div>
    </div>
  );
}
