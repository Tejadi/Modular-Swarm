/** @type {import('tailwindcss').Config} */
module.exports = {
  content: [
    "./src/**/*.{js,jsx,ts,tsx}",
    "./public/index.html",
  ],
  theme: {
    extend: {
      colors: {
        gotham: {
          'bg-primary': '#0d1117',
          'bg-secondary': '#161b22',
          'bg-tertiary': '#1c2128',
          'bg-elevated': '#21262d',
          'border': '#30363d',
          'border-muted': '#21262d',
          'text-primary': '#e6edf3',
          'text-secondary': '#8b949e',
          'text-tertiary': '#484f58',
          'accent-blue': '#388bfd',
          'accent-green': '#3fb950',
          'accent-yellow': '#d29922',
          'accent-orange': '#db6d28',
          'accent-red': '#f85149',
          'accent-purple': '#a371f7',
          'accent-teal': '#39d2c0',
        },
      },
      fontFamily: {
        mono: ['JetBrains Mono', 'SF Mono', 'Menlo', 'Consolas', 'monospace'],
        display: ['Inter', '-apple-system', 'BlinkMacSystemFont', 'sans-serif'],
      },
      fontSize: {
        'data': ['12px', { lineHeight: '16px' }],
        'data-sm': ['11px', { lineHeight: '14px' }],
        'ui': ['13px', { lineHeight: '18px' }],
      },
      animation: {
        'subtle-pulse': 'subtlePulse 2s ease-in-out infinite',
        'slide-up': 'slideUp 0.3s ease-out',
        'slide-down': 'slideDown 0.3s ease-out',
        'slide-left': 'slideLeft 0.2s ease-out',
        'slide-right': 'slideRight 0.2s ease-out',
        'fade-in': 'fadeIn 0.3s ease-out',
        'progress': 'progress 8s linear',
        'status-ring': 'statusRing 2s ease-out infinite',
        'spin-slow': 'spin 8s linear infinite',
      },
      keyframes: {
        subtlePulse: {
          '0%, 100%': { opacity: '1' },
          '50%': { opacity: '0.6' },
        },
        slideUp: {
          '0%': { transform: 'translateY(12px)', opacity: '0' },
          '100%': { transform: 'translateY(0)', opacity: '1' },
        },
        slideDown: {
          '0%': { transform: 'translateY(-12px)', opacity: '0' },
          '100%': { transform: 'translateY(0)', opacity: '1' },
        },
        slideLeft: {
          '0%': { transform: 'translateX(12px)', opacity: '0' },
          '100%': { transform: 'translateX(0)', opacity: '1' },
        },
        slideRight: {
          '0%': { transform: 'translateX(-12px)', opacity: '0' },
          '100%': { transform: 'translateX(0)', opacity: '1' },
        },
        fadeIn: {
          '0%': { opacity: '0' },
          '100%': { opacity: '1' },
        },
        progress: {
          '0%': { width: '100%' },
          '100%': { width: '0%' },
        },
        statusRing: {
          '0%': { transform: 'scale(1)', opacity: '0.6' },
          '100%': { transform: 'scale(2.5)', opacity: '0' },
        },
      },
      boxShadow: {
        'glow-green': '0 0 6px rgba(63, 185, 80, 0.4)',
        'glow-yellow': '0 0 6px rgba(210, 153, 34, 0.4)',
        'glow-red': '0 0 6px rgba(248, 81, 73, 0.5)',
        'glow-blue': '0 0 8px rgba(56, 139, 253, 0.3)',
        'glow-teal': '0 0 6px rgba(57, 210, 192, 0.3)',
        'glow-orange': '0 0 6px rgba(219, 109, 40, 0.3)',
        'panel': '0 2px 8px rgba(0, 0, 0, 0.4)',
        'elevated': '0 4px 16px rgba(0, 0, 0, 0.5)',
      },
      spacing: {
        'header': '48px',
        'sidebar-left': '320px',
        'sidebar-right': '300px',
        'command-bar': '56px',
      },
    },
  },
  plugins: [],
}
