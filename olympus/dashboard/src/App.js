import React, { useState, useCallback } from 'react';
import LayoutShell from './components/LayoutShell';
import Overwatch from './components/Overwatch';
import FleetPanel from './components/FleetPanel';
import DetectionPanel from './components/DetectionPanel';
import CommandBar from './components/CommandBar';
import ErrorBoundary from './components/ErrorBoundary';
import LoadingScreen from './components/LoadingScreen';
import LocationPrompt from './components/LocationPrompt';
import { useZenohPolling } from './hooks/useZenohPolling';
import './App.css';

function App() {
  const [loaded, setLoaded] = useState(false);
  const [locationSet, setLocationSet] = useState(false);

  const zenohUrl = process.env.REACT_APP_ZENOH_URL || 'http://localhost:8000';
  useZenohPolling(zenohUrl);

  const handleLoadComplete = useCallback(() => setLoaded(true), []);
  const handleLocationComplete = useCallback(() => setLocationSet(true), []);

  return (
    <div className="App">
      {!loaded ? (
        <LoadingScreen onComplete={handleLoadComplete} />
      ) : !locationSet ? (
        <LocationPrompt onComplete={handleLocationComplete} />
      ) : (
        <ErrorBoundary fallbackMessage="Error loading map interface. Please refresh the page.">
          <LayoutShell
            leftSidebar={<DetectionPanel />}
            rightSidebar={<FleetPanel />}
            bottomBar={<CommandBar />}
          >
            <Overwatch />
          </LayoutShell>
        </ErrorBoundary>
      )}
    </div>
  );
}

export default App;
