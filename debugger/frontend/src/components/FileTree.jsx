import React, { useState } from 'react';
import { ChevronRight, ChevronDown, FileCode, Folder } from 'lucide-react';

const FileTreeNode = ({ node, onSelect }) => {
    const [isOpen, setIsOpen] = useState(false);

    const handleToggle = (e) => {
        e.stopPropagation();
        if (node.type === 'directory') {
            setIsOpen(!isOpen);
        } else {
            onSelect(node.path);
        }
    };

    return (
        <div className="pl-4">
            <div
                className={`flex items-center gap-2 py-1 px-2 rounded cursor-pointer hover:bg-gray-800 ${node.type === 'file' ? 'text-gray-300' : 'text-blue-300 font-medium'}`}
                onClick={handleToggle}
            >
                {node.type === 'directory' && (
                    isOpen ? <ChevronDown className="w-4 h-4" /> : <ChevronRight className="w-4 h-4" />
                )}
                {node.type === 'file' && <FileCode className="w-4 h-4 text-gray-500" />}
                {node.type === 'directory' && <Folder className="w-4 h-4" />}
                <span className="truncate flex-1">{node.name}</span>
                {node.status && (
                    <span
                        className={`w-2 h-2 rounded-full ${node.status === 'success' ? 'bg-green-500' :
                                node.status === 'failed' ? 'bg-red-500' :
                                    'bg-gray-500'
                            }`}
                        title={node.status}
                    />
                )}
            </div>

            {isOpen && node.children && (
                <div>
                    {node.children.map((child) => (
                        <FileTreeNode key={child.path} node={child} onSelect={onSelect} />
                    ))}
                </div>
            )}
        </div>
    );
};

export default function FileTree({ files, onSelectFile }) {
    return (
        <div className="h-full overflow-y-auto p-2">
            {files.map((node) => (
                <FileTreeNode key={node.path} node={node} onSelect={onSelectFile} />
            ))}
        </div>
    );
}
