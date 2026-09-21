import type { ReactNode } from "react";

interface Props {
  title?: string;
  action?: ReactNode;
  children: ReactNode;
  className?: string;
}

export function Card({ title, action, children, className = "" }: Props) {
  return (
    <section className={`card ${className}`}>
      {(title || action) && (
        <div className="row" style={{ justifyContent: "space-between", marginBottom: 12 }}>
          {title ? <h2>{title}</h2> : <span />}
          {action}
        </div>
      )}
      {children}
    </section>
  );
}
