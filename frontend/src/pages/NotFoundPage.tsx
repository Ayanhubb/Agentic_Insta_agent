import { Link } from "react-router-dom";

export function NotFoundPage() {
  return (
    <div className="page">
      <h1>Page not found</h1>
      <p className="lede">
        That route does not exist. <Link to="/dashboard">Return to the dashboard</Link>.
      </p>
    </div>
  );
}
