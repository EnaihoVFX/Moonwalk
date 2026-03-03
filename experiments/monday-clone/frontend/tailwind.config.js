/** @type {import('tailwindcss').Config} */
export default {
  content: [
    "./index.html",
    "./src/**/*.{js,ts,jsx,tsx}",
  ],
  theme: {
    extend: {
      colors: {
        'monday-blue': '#0073ea',
        'monday-bg': '#f5f6f8',
        'monday-border': '#e6e9ef',
        'monday-text': '#323338',
      }
    },
  },
  plugins: [],
}
