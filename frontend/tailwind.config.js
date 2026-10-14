/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{js,jsx}'],
  theme: {
    extend: {
      fontFamily: {
        sans: ['Manrope', 'sans-serif'],
        mono: ['IBM Plex Mono', 'monospace'],
      },
      colors: {
        ink: '#172326',
        paper: '#f2f5f3',
        signal: '#e65e32',
        mint: '#a8e3c0',
      },
    },
  },
  plugins: [],
}
