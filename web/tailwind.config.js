/** Tailwind mapped onto tokens.css (navy headings, teal→cyan actions, light canvas). */
export default {
  content: ["./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        bg: "var(--color-bg)",
        surface: "var(--color-surface)",
        line: "var(--color-line)",
        ink: "var(--color-text)",
        muted: "var(--color-muted)",
        primary: { DEFAULT: "var(--color-primary)", hover: "var(--color-primary-hover)" },
        accent: "var(--color-accent)",
        "heading-accent": "var(--color-heading-accent)",
        "success-bg": "var(--color-success-bg)",
        "success-fg": "var(--color-success-fg)",
        "warning-bg": "var(--color-warning-bg)",
        "warning-fg": "var(--color-warning-fg)",
        "danger-bg": "var(--color-danger-bg)",
        "danger-fg": "var(--color-danger-fg)",
        chip: "var(--color-chip-bg)",
        "chip-accent": "var(--color-chip-accent-bg)",
        hoverbg: "var(--color-hover)",
      },
      borderRadius: { card: "12px", btn: "8px", chip: "8px", input: "10px", pill: "999px" },
      boxShadow: {
        cardhover: "0 2px 8px rgb(0 0 0 / 0.08)",
        popover: "0 8px 24px rgb(0 0 0 / 0.12)",
      },
      maxWidth: { content: "1500px" },
      fontFamily: {
        sans: ["Open Sans", "-apple-system", "Segoe UI", "Roboto", "Helvetica", "Arial", "sans-serif"],
        mono: ["ui-monospace", "Cascadia Mono", "Consolas", "monospace"],
      },
    },
  },
  plugins: [],
};
