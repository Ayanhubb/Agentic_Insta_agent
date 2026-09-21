import type { ButtonHTMLAttributes, ReactNode } from "react";

interface Props extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: "primary" | "secondary" | "danger" | "ghost";
  block?: boolean;
  children: ReactNode;
}

export function Button({ variant = "primary", block, className = "", children, ...props }: Props) {
  const classes = ["btn", variant !== "primary" ? variant : "", block ? "block" : "", className]
    .filter(Boolean)
    .join(" ");
  return (
    <button className={classes} {...props}>
      {children}
    </button>
  );
}
