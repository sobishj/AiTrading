/** @type {import('tailwindcss').Config} */

// Theme colors are CSS variables (RGB channels) defined in styles/globals.css
// for :root (dark) and html.light, so every existing utility class
// (bg-base-900/60, text-slate-300, border-white/5, ...) follows the active theme.
const v = (name) => `rgb(var(--${name}) / <alpha-value>)`;
const scale = (name, steps) => Object.fromEntries(steps.map((s) => [s, v(`${name}-${s}`)]));

export default {
  content: ["./index.html", "./src/**/*.{js,ts,jsx,tsx}"],
  darkMode: "class",
  theme: {
    extend: {
      colors: {
        base: scale("base", [950, 900, 800, 700, 600]),
        neon: {
          blue: v("neon-blue"),
          purple: v("neon-purple"),
          emerald: v("neon-emerald"),
          rose: v("neon-rose"),
        },
        slate: scale("slate", [50, 100, 200, 300, 400, 500, 600, 700, 800, 900]),
        amber: scale("amber", [300]),
        // "white" is only used for translucent overlays/borders (bg-white/5,
        // border-white/10); in light mode those need to be dark tints instead.
        white: v("overlay"),
        onaccent: "#ffffff",
      },
      boxShadow: {
        glow: "0 0 24px rgb(var(--neon-blue) / var(--glow-strength))",
        "glow-purple": "0 0 24px rgb(var(--neon-purple) / var(--glow-strength))",
        "glow-emerald": "0 0 24px rgb(var(--neon-emerald) / var(--glow-strength))",
      },
      backdropBlur: {
        xs: "2px",
      },
      fontFamily: {
        sans: ["Inter", "system-ui", "sans-serif"],
        mono: ["JetBrains Mono", "monospace"],
      },
    },
  },
  plugins: [],
};
