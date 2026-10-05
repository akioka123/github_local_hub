/** @type {import('tailwindcss').Config} */
module.exports = {
  content: ['./hub/static/index.html', './hub/static/app.js'],
  theme: {
    extend: {
      fontFamily: {
        sans: ['Inter', '-apple-system', 'BlinkMacSystemFont', 'Segoe UI', 'Noto Sans JP', 'sans-serif'],
      },
    },
  },
  plugins: [],
};
