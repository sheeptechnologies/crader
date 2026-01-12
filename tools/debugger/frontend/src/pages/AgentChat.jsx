import React, { useState, useEffect, useRef } from 'react';
import { useParams, Link } from 'react-router-dom';
import { ArrowLeft, Send, Bot, User, Terminal, Loader2 } from 'lucide-react';
import ReactMarkdown from 'react-markdown';

const API_BASE = 'http://localhost:8019/api';

export default function AgentChat() {
    const { repoId } = useParams();
    const [messages, setMessages] = useState([]);
    const [input, setInput] = useState('');
    const [loading, setLoading] = useState(false);
    const [threadId] = useState(() => `thread-${Math.random().toString(36).substr(2, 9)}`);
    const messagesEndRef = useRef(null);

    const scrollToBottom = () => {
        messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
    };

    useEffect(() => {
        scrollToBottom();
    }, [messages]);

    const handleSubmit = async (e) => {
        e.preventDefault();
        if (!input.trim() || loading) return;

        const userMsg = { type: 'user', content: input };
        setMessages(prev => [...prev, userMsg]);
        setInput('');
        setLoading(true);

        try {
            const response = await fetch(`${API_BASE}/agent/chat`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    repo_id: repoId,
                    message: userMsg.content,
                    thread_id: threadId
                })
            });

            const reader = response.body.getReader();
            const decoder = new TextDecoder();
            let buffer = '';

            // Placeholder for AI response
            setMessages(prev => [...prev, { type: 'ai', content: '', toolCalls: [] }]);

            while (true) {
                const { done, value } = await reader.read();
                if (done) break;

                buffer += decoder.decode(value, { stream: true });
                const lines = buffer.split('\n');
                buffer = lines.pop(); // Keep incomplete line

                for (const line of lines) {
                    if (!line.trim()) continue;
                    try {
                        const event = JSON.parse(line);
                        handleEvent(event);
                    } catch (err) {
                        console.error("Error parsing stream:", err);
                    }
                }
            }
        } catch (err) {
            console.error(err);
            setMessages(prev => [...prev, { type: 'error', content: 'Failed to send message.' }]);
        } finally {
            setLoading(false);
        }
    };

    const handleEvent = (event) => {
        setMessages(prev => {
            const newMsgs = [...prev];
            if (newMsgs.length === 0) return newMsgs;

            const lastMsgIndex = newMsgs.length - 1;
            // Create a shallow copy of the last message to avoid mutating state
            const lastMsg = { ...newMsgs[lastMsgIndex] };

            // Also copy toolCalls array if we're going to modify it
            if (lastMsg.toolCalls) {
                lastMsg.toolCalls = [...lastMsg.toolCalls];
            }

            if (event.type === 'tool_call') {
                // Add tool call to last AI message
                if (lastMsg.type === 'ai') {
                    lastMsg.toolCalls = [...(lastMsg.toolCalls || []), { ...event, status: 'running' }];
                }
            } else if (event.type === 'tool_output') {
                // Update tool call status
                if (lastMsg.type === 'ai' && lastMsg.toolCalls) {
                    const callIndex = lastMsg.toolCalls.findIndex(tc => tc.id === event.tool_call_id);
                    if (callIndex !== -1) {
                        // Copy the tool call object
                        lastMsg.toolCalls[callIndex] = {
                            ...lastMsg.toolCalls[callIndex],
                            status: 'done',
                            output: event.content
                        };
                    }
                }
            } else if (event.type === 'message') {
                // Update AI message content
                if (lastMsg.type === 'ai') {
                    lastMsg.content = event.content;

                    // Force complete any stuck tool calls
                    if (lastMsg.toolCalls) {
                        lastMsg.toolCalls = lastMsg.toolCalls.map(tc => {
                            if (tc.status === 'running') {
                                return { ...tc, status: 'done', output: '(No output received)' };
                            }
                            return tc;
                        });
                    }
                }
            }

            // Update the message in the array
            newMsgs[lastMsgIndex] = lastMsg;
            return newMsgs;
        });
    };

    return (
        <div className="h-screen flex flex-col bg-gray-950 text-gray-100 font-sans">
            {/* Header */}
            <div className="p-4 border-b border-gray-800 flex items-center gap-4 bg-gray-900 shadow-sm">
                <Link to={`/repo/${repoId}`} className="p-2 hover:bg-gray-800 rounded-full transition-colors text-gray-400 hover:text-white">
                    <ArrowLeft className="w-5 h-5" />
                </Link>
                <div>
                    <h1 className="text-lg font-bold text-white flex items-center gap-2">
                        <Bot className="w-5 h-5 text-blue-400" />
                        Agent Chat
                    </h1>
                    <p className="text-xs text-gray-400">Powered by LangGraph & Sheep Indexer</p>
                </div>
            </div>

            {/* Chat Area */}
            <div className="flex-1 overflow-y-auto p-4 space-y-6">
                {messages.map((msg, idx) => (
                    <div key={idx} className={`flex gap-4 ${msg.type === 'user' ? 'flex-row-reverse' : ''}`}>
                        <div className={`w-8 h-8 rounded-full flex items-center justify-center flex-shrink-0 ${msg.type === 'user' ? 'bg-blue-600' : 'bg-purple-600'}`}>
                            {msg.type === 'user' ? <User className="w-5 h-5" /> : <Bot className="w-5 h-5" />}
                        </div>

                        <div className={`flex flex-col gap-2 max-w-[80%] ${msg.type === 'user' ? 'items-end' : 'items-start'}`}>
                            {/* Tool Calls */}
                            {msg.toolCalls && msg.toolCalls.map((tc, i) => (
                                <div key={i} className="bg-gray-900 border border-gray-800 rounded-md p-2 text-xs font-mono w-full max-w-xl">
                                    <div className="flex items-center gap-2 text-gray-400 mb-1">
                                        <Terminal className="w-3 h-3" />
                                        <span className="font-bold text-purple-400">{tc.name}</span>
                                        <span className="text-gray-600 truncate">{JSON.stringify(tc.args)}</span>
                                        {tc.status === 'running' && <Loader2 className="w-3 h-3 animate-spin ml-auto" />}
                                    </div>
                                    {tc.output && (
                                        <div className="mt-2 pt-2 border-t border-gray-800 text-gray-500 max-h-32 overflow-y-auto whitespace-pre-wrap">
                                            {tc.output}
                                        </div>
                                    )}
                                </div>
                            ))}

                            {/* Message Content */}
                            {msg.content && (
                                <div className={`p-4 rounded-2xl ${msg.type === 'user' ? 'bg-blue-600 text-white' : 'bg-gray-800 text-gray-200'}`}>
                                    <div className="prose prose-invert prose-sm max-w-none">
                                        <ReactMarkdown>
                                            {msg.content}
                                        </ReactMarkdown>
                                    </div>
                                </div>
                            )}
                        </div>
                    </div>
                ))}
                <div ref={messagesEndRef} />
            </div>

            {/* Input Area */}
            <div className="p-4 bg-gray-900 border-t border-gray-800">
                <form onSubmit={handleSubmit} className="relative max-w-4xl mx-auto">
                    <input
                        type="text"
                        value={input}
                        onChange={(e) => setInput(e.target.value)}
                        placeholder="Ask about the codebase..."
                        className="w-full bg-gray-950 border border-gray-700 rounded-xl py-4 pl-6 pr-14 text-gray-100 focus:outline-none focus:border-blue-500 focus:ring-1 focus:ring-blue-500 transition-all shadow-lg"
                        disabled={loading}
                    />
                    <button
                        type="submit"
                        disabled={!input.trim() || loading}
                        className="absolute right-3 top-1/2 -translate-y-1/2 p-2 bg-blue-600 hover:bg-blue-500 text-white rounded-lg transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
                    >
                        {loading ? <Loader2 className="w-5 h-5 animate-spin" /> : <Send className="w-5 h-5" />}
                    </button>
                </form>
            </div>
        </div>
    );
}
