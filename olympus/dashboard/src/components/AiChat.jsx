import React, { useState, useRef, useEffect, useCallback } from 'react';
import { useInstance } from '../instances';

const API_BASE = process.env.REACT_APP_VEHICLE_API_URL || 'http://localhost:3001';
const API_TOKEN = process.env.REACT_APP_CERES_API_KEY || '';

const AiChat = () => {
  const instance = useInstance();
  const [messages, setMessages] = useState([
    {
      role: 'assistant',
      content: instance.advisor.greeting,
    },
  ]);
  const [input, setInput] = useState('');
  const [loading, setLoading] = useState(false);
  const messagesEndRef = useRef(null);
  const inputRef = useRef(null);

  const scrollToBottom = useCallback(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, []);

  useEffect(() => {
    scrollToBottom();
  }, [messages, scrollToBottom]);

  const sendMessage = useCallback(async () => {
    const text = input.trim();
    if (!text || loading) return;

    setInput('');
    setMessages((prev) => [...prev, { role: 'user', content: text }]);
    setLoading(true);

    try {
      const headers = { 'Content-Type': 'application/json' };
      if (API_TOKEN) {
        headers['Authorization'] = `Bearer ${API_TOKEN}`;
      }

      const res = await fetch(`${API_BASE}/api/v1/advisor/chat`, {
        method: 'POST',
        headers,
        body: JSON.stringify({ message: text }),
      });

      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(err.error || `HTTP ${res.status}`);
      }

      const data = await res.json();
      setMessages((prev) => [
        ...prev,
        { role: 'assistant', content: data.response || 'No response received.' },
      ]);
    } catch (err) {
      setMessages((prev) => [
        ...prev,
        {
          role: 'error',
          content: `Connection error: ${err.message}. Ensure the Vehicle API and Python brain are running.`,
        },
      ]);
    } finally {
      setLoading(false);
      inputRef.current?.focus();
    }
  }, [input, loading]);

  const handleKeyDown = useCallback(
    (e) => {
      if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        sendMessage();
      }
    },
    [sendMessage]
  );

  const quickActions = instance.advisor.quickActions;

  return (
    <div className="flex flex-col h-full">
      <div className="flex-1 overflow-y-auto scrollbar-thin px-3 py-2 space-y-2">
        {messages.map((msg, i) => (
          <ChatBubble key={i} message={msg} persona={instance.advisor.persona} />
        ))}

        {loading && (
          <div className="flex items-center gap-2 px-3 py-2">
            <div className="flex gap-1">
              <span className="w-1.5 h-1.5 rounded-full bg-gotham-accent-blue animate-bounce" style={{ animationDelay: '0ms' }} />
              <span className="w-1.5 h-1.5 rounded-full bg-gotham-accent-blue animate-bounce" style={{ animationDelay: '150ms' }} />
              <span className="w-1.5 h-1.5 rounded-full bg-gotham-accent-blue animate-bounce" style={{ animationDelay: '300ms' }} />
            </div>
            <span className="text-data-sm text-gotham-text-tertiary">Thinking...</span>
          </div>
        )}

        <div ref={messagesEndRef} />
      </div>

      {messages.length <= 2 && (
        <div className="px-3 py-1.5 border-t border-gotham-border-muted flex flex-wrap gap-1">
          {quickActions.map((action) => (
            <button
              key={action.label}
              onClick={() => {
                setInput(action.msg);
                setTimeout(() => {
                  setInput(action.msg);
                  sendMessage();
                }, 50);
              }}
              className="px-2 py-0.5 text-[10px] rounded border border-gotham-border-muted text-gotham-accent-blue hover:bg-gotham-accent-blue/10 transition-all"
            >
              {action.label}
            </button>
          ))}
        </div>
      )}

      <div className="px-3 py-2 border-t border-gotham-border bg-gotham-bg-tertiary/30">
        <div className="flex gap-2">
          <input
            ref={inputRef}
            type="text"
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder="Ask the advisor..."
            disabled={loading}
            className="flex-1 bg-gotham-bg-primary border border-gotham-border rounded px-2.5 py-1.5 text-data text-gotham-text-primary placeholder:text-gotham-text-tertiary focus:border-gotham-accent-blue focus:outline-none transition-colors disabled:opacity-50"
          />
          <button
            onClick={sendMessage}
            disabled={loading || !input.trim()}
            className="px-3 py-1.5 rounded bg-gotham-accent-blue/20 text-gotham-accent-blue text-data-sm font-medium hover:bg-gotham-accent-blue/30 disabled:opacity-40 disabled:cursor-not-allowed transition-all"
          >
            Send
          </button>
        </div>
      </div>
    </div>
  );
};

const ChatBubble = ({ message, persona }) => {
  const isUser = message.role === 'user';
  const isError = message.role === 'error';

  return (
    <div className={`flex ${isUser ? 'justify-end' : 'justify-start'}`}>
      <div
        className={`max-w-[90%] rounded px-3 py-2 text-data ${
          isUser
            ? 'bg-gotham-accent-blue/15 text-gotham-text-primary border border-gotham-accent-blue/20'
            : isError
            ? 'bg-gotham-accent-red/10 text-gotham-accent-red border border-gotham-accent-red/20'
            : 'bg-gotham-bg-tertiary text-gotham-text-secondary border border-gotham-border-muted'
        }`}
      >
        {!isUser && !isError && (
          <div className="text-[9px] uppercase tracking-wider text-gotham-accent-blue mb-1 font-medium">
            CERES {persona}
          </div>
        )}
        <div className="whitespace-pre-wrap text-data-sm leading-relaxed">{message.content}</div>
      </div>
    </div>
  );
};

export default AiChat;
