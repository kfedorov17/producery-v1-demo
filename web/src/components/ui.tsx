/** Shared primitives. */
import type { ButtonHTMLAttributes, HTMLAttributes, ReactNode } from "react";

export const cx = (...parts: (string | false | undefined | null)[]) => parts.filter(Boolean).join(" ");

type ButtonVariant = "primary" | "ghost" | "outline" | "flat" | "teal" | "danger";

const variantClasses: Record<ButtonVariant, string> = {
  primary: "bg-grad-bar text-white disabled:opacity-50 shadow-sm",
  ghost: "bg-surface text-ink border border-line hover:bg-hoverbg disabled:opacity-50",
  outline: "bg-transparent text-ink border border-line hover:bg-hoverbg disabled:opacity-50",
  flat: "bg-surface text-ink hover:bg-hoverbg disabled:opacity-50",
  teal: "bg-success-bg text-success-fg border border-success-fg/30 hover:brightness-95 font-medium",
  danger: "bg-danger-bg text-danger-fg border border-danger-fg/30 hover:brightness-95",
};

export function Button({
  variant = "primary",
  className,
  ...rest
}: ButtonHTMLAttributes<HTMLButtonElement> & { variant?: ButtonVariant }) {
  return (
    <button
      className={cx(
        "inline-flex items-center justify-center gap-2 rounded-btn px-4 py-2 text-sm font-medium",
        "transition-colors focus:outline-none focus-visible:ring-2 focus-visible:ring-accent",
        variantClasses[variant],
        className,
      )}
      {...rest}
    />
  );
}

export function Chip({ children, accent = false, title }: { children: ReactNode; accent?: boolean; title?: string }) {
  return (
    <span
      title={title}
      className={cx(
        "inline-flex items-center rounded-chip px-2.5 py-1 text-[13px] font-medium",
        accent ? "bg-chip-accent text-primary" : "bg-chip text-ink",
      )}
    >
      {children}
    </span>
  );
}

const statusStyles: Record<string, string> = {
  active: "bg-success-bg text-success-fg",
  approved: "bg-success-bg text-success-fg",
  edited: "bg-success-bg text-success-fg",
  executed: "bg-success-bg text-success-fg",
  done: "bg-success-bg text-success-fg",
  ready: "bg-success-bg text-success-fg",
  sent: "bg-success-bg text-success-fg",
  manual: "bg-chip text-muted",
  pending: "bg-warning-bg text-warning-fg",
  paused: "bg-warning-bg text-warning-fg",
  running: "bg-warning-bg text-warning-fg",
  new: "bg-warning-bg text-warning-fg",
  held: "bg-chip text-muted",
  ingesting: "bg-warning-bg text-warning-fg",
  connecting: "bg-warning-bg text-warning-fg",
  connected: "bg-success-bg text-success-fg",
  rejected: "bg-danger-bg text-danger-fg",
  failed: "bg-danger-bg text-danger-fg",
  flagged: "bg-danger-bg text-danger-fg",
  fallback: "bg-warning-bg text-warning-fg",
};

export function StatusPill({ status }: { status: string }) {
  return (
    <span
      className={cx(
        "inline-flex items-center rounded-pill px-2.5 py-0.5 text-[11.5px] font-semibold tracking-wide uppercase",
        statusStyles[status] ?? "bg-chip text-muted",
      )}
    >
      {status.replace(/_/g, " ")}
    </span>
  );
}

export function Card({ children, className, ...rest }: { children: ReactNode; className?: string } & HTMLAttributes<HTMLDivElement>) {
  return (
    <div className={cx("card card-hover", className)} {...rest}>
      {children}
    </div>
  );
}

export function SectionTitle({ children }: { children: ReactNode }) {
  return <h2 className="mb-2 mt-6 text-[15px] font-semibold text-heading-accent">{children}</h2>;
}

export function ErrorState({ children, onRetry }: { children: ReactNode; onRetry?: () => void }) {
  return (
    <div className="card flex flex-col items-center gap-2 border-danger-fg/30 py-8 text-center">
      <div className="text-sm font-semibold text-danger-fg">{children}</div>
      <div className="text-[12.5px] text-muted">Your data is safe; nothing was lost.</div>
      {onRetry && (
        <Button variant="outline" onClick={onRetry}>
          Retry
        </Button>
      )}
    </div>
  );
}

export function EmptyState({ children }: { children: ReactNode }) {
  return (
    <div className="card flex flex-col items-center gap-2 py-10 text-center text-muted">
      <div className="grid grid-cols-2 gap-1 opacity-40">
        {[0, 1, 2, 3].map((i) => (
          <span key={i} className="h-3 w-3 rounded-sm border border-muted" />
        ))}
      </div>
      <div className="text-sm">{children}</div>
    </div>
  );
}

export function ProduceryLogo({ size = 22 }: { size?: number }) {
  return (
    <span className="flex items-center gap-3">
      <span className="text-grad-brand font-extrabold tracking-tight" style={{ fontSize: size }}>
        Producery
      </span>
      <span className="flex flex-col border-l border-line pl-3 leading-none">
        <span className="mb-1 text-[8px] font-semibold tracking-[0.24em] text-muted">THE AGENCY</span>
        <span className="text-[11px] font-semibold text-heading-accent">OPERATING LAYER</span>
      </span>
    </span>
  );
}

export function PageHeader({ title, subtitle, action }: { title: string; subtitle: string; action?: ReactNode }) {
  return (
    <div className="flex flex-col items-center gap-2 pb-6 pt-6 text-center">
      <h1 className="text-[28px] font-bold text-ink">{title}</h1>
      <p className="text-[15px] text-muted">{subtitle}</p>
      {action}
    </div>
  );
}
