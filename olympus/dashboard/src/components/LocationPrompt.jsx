/**
 * LocationPrompt — Set the operating center on startup.
 *
 * Flow:
 *  1. Show manual entry form immediately (coords or address)
 *  2. Attempt browser geolocation in background — if it succeeds, auto-fill coords
 *  3. "Detect my location" button for explicit retry
 *  4. "Use default" skips relocation and uses the instance's configured center
 */

import React, { useEffect, useState, useCallback, useRef } from 'react';
import useFleetStore from '../store/fleetStore';
import { useInstance } from '../instances';

const LocationPrompt = ({ onComplete }) => {
  const instance = useInstance();
  const setOperatingCenter = useFleetStore((s) => s.setOperatingCenter);

  const [coords, setCoords] = useState('');
  const [address, setAddress] = useState('');
  const [error, setError] = useState(null);
  const [geocoding, setGeocoding] = useState(false);
  const [detecting, setDetecting] = useState(false);
  const [tab, setTab] = useState('coords'); // coords | address
  const completedRef = useRef(false);

  const applyLocation = useCallback((lat, lon) => {
    if (completedRef.current) return;
    completedRef.current = true;
    try {
      setOperatingCenter(lat, lon);
    } catch (e) {
      console.error('[LocationPrompt] setOperatingCenter failed:', e);
    }
    onComplete();
  }, [setOperatingCenter, onComplete]);

  // Attempt background geolocation on mount (silent, no spinner)
  useEffect(() => {
    if (!navigator.geolocation) return;

    navigator.geolocation.getCurrentPosition(
      (position) => {
        if (completedRef.current) return;
        const { latitude, longitude } = position.coords;
        // Auto-fill the coordinates input instead of auto-proceeding
        setCoords(`${latitude.toFixed(6)}, ${longitude.toFixed(6)}`);
      },
      () => {
        // Silently ignore — user already has the manual form
      },
      { enableHighAccuracy: false, timeout: 4000, maximumAge: 300000 }
    );
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const handleDetectLocation = useCallback(() => {
    if (!navigator.geolocation) {
      setError('Geolocation not supported by this browser.');
      return;
    }
    setDetecting(true);
    setError(null);

    navigator.geolocation.getCurrentPosition(
      (position) => {
        const { latitude, longitude } = position.coords;
        setDetecting(false);
        applyLocation(latitude, longitude);
      },
      (err) => {
        setDetecting(false);
        if (err.code === 1) {
          setError('Location access denied. Enter coordinates manually.');
        } else if (err.code === 2) {
          setError('Location unavailable — no GPS on this device. Enter coordinates or an address.');
        } else {
          setError('Location request timed out. Try again or enter manually.');
        }
      },
      { enableHighAccuracy: true, timeout: 8000, maximumAge: 300000 }
    );
  }, [applyLocation]);

  const handleCoordsSubmit = useCallback(() => {
    const parts = coords.split(',').map((s) => s.trim());
    if (parts.length !== 2) {
      setError('Enter coordinates as: latitude, longitude');
      return;
    }
    const lat = parseFloat(parts[0]);
    const lon = parseFloat(parts[1]);
    if (isNaN(lat) || isNaN(lon) || lat < -90 || lat > 90 || lon < -180 || lon > 180) {
      setError('Invalid coordinates. Latitude: -90 to 90, Longitude: -180 to 180');
      return;
    }
    applyLocation(lat, lon);
  }, [coords, applyLocation]);

  const handleAddressSubmit = useCallback(async () => {
    if (!address.trim()) {
      setError('Enter an address or place name');
      return;
    }
    setGeocoding(true);
    setError(null);
    try {
      const response = await fetch(
        `https://nominatim.openstreetmap.org/search?format=json&limit=1&q=${encodeURIComponent(address)}`,
        { headers: { 'User-Agent': 'OlympusOS/1.0' } }
      );
      const results = await response.json();
      if (results.length === 0) {
        setError('Address not found. Try a different search or use coordinates.');
        setGeocoding(false);
        return;
      }
      const lat = parseFloat(results[0].lat);
      const lon = parseFloat(results[0].lon);
      applyLocation(lat, lon);
    } catch (e) {
      setError('Geocoding failed. Check your internet connection or use coordinates.');
      setGeocoding(false);
    }
  }, [address, applyLocation]);

  const handleUseDefault = useCallback(() => {
    if (completedRef.current) return;
    completedRef.current = true;
    onComplete();
  }, [onComplete]);

  return (
    <div className="fixed inset-0 z-[9999] bg-gotham-bg-primary/95 flex items-center justify-center">
      <div className="bg-gotham-bg-secondary border border-gotham-border rounded-lg shadow-2xl w-full max-w-md mx-4">
        {/* Header */}
        <div className="px-5 py-4 border-b border-gotham-border">
          <h2 className="text-base font-semibold text-gotham-text-primary">
            Set Operating Location
          </h2>
          <p className="text-xs text-gotham-text-tertiary mt-1">
            Position the fleet map at your operating area.
          </p>
        </div>

        {/* Detect button */}
        <div className="px-5 pt-4 pb-2">
          <button
            onClick={handleDetectLocation}
            disabled={detecting}
            className="w-full flex items-center justify-center gap-2 px-4 py-2.5 text-sm font-medium bg-gotham-accent-blue/10 text-gotham-accent-blue border border-gotham-accent-blue/20 rounded hover:bg-gotham-accent-blue/20 transition-colors disabled:opacity-50"
          >
            {detecting ? (
              <>
                <span className="w-4 h-4 border-2 border-gotham-accent-blue border-t-transparent rounded-full animate-spin" />
                Detecting...
              </>
            ) : (
              <>
                <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                  <path strokeLinecap="round" strokeLinejoin="round" d="M17.657 16.657L13.414 20.9a1.998 1.998 0 01-2.827 0l-4.244-4.243a8 8 0 1111.314 0z" />
                  <path strokeLinecap="round" strokeLinejoin="round" d="M15 11a3 3 0 11-6 0 3 3 0 016 0z" />
                </svg>
                Detect my location
              </>
            )}
          </button>
        </div>

        {/* Divider */}
        <div className="px-5 py-2 flex items-center gap-3">
          <div className="flex-1 border-t border-gotham-border" />
          <span className="text-[10px] text-gotham-text-tertiary uppercase tracking-wider">or enter manually</span>
          <div className="flex-1 border-t border-gotham-border" />
        </div>

        {/* Tab switcher */}
        <div className="flex border-b border-gotham-border mx-5 rounded-t overflow-hidden">
          <button
            onClick={() => { setTab('coords'); setError(null); }}
            className={`flex-1 px-4 py-2 text-xs font-medium transition-colors ${
              tab === 'coords'
                ? 'text-gotham-accent-blue border-b-2 border-gotham-accent-blue bg-gotham-accent-blue/5'
                : 'text-gotham-text-tertiary hover:text-gotham-text-secondary'
            }`}
          >
            GPS Coordinates
          </button>
          <button
            onClick={() => { setTab('address'); setError(null); }}
            className={`flex-1 px-4 py-2 text-xs font-medium transition-colors ${
              tab === 'address'
                ? 'text-gotham-accent-blue border-b-2 border-gotham-accent-blue bg-gotham-accent-blue/5'
                : 'text-gotham-text-tertiary hover:text-gotham-text-secondary'
            }`}
          >
            Address / Place
          </button>
        </div>

        {/* Input area */}
        <div className="px-5 py-4">
          {tab === 'coords' ? (
            <div>
              <label className="block text-xs text-gotham-text-tertiary mb-1.5">
                Latitude, Longitude
              </label>
              <input
                type="text"
                value={coords}
                onChange={(e) => { setCoords(e.target.value); setError(null); }}
                onKeyDown={(e) => e.key === 'Enter' && handleCoordsSubmit()}
                placeholder="e.g. 34.0522, -118.2437"
                className="w-full bg-gotham-bg-primary border border-gotham-border-muted rounded px-3 py-2 text-sm text-gotham-text-primary font-mono focus:border-gotham-accent-blue focus:outline-none placeholder:text-gotham-text-tertiary/50"
                autoFocus
              />
              <p className="text-[10px] text-gotham-text-tertiary mt-1.5">
                Decimal degrees. Lat: -90 to 90, Lon: -180 to 180
              </p>
            </div>
          ) : (
            <div>
              <label className="block text-xs text-gotham-text-tertiary mb-1.5">
                Address or Place Name
              </label>
              <input
                type="text"
                value={address}
                onChange={(e) => { setAddress(e.target.value); setError(null); }}
                onKeyDown={(e) => e.key === 'Enter' && handleAddressSubmit()}
                placeholder="e.g. Salinas Valley, CA"
                className="w-full bg-gotham-bg-primary border border-gotham-border-muted rounded px-3 py-2 text-sm text-gotham-text-primary focus:border-gotham-accent-blue focus:outline-none placeholder:text-gotham-text-tertiary/50"
                autoFocus
              />
              <p className="text-[10px] text-gotham-text-tertiary mt-1.5">
                Uses OpenStreetMap geocoding (requires internet)
              </p>
            </div>
          )}

          {error && (
            <p className="text-xs text-gotham-accent-red mt-2">{error}</p>
          )}
        </div>

        {/* Actions */}
        <div className="px-5 py-3 border-t border-gotham-border flex items-center justify-between">
          <button
            onClick={handleUseDefault}
            className="text-xs text-gotham-text-tertiary hover:text-gotham-text-secondary transition-colors"
          >
            Use default ({instance.operatingArea.center.lat.toFixed(2)}, {instance.operatingArea.center.lon.toFixed(2)})
          </button>
          <button
            onClick={tab === 'coords' ? handleCoordsSubmit : handleAddressSubmit}
            disabled={geocoding}
            className="px-4 py-1.5 text-xs font-medium bg-gotham-accent-blue/20 text-gotham-accent-blue rounded hover:bg-gotham-accent-blue/30 transition-colors disabled:opacity-50"
          >
            {geocoding ? 'Locating...' : 'Set Location'}
          </button>
        </div>
      </div>
    </div>
  );
};

export default LocationPrompt;
