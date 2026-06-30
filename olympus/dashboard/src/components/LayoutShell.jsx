/**
 * CERES OS - Layout Shell
 * Composable panel layout manager with collapsible sidebars
 * Inspired by Palantir Gotham / ATAK common operating picture layout
 */

import React, { useState } from 'react';
import HeaderBar from './HeaderBar';

const LayoutShell = ({ children, leftSidebar, rightSidebar, bottomBar }) => {
  const [leftOpen, setLeftOpen] = useState(true);
  const [rightOpen, setRightOpen] = useState(true);

  return (
    <div className="flex flex-col h-screen bg-gotham-bg-primary overflow-hidden">
      {/* Header */}
      <HeaderBar />

      {/* Main Content Area */}
      <div className="flex flex-1 overflow-hidden">
        {/* Left Sidebar */}
        {leftOpen ? (
          <div className="w-sidebar-left flex-shrink-0 bg-gotham-bg-secondary border-r border-gotham-border flex flex-col overflow-hidden animate-slide-right">
            <div className="flex items-center justify-between px-3 py-2 border-b border-gotham-border">
              <span className="gotham-label">Situational Awareness</span>
              <button
                onClick={() => setLeftOpen(false)}
                className="text-gotham-text-tertiary hover:text-gotham-text-secondary transition-colors p-0.5"
                title="Collapse panel"
              >
                <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M11 19l-7-7 7-7" />
                </svg>
              </button>
            </div>
            <div className="flex-1 overflow-hidden">
              {leftSidebar}
            </div>
          </div>
        ) : (
          <SidebarToggle side="left" onClick={() => setLeftOpen(true)} />
        )}

        {/* Map Canvas + Bottom Bar */}
        <div className="flex-1 relative flex flex-col overflow-hidden">
          {/* Map fills available space */}
          <div className="flex-1 relative">
            {children}
          </div>
          {/* Bottom Command Bar */}
          {bottomBar && (
            <div className="flex-shrink-0">
              {bottomBar}
            </div>
          )}
        </div>

        {/* Right Sidebar */}
        {rightOpen ? (
          <div className="w-sidebar-right flex-shrink-0 bg-gotham-bg-secondary border-l border-gotham-border flex flex-col overflow-hidden animate-slide-left">
            <div className="flex items-center justify-between px-3 py-2 border-b border-gotham-border">
              <span className="gotham-label">Fleet Status</span>
              <button
                onClick={() => setRightOpen(false)}
                className="text-gotham-text-tertiary hover:text-gotham-text-secondary transition-colors p-0.5"
                title="Collapse panel"
              >
                <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M13 5l7 7-7 7" />
                </svg>
              </button>
            </div>
            <div className="flex-1 overflow-hidden">
              {rightSidebar}
            </div>
          </div>
        ) : (
          <SidebarToggle side="right" onClick={() => setRightOpen(true)} />
        )}
      </div>
    </div>
  );
};

const SidebarToggle = ({ side, onClick }) => (
  <button
    onClick={onClick}
    className="flex-shrink-0 w-6 bg-gotham-bg-secondary border-gotham-border hover:bg-gotham-bg-tertiary transition-colors flex items-center justify-center cursor-pointer"
    style={{ borderLeft: side === 'right' ? '1px solid #30363d' : 'none', borderRight: side === 'left' ? '1px solid #30363d' : 'none' }}
    title={`Expand ${side} panel`}
  >
    <svg className="w-3 h-3 text-gotham-text-tertiary" fill="none" stroke="currentColor" viewBox="0 0 24 24">
      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2}
        d={side === 'left' ? 'M13 5l7 7-7 7' : 'M11 19l-7-7 7-7'} />
    </svg>
  </button>
);

export default LayoutShell;
