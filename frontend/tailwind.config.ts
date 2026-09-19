import type { Config } from "tailwindcss";

// Noblen brand direction: professional, blue-led palette (avoid heavy gold/yellow).
const config: Config = {
  content: ["./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        noblen: {
          50: "#eef4ff",
          100: "#d9e6ff",
          200: "#bcd2ff",
          300: "#8eb4ff",
          400: "#598cff",
          500: "#3366ff",
          600: "#1f47f5",
          700: "#1a37d8",
          800: "#1b30ae",
          900: "#1c2f89",
          950: "#131c4f",
        },
      },
      fontFamily: {
        sans: ["var(--font-sans)", "system-ui", "sans-serif"],
      },
    },
  },
  plugins: [],
};

export default config;
