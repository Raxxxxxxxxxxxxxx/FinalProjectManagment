/** @type {import('tailwindcss').Config} */
module.exports = {
  darkMode: "class",
  content: [
    "./templates/**/*.html",
    "./accounts/**/*.py",
    "./marketplace/**/*.py",
    "./static/js/**/*.js"
  ],
  theme: {
    extend: {
      colors: {
        university: {
          50: "#f5f8ff",
          100: "#e7eef8",
          200: "#cad9eb",
          300: "#9fb9d8",
          400: "#668cb8",
          500: "#315f91",
          600: "#183b65",
          700: "#12345d",
          800: "#082c58",
          900: "#061f40"
        },
        brand: {
          50: "#f5f8ff",
          100: "#e7eef8",
          200: "#cad9eb",
          300: "#9fb9d8",
          400: "#668cb8",
          500: "#315f91",
          600: "#183b65",
          700: "#12345d",
          800: "#082c58",
          900: "#061f40"
        },
        gold: {
          50: "#fbf8f2",
          100: "#f3eadb",
          200: "#e5d3b6",
          300: "#d5b88b",
          400: "#c49f69",
          500: "#b9955c",
          600: "#a37f48",
          700: "#85653b"
        },
        ink: "#082c58"
      },
      boxShadow: {
        soft: "0 18px 50px rgba(8, 44, 88, .10)",
        academic: "0 24px 70px rgba(8, 44, 88, .16)"
      }
    }
  },
  plugins: []
};
