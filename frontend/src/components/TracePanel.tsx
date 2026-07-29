import React, { useState } from 'react';
import type { ToolCall } from '../types';

interface Props {
  trace: ToolCall[];
}

export default function TracePanel({ trace }: Props) {
  const [expandedIdx, setExpandedIdx] = useState<number | null>(null);

  if (trace.length === 0) {
    return null;
  }

  return (
    <div style={{ padding: 16 }}>
      <h3 style={{ fontSize: 14, fontWeight: 600, marginBottom: 12 }}>
        Agent Trace ({trace.length} tool call{trace.length !== 1 ? 's' : ''})
      </h3>
      {trace.map((tc, i) => (
        <div
          key={i}
          style={{
            marginBottom: 8,
            border: '1px solid #e0e0e0',
            borderRadius: 6,
            overflow: 'hidden',
          }}
        >
          <button
            onClick={() => setExpandedIdx(expandedIdx === i ? null : i)}
            style={{
              width: '100%',
              padding: '8px 12px',
              background: '#fafafa',
              border: 'none',
              cursor: 'pointer',
              textAlign: 'left',
              fontSize: 13,
              fontFamily: 'monospace',
            }}
          >
            {expandedIdx === i ? '\u25BC' : '\u25B6'}{' '}
            <strong>{tc.tool}</strong>({JSON.stringify(tc.args)})
          </button>
          {expandedIdx === i && (
            <pre
              style={{
                padding: 12,
                fontSize: 12,
                background: '#f5f5f5',
                overflow: 'auto',
                maxHeight: 300,
                margin: 0,
                whiteSpace: 'pre-wrap',
                wordBreak: 'break-word',
              }}
            >
              {tc.result_preview}
            </pre>
          )}
        </div>
      ))}
    </div>
  );
}
