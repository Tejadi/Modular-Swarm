import React, { useState, useEffect } from 'react';

const LoadingScreen = ({ onComplete }) => {
  const [progress, setProgress] = useState(0);
  const [status, setStatus] = useState('Initializing core systems...');
  const [fadeOut, setFadeOut] = useState(false);

  useEffect(() => {
    const stages = [
      { at: 15, msg: 'Loading geospatial engine...' },
      { at: 35, msg: 'Connecting to fleet mesh...' },
      { at: 55, msg: 'Initializing Voronoi partitioner...' },
      { at: 75, msg: 'Loading mission protocols...' },
      { at: 90, msg: 'Systems nominal. Standing by.' },
    ];

    let current = 0;
    const interval = setInterval(() => {
      current += Math.random() * 4 + 1;
      if (current >= 100) {
        current = 100;
        clearInterval(interval);
        setTimeout(() => {
          setFadeOut(true);
          setTimeout(() => onComplete?.(), 600);
        }, 400);
      }
      setProgress(Math.min(current, 100));

      const stage = stages.find((s) => current >= s.at && current < s.at + 15);
      if (stage) setStatus(stage.msg);
    }, 60);

    return () => clearInterval(interval);
  }, [onComplete]);

  return (
    <div
      className={`fixed inset-0 z-[9999] flex flex-col items-center justify-center transition-opacity duration-500 ${
        fadeOut ? 'opacity-0' : 'opacity-100'
      }`}
      style={{ backgroundColor: '#0d1117' }}
    >
      <div
        className="absolute inset-0 opacity-[0.03]"
        style={{
          backgroundImage: `
            linear-gradient(rgba(56,139,253,0.3) 1px, transparent 1px),
            linear-gradient(90deg, rgba(56,139,253,0.3) 1px, transparent 1px)
          `,
          backgroundSize: '40px 40px',
        }}
      />

      <div
        className="absolute inset-x-0 h-px opacity-20"
        style={{
          background: 'linear-gradient(90deg, transparent, #388bfd, transparent)',
          top: `${(progress / 100) * 100}%`,
          transition: 'top 0.3s ease-out',
        }}
      />

      <div className="relative z-10 flex flex-col items-center">
        <div className="mb-8 relative">
          <svg width="80" height="80" viewBox="0 0 80 80" className="animate-spin-slow">
            <circle
              cx="40" cy="40" r="36"
              fill="none"
              stroke="#388bfd"
              strokeWidth="1"
              strokeDasharray="8 4"
              opacity="0.4"
            />
          </svg>
          <svg
            width="48" height="48"
            viewBox="0 0 100 100"
            className="absolute top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2"
          >
            <polygon points="50,15 85,75 15,75" fill="none" stroke="#388bfd" strokeWidth="3" strokeLinejoin="round" opacity="0.8" />
            <circle cx="50" cy="15" r="4" fill="#388bfd" opacity="0.9">
              <animate attributeName="opacity" values="0.5;1;0.5" dur="2s" repeatCount="indefinite" />
            </circle>
            <ellipse cx="50" cy="65" rx="30" ry="10" fill="none" stroke="#388bfd" strokeWidth="1.5" opacity="0.3" />
          </svg>
        </div>

        <div className="text-center mb-6">
          <h1
            className="text-2xl font-light tracking-[0.3em] uppercase mb-1"
            style={{ color: '#e6edf3' }}
          >
            OLYMPUS
          </h1>
          <div
            className="text-[10px] tracking-[0.25em] uppercase"
            style={{ color: '#484f58' }}
          >
            Autonomous Fleet Command & Control
          </div>
        </div>

        <div
          className="px-3 py-1 rounded-full border mb-8 text-[10px] tracking-wider uppercase"
          style={{
            borderColor: '#21262d',
            color: '#484f58',
          }}
        >
          v0.1.0 // OVERWATCH
        </div>

        <div className="w-64 mb-3">
          <div
            className="h-[2px] rounded-full overflow-hidden"
            style={{ backgroundColor: '#161b22' }}
          >
            <div
              className="h-full rounded-full transition-all duration-200 ease-out"
              style={{
                width: `${progress}%`,
                background: 'linear-gradient(90deg, #388bfd, #39d2c0)',
              }}
            />
          </div>
        </div>

        <div className="text-center">
          <span
            className="text-[11px] font-mono tracking-wide"
            style={{ color: '#484f58' }}
          >
            {status}
          </span>
        </div>

        <div className="mt-12 flex items-center gap-3">
          <div className="w-8 h-px" style={{ backgroundColor: '#21262d' }} />
          <span
            className="text-[9px] tracking-[0.2em] uppercase"
            style={{ color: '#30363d' }}
          >
            UNCLASSIFIED // FOR AUTHORIZED USE ONLY
          </span>
          <div className="w-8 h-px" style={{ backgroundColor: '#21262d' }} />
        </div>
      </div>
    </div>
  );
};

export default LoadingScreen;
