/** @type {import('tailwindcss').Config} */
module.exports = {
  content: {
    files: ['./hub/static/index.html', './hub/static/app.js'],
    transform: {
      // Ignore generated assets to prevent the CSS from scanning itself.
      html: content => content.replace(/<!-- hub:(styles|script):start -->[\s\S]*?<!-- hub:\1:end -->/g, ''),
    },
  },
  theme: {
    extend: {
      fontFamily: {
        sans: ['Inter', '-apple-system', 'BlinkMacSystemFont', 'Segoe UI', 'Noto Sans JP', 'sans-serif'],
      },
    },
  },
  plugins: [],
};
