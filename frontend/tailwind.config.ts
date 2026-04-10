import type { Config } from "tailwindcss";

const config: Config = {
  content: ["./app/**/*.{ts,tsx}", "./components/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        bg: {
          primary: "#000000",
          surface: "#0a0a0a",
          elevated: "#171717",
        },
        accent: {
          DEFAULT: "#F97316",
          hover: "#FB923C",
          muted: "rgba(249, 115, 22, 0.15)",
        },
        fg: {
          DEFAULT: "#ffffff",
          secondary: "#a1a1a1",
          muted: "#575757",
        },
        line: {
          DEFAULT: "#262626",
          hover: "#404040",
        },
        success: "#22c55e",
        error: "#ef4444",
        warning: "#f59e0b",
        running: "#F97316",
      },
      fontFamily: {
        display: ["var(--font-geist-sans)", "system-ui", "sans-serif"],
        body: ["var(--font-geist-sans)", "system-ui", "sans-serif"],
        mono: ["var(--font-geist-mono)", "monospace"],
      },
      borderRadius: {
        sm: "2px",
        DEFAULT: "6px",
        md: "8px",
        lg: "12px",
      },
      keyframes: {
        "fade-in": {
          "0%": { opacity: "0" },
          "100%": { opacity: "1" },
        },
        "slide-up": {
          "0%": { transform: "translateY(8px)", opacity: "0" },
          "100%": { transform: "translateY(0)", opacity: "1" },
        },
      },
      animation: {
        "fade-in": "fade-in 0.3s ease-out",
        "slide-up": "slide-up 0.3s ease-out",
      },
    },
  },
  plugins: [],
};

export default config;
