/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{js,jsx}'],
  theme: {
    extend: {
      colors: {
        bg:       '#0d1117',
        surface:  '#161b22',
        border:   '#30363d',
        text:     '#e6edf3',
        muted:    '#8b949e',
        bull:     '#3fb950',
        bear:     '#f85149',
        neutral:  '#d29922',
        accent:   '#58a6ff',
      }
    }
  },
  plugins: []
}
