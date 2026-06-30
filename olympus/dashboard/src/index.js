/**
 * CERES OS - React Entry Point
 */

import React from 'react';
import ReactDOM from 'react-dom/client';
import App from './App';

// Initialize Cesium
import 'cesium/Build/Cesium/Widgets/widgets.css';

// Set Cesium base URL for assets
window.CESIUM_BASE_URL = '/cesium';

// Cesium Ion token removed - using default open-source tiles
// For production, get a free token at: https://cesium.com/ion/tokens

const root = ReactDOM.createRoot(document.getElementById('root'));
root.render(
  // StrictMode disabled - causes issues with Cesium/Resium rendering
  <App />
);
