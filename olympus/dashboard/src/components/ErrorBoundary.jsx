/**
 * CERES OS - Error Boundary
 * Catches React errors and prevents complete app crashes
 */

import React from 'react';

class ErrorBoundary extends React.Component {
  constructor(props) {
    super(props);
    this.state = { hasError: false, error: null };
  }

  static getDerivedStateFromError(error) {
    return { hasError: true, error };
  }

  componentDidCatch(error, errorInfo) {
    console.error('[CERES] Error Boundary caught an error:', error, errorInfo);
  }

  render() {
    if (this.state.hasError) {
      return (
        <div className="w-screen h-screen bg-gray-900 flex flex-col items-center justify-center">
          <div className="text-center max-w-md">
            <div className="w-24 h-24 mx-auto border-4 border-red-500 rounded-full flex items-center justify-center">
              <span className="text-4xl text-red-500">⚠</span>
            </div>

            <h1 className="mt-8 text-3xl font-bold tracking-widest text-red-400">
              SYSTEM ERROR
            </h1>

            <p className="mt-4 text-gray-400 text-sm">
              {this.props.fallbackMessage || 'An error occurred while loading the map interface.'}
            </p>

            {this.state.error && (
              <div className="mt-4 p-4 bg-red-900/20 border border-red-500/30 rounded text-xs text-left">
                <p className="text-red-300 font-mono break-words">
                  {this.state.error.toString()}
                </p>
              </div>
            )}

            <button
              onClick={() => window.location.reload()}
              className="mt-6 px-6 py-2 text-cyan-400 border border-cyan-500 rounded hover:bg-cyan-500/20 transition"
            >
              Reload Application
            </button>
          </div>
        </div>
      );
    }

    return this.props.children;
  }
}

export default ErrorBoundary;
