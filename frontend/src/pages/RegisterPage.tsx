import { useState, type FormEvent } from "react";
import { Link, Navigate } from "react-router-dom";
import { useAuth } from "../context/AuthContext";
import { userMessageFor } from "../services/api";
import { Button } from "../components/ui/Button";
import { Field, Input } from "../components/ui/Field";
import { LoadingState } from "../components/ui/States";

export function RegisterPage() {
  const { user, loading, register } = useAuth();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  if (loading) return <LoadingState label="Checking your session…" />;
  if (user) return <Navigate to="/dashboard" replace />;

  async function onSubmit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await register(email, password);
    } catch (err) {
      setError(userMessageFor(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="auth-screen">
      <div className="card auth-card">
        <h1>Create account</h1>
        <p className="lede">Your content, images, and Instagram account stay isolated to this login.</p>
        <form className="stack" style={{ marginTop: 20 }} onSubmit={(event) => void onSubmit(event)}>
          <Field label="Email">
            <Input type="email" autoComplete="email" required value={email} onChange={(event) => setEmail(event.target.value)} />
          </Field>
          <Field label="Password">
            <Input type="password" autoComplete="new-password" required minLength={8} value={password} onChange={(event) => setPassword(event.target.value)} />
          </Field>
          {error ? <div className="error-box">{error}</div> : null}
          <Button type="submit" disabled={busy} block>
            {busy ? "Creating…" : "Create account"}
          </Button>
        </form>
        <p className="faint" style={{ marginTop: 16 }}>
          Already registered? <Link to="/login">Sign in</Link>
        </p>
      </div>
    </div>
  );
}
