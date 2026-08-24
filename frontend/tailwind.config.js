/** @type {import('tailwindcss').Config} */
export default {
  // Scoped, additive setup: QuantDeck's existing UI is a hand-rolled CSS
  // design system (src/styles/theme.css — see docs/FRONTEND_SPEC.md, marked
  // "frozen base, do not modify"). Tailwind is layered ONLY for components
  // that opt into it (currently: components/ui/*, the homepage hero).
  // Preflight is disabled in src/styles/tailwind.css (no `@tailwind base;`)
  // so Tailwind never resets global element styles used elsewhere in the app.
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  // "class" (not "media"): QuantDeck's theme is permanently dark regardless
  // of OS preference (index.html sets class="dark" on <html> to match), so
  // Tailwind's dark: variant should follow that class, not prefers-color-scheme.
  darkMode: "class",
  theme: {
    extend: {},
  },
  corePlugins: {
    preflight: false,
  },
  plugins: [],
};
