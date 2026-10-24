import {
  createContext,
  useContext,
  useEffect,
  useId,
  useRef,
  useState,
  type ButtonHTMLAttributes,
  type InputHTMLAttributes,
  type ReactNode,
  type SelectHTMLAttributes,
  type TextareaHTMLAttributes,
} from "react";
import { createPortal } from "react-dom";
import { ApiError } from "../api";
import { humanError, TERMS, useL, useLang, type HumanError } from "../i18n";
import { useApp, type Toast } from "../state";
import { Icon, type IconName } from "./Icon";

export function Button({
  variant = "primary",
  size = "md",
  icon,
  loading = false,
  disabled = false,
  children,
  className = "",
  ...rest
}: ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: "primary" | "secondary" | "ghost" | "danger";
  size?: "sm" | "md" | "lg";
  icon?: IconName;
  loading?: boolean;
}) {
  const l = useL();
  return (
    <button
      {...rest}
      className={`btn btn-${variant} btn-${size} ${className}`}
      disabled={disabled || loading}
      aria-disabled={disabled || loading ? true : undefined}
      data-disabled-label={disabled || loading ? l("不可用", "Unavailable") : undefined}
    >
      {loading ? <span className="spinner" aria-hidden="true" /> : icon ? <Icon name={icon} size={18} /> : null}
      <span>{children}</span>
    </button>
  );
}

export function IconButton({
  icon,
  label,
  ...rest
}: ButtonHTMLAttributes<HTMLButtonElement> & { icon: IconName; label: string }) {
  return (
    <button {...rest} className={`icon-btn ${rest.className || ""}`} aria-label={label} title={label}>
      <Icon name={icon} size={19} />
    </button>
  );
}

export function Card({
  children,
  className = "",
  tone = "canvas",
}: {
  children: ReactNode;
  className?: string;
  tone?: "canvas" | "accent" | "inverse";
}) {
  return <div className={`card card-${tone} ${className}`}>{children}</div>;
}

export function Section({
  title,
  subtitle,
  actions,
  children,
  className = "",
  id,
}: {
  title: string;
  subtitle?: string;
  actions?: ReactNode;
  children: ReactNode;
  className?: string;
  id?: string;
}) {
  return (
    <section className={`section ${className}`} id={id}>
      <div className="section-head">
        <div>
          <h2>{title}</h2>
          {subtitle ? <p className="sub">{subtitle}</p> : null}
        </div>
        {actions ? <div className="section-actions">{actions}</div> : null}
      </div>
      {children}
    </section>
  );
}

export function PageHeader({
  title,
  subtitle,
  actions,
}: {
  title: string;
  subtitle?: string;
  actions?: ReactNode;
}) {
  return (
    <header className="page-head">
      <div>
        <h1>{title}</h1>
        {subtitle ? <p className="sub">{subtitle}</p> : null}
      </div>
      {actions ? <div className="page-actions">{actions}</div> : null}
    </header>
  );
}

export function Badge({
  children,
  variant = "outline",
}: {
  children: ReactNode;
  variant?: "outline" | "accent" | "inverse" | "dashed";
}) {
  return <span className={`badge badge-${variant}`}>{children}</span>;
}

export function StatusMark({
  tone,
  children,
}: {
  tone: "ok" | "warn" | "idle" | "bad";
  children: ReactNode;
}) {
  return (
    <span className={`status-mark status-${tone}`}>
      <span className="status-shape" aria-hidden="true" />
      <span>{children}</span>
    </span>
  );
}

export function Toggle({
  checked,
  onChange,
  label,
  hint,
  disabled = false,
  disabledReason,
}: {
  checked: boolean;
  onChange: (next: boolean) => void;
  label: string;
  hint?: string;
  disabled?: boolean;
  disabledReason?: string;
}) {
  const id = useId();
  const l = useL();
  return (
    <div className={`toggle-row ${disabled ? "is-disabled" : ""}`}>
      <div>
        <label htmlFor={id} className="toggle-label">
          {label}
        </label>
        {hint ? <p className="sub">{hint}</p> : null}
        {disabled && disabledReason ? <p className="disabled-note">{disabledReason}</p> : null}
      </div>
      <button
        id={id}
        type="button"
        role="switch"
        aria-checked={Boolean(checked)}
        className={`toggle ${checked ? "is-on" : ""}`}
        onClick={() => onChange(!checked)}
        disabled={disabled}
        aria-label={label}
      >
        <span className="toggle-thumb" aria-hidden="true" />
        <span className="sr-only">
          {checked ? l("已开启", "On") : l("已关闭", "Off")}
        </span>
      </button>
    </div>
  );
}

export function Field({
  label,
  hint,
  error,
  children,
  htmlFor,
}: {
  label: string;
  hint?: string;
  error?: string;
  children: ReactNode;
  htmlFor?: string;
}) {
  return (
    <div className="field">
      <label htmlFor={htmlFor}>{label}</label>
      {children}
      {hint ? <p className="sub">{hint}</p> : null}
      {error ? <p className="field-error">{error}</p> : null}
    </div>
  );
}

export function Input(props: InputHTMLAttributes<HTMLInputElement>) {
  return <input {...props} className={`input ${props.className || ""}`} />;
}

export function TextArea(props: TextareaHTMLAttributes<HTMLTextAreaElement>) {
  return <textarea {...props} className={`input textarea ${props.className || ""}`} />;
}

export function Select({
  children,
  ...props
}: SelectHTMLAttributes<HTMLSelectElement> & { children: ReactNode }) {
  return (
    <select {...props} className={`input select ${props.className || ""}`}>
      {children}
    </select>
  );
}

export function Segmented<T extends string>({
  value,
  options,
  onChange,
  label,
  disabled = false,
}: {
  value: T;
  options: { value: T; label: string }[];
  onChange: (value: T) => void;
  label: string;
  disabled?: boolean;
}) {
  return (
    <div className="segmented" role="radiogroup" aria-label={label}>
      {options.map((option) => (
        <button
          key={option.value}
          type="button"
          role="radio"
          aria-checked={value === option.value}
          className={`segment ${value === option.value ? "is-active" : ""}`}
          onClick={() => onChange(option.value)}
          disabled={disabled}
        >
          {option.label}
        </button>
      ))}
    </div>
  );
}

export function RadioCards<T extends string>({
  value,
  options,
  onChange,
  label,
  disabled = false,
}: {
  value: T;
  options: { value: T; title: string; description: string; detail?: string }[];
  onChange: (value: T) => void;
  label: string;
  disabled?: boolean;
}) {
  const refs = useRef<(HTMLButtonElement | null)[]>([]);
  const focusAt = (index: number) => {
    const next = (index + options.length) % options.length;
    const option = options[next];
    onChange(option.value);
    refs.current[next]?.focus();
  };
  return (
    <div className="radio-cards" role="radiogroup" aria-label={label}>
      {options.map((option, index) => (
        <button
          key={option.value}
          ref={(node) => {
            refs.current[index] = node;
          }}
          type="button"
          role="radio"
          aria-checked={value === option.value}
          tabIndex={value === option.value ? 0 : -1}
          disabled={disabled}
          className={`radio-card ${value === option.value ? "is-active" : ""}`}
          onClick={() => onChange(option.value)}
          onKeyDown={(event) => {
            if (disabled) return;
            if (event.key === "ArrowDown" || event.key === "ArrowRight") {
              event.preventDefault();
              focusAt(index + 1);
            } else if (event.key === "ArrowUp" || event.key === "ArrowLeft") {
              event.preventDefault();
              focusAt(index - 1);
            } else if (event.key === " " || event.key === "Enter") {
              event.preventDefault();
              onChange(option.value);
            }
          }}
        >
          <span className="radio-dot" aria-hidden="true">
            {value === option.value ? <span className="radio-dot-inner" /> : null}
          </span>
          <span className="radio-body">
            <strong>{option.title}</strong>
            <span className="sub">{option.description}</span>
            {option.detail ? <span className="radio-detail">{option.detail}</span> : null}
          </span>
        </button>
      ))}
    </div>
  );
}

export function Modal({
  title,
  subtitle,
  children,
  footer,
  onClose,
  wide = false,
}: {
  title: string;
  subtitle?: string;
  children: ReactNode;
  footer?: ReactNode;
  onClose: () => void;
  wide?: boolean;
}) {
  const l = useL();
  const panel = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const previous = document.activeElement as HTMLElement | null;
    const node = panel.current;
    const focusable = node?.querySelector<HTMLElement>(
      "button, [href], input, select, textarea, [tabindex]:not([tabindex='-1'])",
    );
    focusable?.focus();
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.preventDefault();
        onClose();
      }
      if (event.key !== "Tab" || !node) return;
      const items = Array.from(
        node.querySelectorAll<HTMLElement>(
          "button:not([disabled]), [href], input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex='-1'])",
        ),
      ).filter((item) => item.offsetParent !== null);
      if (items.length === 0) return;
      const first = items[0];
      const last = items[items.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    };
    document.addEventListener("keydown", onKey);
    const oldOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.removeEventListener("keydown", onKey);
      document.body.style.overflow = oldOverflow;
      previous?.focus();
    };
  }, [onClose]);
  return createPortal(
    <div className="modal-screen" role="presentation">
      <div
        ref={panel}
        className={`modal-panel ${wide ? "modal-wide" : ""}`}
        role="dialog"
        aria-modal="true"
        aria-label={title}
      >
        <div className="modal-head">
          <div>
            <h2>{title}</h2>
            {subtitle ? <p className="sub">{subtitle}</p> : null}
          </div>
          <IconButton icon="x" label={l("关闭", "Close")} onClick={onClose} />
        </div>
        <div className="modal-body">{children}</div>
        {footer ? <div className="modal-foot">{footer}</div> : null}
      </div>
    </div>,
    document.body,
  );
}

export function ConfirmDialog({
  title,
  children,
  confirmLabel,
  onConfirm,
  onClose,
  danger = false,
  busy = false,
}: {
  title: string;
  children: ReactNode;
  confirmLabel: string;
  onConfirm: () => void;
  onClose: () => void;
  danger?: boolean;
  busy?: boolean;
}) {
  const l = useL();
  return (
    <Modal
      title={title}
      onClose={onClose}
      footer={
        <>
          <Button variant="ghost" onClick={onClose} disabled={busy}>
            {l("取消", "Cancel")}
          </Button>
          <Button variant={danger ? "danger" : "primary"} onClick={onConfirm} loading={busy}>
            {confirmLabel}
          </Button>
        </>
      }
    >
      <div className="confirm-body">{children}</div>
    </Modal>
  );
}

export function ErrorBanner({
  error,
  onRetry,
}: {
  error: ApiError | HumanError | null;
  onRetry?: () => void;
}) {
  const l = useL();
  const lang = useLang();
  if (!error) return null;
  const human =
    error instanceof ApiError ? humanError(error.code, error.message, error.action, lang) : error;
  return (
    <div className="error-banner" role="alert">
      <Icon name="alert" size={22} />
      <div>
        <strong>{human.what}</strong>
        <p>{human.why}</p>
        <p>{human.action}</p>
        <p className="error-money">{human.money}</p>
        <div className="row">
          {onRetry ? (
            <Button size="sm" variant="ghost" icon="refresh" onClick={onRetry}>
              {l("重试", "Retry")}
            </Button>
          ) : null}
          <CopyButton
            label={l("复制诊断信息", "Copy diagnostics")}
            text={JSON.stringify(
              {
                code: error instanceof ApiError ? error.code : "HUMAN_ERROR",
                what: human.what,
                why: human.why,
                action: human.action,
                money: human.money,
              },
              null,
              2,
            )}
          />
        </div>
      </div>
    </div>
  );
}

export function EmptyState({
  icon = "spark",
  title,
  children,
  action,
}: {
  icon?: IconName;
  title: string;
  children?: ReactNode;
  action?: ReactNode;
}) {
  return (
    <div className="empty-state">
      <span className="empty-icon">
        <Icon name={icon} size={28} />
      </span>
      <h3>{title}</h3>
      {children ? <div className="sub">{children}</div> : null}
      {action ? <div className="empty-action">{action}</div> : null}
    </div>
  );
}

export function Progress({
  value,
  max,
  label,
}: {
  value: number;
  max: number;
  label: string;
}) {
  const safeMax = Math.max(max, 0.000001);
  const percent = Math.min(100, Math.max(0, (value / safeMax) * 100));
  return (
    <div className="progress-wrap">
      <progress className="progress" value={percent} max={100} aria-label={label} />
      <span className="progress-label">{Math.round(percent)}%</span>
    </div>
  );
}

export function Tabs<T extends string>({
  value,
  tabs,
  onChange,
  label,
}: {
  value: T;
  tabs: { value: T; label: string; count?: number }[];
  onChange: (value: T) => void;
  label: string;
}) {
  const refs = useRef<(HTMLButtonElement | null)[]>([]);
  const focusAt = (index: number) => {
    const next = (index + tabs.length) % tabs.length;
    onChange(tabs[next].value);
    refs.current[next]?.focus();
  };
  return (
    <div className="tabs" role="tablist" aria-label={label}>
      {tabs.map((tab, index) => (
        <button
          key={tab.value}
          ref={(node) => {
            refs.current[index] = node;
          }}
          role="tab"
          type="button"
          aria-selected={value === tab.value}
          tabIndex={value === tab.value ? 0 : -1}
          className={`tab ${value === tab.value ? "is-active" : ""}`}
          onClick={() => onChange(tab.value)}
          onKeyDown={(event) => {
            if (event.key === "ArrowRight" || event.key === "ArrowDown") {
              event.preventDefault();
              focusAt(index + 1);
            } else if (event.key === "ArrowLeft" || event.key === "ArrowUp") {
              event.preventDefault();
              focusAt(index - 1);
            } else if (event.key === "Home") {
              event.preventDefault();
              focusAt(0);
            } else if (event.key === "End") {
              event.preventDefault();
              focusAt(tabs.length - 1);
            }
          }}
        >
          {tab.label}
          {typeof tab.count === "number" ? <span className="tab-count">{tab.count}</span> : null}
        </button>
      ))}
    </div>
  );
}

export function Term({ name, children }: { name: string; children: ReactNode }) {
  const app = useApp();
  const lang = useLang();
  const term = TERMS[name];
  if (!term || !app.settings?.ui.terminology_tooltips) return <>{children}</>;
  return (
    <abbr className="term" title={lang === "zh" ? term.zh : term.en}>
      {children}
    </abbr>
  );
}

export function InfoNote({
  children,
  tone = "info",
  icon = "shield",
}: {
  children: ReactNode;
  tone?: "info" | "warn" | "ok";
  icon?: IconName;
}) {
  return (
    <div className={`note note-${tone}`}>
      <Icon name={icon} size={20} />
      <div>{children}</div>
    </div>
  );
}

export function CopyButton({
  text,
  label,
  onCopied,
}: {
  text: string;
  label?: string;
  onCopied?: () => void;
}) {
  const l = useL();
  const [copied, setCopied] = useState(false);
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(text);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1800);
      onCopied?.();
    } catch {
      setCopied(false);
    }
  };
  return (
    <Button size="sm" variant="ghost" icon="copy" onClick={copy}>
      {copied ? l("已复制", "Copied") : label || l("复制", "Copy")}
    </Button>
  );
}

export function CodeBlock({ code, label }: { code: string; label?: string }) {
  const l = useL();
  return (
    <div className="code-wrap">
      <div className="code-head">
        <span>{label || "diff"}</span>
        <CopyButton text={code} label={l("复制", "Copy")} />
      </div>
      <pre className="code-block" tabIndex={0}>
        {code || l("（没有内容）", "(empty)")}
      </pre>
    </div>
  );
}

export function KeyValue({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="kv">
      <span className="kv-label">{label}</span>
      <span className="kv-value">{children}</span>
    </div>
  );
}

export function ExternalLink({
  href,
  children,
}: {
  href: string;
  children: ReactNode;
}) {
  return (
    <a className="link" href={href} target="_blank" rel="noreferrer noopener">
      {children}
      <Icon name="external" size={14} />
    </a>
  );
}

export function ToastHost({
  toasts,
  onDismiss,
}: {
  toasts: Toast[];
  onDismiss: (id: number) => void;
}) {
  const lang = useLang();
  const l = useL();
  if (toasts.length === 0) return null;
  return createPortal(
    <div className="toast-stack" aria-live="polite" aria-atomic="false">
      {toasts.map((toast) => (
        <div key={toast.id} className={`toast toast-${toast.kind}`} role="status">
          <Icon name={toast.kind === "ok" ? "check" : toast.kind === "warn" ? "alert" : "spark"} size={18} />
          <span>{lang === "zh" ? toast.zh : toast.en}</span>
          <IconButton
            icon="x"
            label={l("关闭提示", "Dismiss")}
            onClick={() => onDismiss(toast.id)}
          />
        </div>
      ))}
    </div>,
    document.body,
  );
}

export function DangerAction({
  title,
  description,
  button,
  children,
}: {
  title: string;
  description: string;
  button: ReactNode;
  children?: ReactNode;
}) {
  return (
    <div className="danger-action">
      <div>
        <strong>{title}</strong>
        <p className="sub">{description}</p>
        {children}
      </div>
      <div>{button}</div>
    </div>
  );
}

export function ScreenReaderOnly({ children }: { children: ReactNode }) {
  return <span className="sr-only">{children}</span>;
}

export function useAsyncAction() {
  const [busy, setBusy] = useState(false);
  return {
    busy,
    run: async (fn: () => Promise<unknown>) => {
      setBusy(true);
      try {
        return await fn();
      } finally {
        setBusy(false);
      }
    },
  };
}

export const LanguageNote = createContext<null>(null);
export function NoopLanguageNote() {
  return useContext(LanguageNote);
}
