import React from 'react';
import { Prism as SyntaxHighlighter } from 'react-syntax-highlighter';
import { vscDarkPlus } from 'react-syntax-highlighter/dist/esm/styles/prism';

export default function CodeViewer({ content, chunks, onChunkClick }) {
    if (!content) return null;
    const lines = content.split('\n');

    // Calculate byte offset for each line
    const lineOffsets = [];
    let currentOffset = 0;
    lines.forEach(line => {
        lineOffsets.push(currentOffset);
        currentOffset += line.length + 1; // +1 for newline
    });

    return (
        <div className="relative font-mono text-sm bg-[#1e1e1e] min-h-full p-4 overflow-auto">
            {lines.map((line, i) => {
                const lineNum = i + 1;
                const lineStart = lineOffsets[i];
                const lineEnd = lineStart + line.length;

                // Find chunks that overlap with this line using byte ranges
                const activeChunks = chunks.filter(c => {
                    if (c.byte_start === undefined || c.byte_end === undefined) {
                        // Fallback to line numbers if bytes missing
                        return lineNum >= c.start_line && lineNum <= c.end_line;
                    }
                    // Check overlap: chunk starts before line ends AND chunk ends after line starts
                    return c.byte_start < lineEnd && c.byte_end > lineStart;
                });

                // Determine style based on chunk type (metadata)
                // If multiple chunks, pick the smallest one (most specific) by byte length
                activeChunks.sort((a, b) => {
                    const lenA = (a.byte_end - a.byte_start) || (a.end_line - a.start_line);
                    const lenB = (b.byte_end - b.byte_start) || (b.end_line - b.start_line);
                    return lenA - lenB;
                });
                const topChunk = activeChunks[0];

                let bgClass = '';
                let borderClass = '';
                let label = null;
                let isStart = false;
                let isEnd = false;

                if (topChunk) {
                    // Extract type from semantic_matches if available
                    let type = 'unknown';
                    let labels = [];

                    if (topChunk.metadata?.semantic_matches?.length > 0) {
                        // Use all matches
                        labels = topChunk.metadata.semantic_matches.map(m => m.label || m.value);
                        type = topChunk.metadata.semantic_matches[0].value || 'unknown';
                    } else if (topChunk.metadata?.type) {
                        type = topChunk.metadata.type;
                        labels = [type];
                    } else {
                        labels = ['CHUNK'];
                    }

                    // Add tags if available
                    if (topChunk.metadata?.tags?.length > 0) {
                        labels = [...labels, ...topChunk.metadata.tags];
                    }

                    // Stronger symbolic colors based on type
                    if (type.includes('class')) {
                        bgClass = 'bg-blue-900/40';
                        borderClass = 'border-l-4 border-blue-500';
                    } else if (type.includes('function') || type.includes('method')) {
                        bgClass = 'bg-green-900/40';
                        borderClass = 'border-l-4 border-green-500';
                    } else if (type.includes('import')) {
                        bgClass = 'bg-purple-900/40';
                        borderClass = 'border-l-4 border-purple-500';
                    } else {
                        bgClass = 'bg-gray-700/40';
                        borderClass = 'border-l-4 border-gray-500';
                    }

                    if (topChunk.byte_start !== undefined) {
                        // Exact byte check for start/end lines
                        // A line is the start if the chunk starts within this line
                        isStart = (topChunk.byte_start >= lineStart && topChunk.byte_start < lineEnd);
                        // A line is the end if the chunk ends within this line (or exactly at the end)
                        isEnd = (topChunk.byte_end > lineStart && topChunk.byte_end <= lineEnd);

                        // Special case: if chunk spans multiple lines, we might want to mark the first line it touches as start
                        // The overlap logic ensures we only see lines touched.
                        // But strictly, "start line" is the line containing byte_start.
                    } else {
                        isStart = (lineNum === topChunk.start_line);
                        isEnd = (lineNum === topChunk.end_line);
                    }

                    // Always show labels on the first line of the chunk (using start_line for robustness)
                    if (lineNum === topChunk.start_line) {
                        label = (
                            <div className="absolute right-4 top-0 flex flex-col gap-1 items-end z-20">
                                {labels.map((l, idx) => (
                                    <span key={idx} className="text-[10px] uppercase font-bold opacity-90 px-2 py-0.5 rounded bg-black/60 text-white shadow-sm whitespace-nowrap">
                                        {l.replace(/_/g, ' ')}
                                    </span>
                                ))}
                            </div>
                        );
                    }
                }

                // Add separation borders
                const topBorder = isStart ? 'border-t border-white/10 mt-1' : '';
                const bottomBorder = isEnd ? 'border-b border-white/10 mb-1' : '';

                return (
                    <div
                        key={i}
                        className={`flex relative hover:bg-white/5 cursor-pointer ${bgClass} ${borderClass} ${topBorder} ${bottomBorder}`}
                        onClick={() => topChunk && onChunkClick(topChunk)}
                    >
                        <span className="w-12 text-gray-500 text-right mr-4 select-none shrink-0 pt-0.5">{lineNum}</span>
                        <pre className="flex-1 whitespace-pre-wrap break-all m-0 text-gray-200 relative z-10 pointer-events-none font-mono leading-relaxed">
                            {line}
                        </pre>
                        {label}
                    </div>
                );
            })}
        </div>
    );
}
