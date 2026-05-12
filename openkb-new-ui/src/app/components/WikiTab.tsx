'use client';

import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import axios from 'axios';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { Card, CardHeader, CardTitle } from '@/components/ui/card';
import { ScrollArea } from '@/components/ui/scroll-area';
import { Loader2, FileText, FolderTree } from 'lucide-react';
import { Button } from '@/components/ui/button';

interface WikiFileNode {
  path: string;
  name: string;
  directory: string;
  depth: number;
  extension: string;
  size: number;
  modified: string;
}

export function WikiTab({ kbDir }: { kbDir: string }) {
  const [selectedFilePath, setSelectedFilePath] = useState<string | null>(null);

  // Fetch the Wiki Tree
  const { data: treeData, isLoading: isLoadingTree } = useQuery({
    queryKey: ['wikiTree', kbDir],
    queryFn: async () => {
      const res = await axios.get('/api/wiki/tree', { params: { kb_dir: kbDir } });
      return res.data;
    },
    enabled: !!kbDir,
  });

  // Fetch the specific file content when selected
  const { data: fileData, isLoading: isLoadingFile } = useQuery({
    queryKey: ['wikiFile', kbDir, selectedFilePath],
    queryFn: async () => {
      if (!selectedFilePath) return null;
      const res = await axios.get('/api/wiki/file', {
        params: { kb_dir: kbDir, path: selectedFilePath },
      });
      return res.data;
    },
    enabled: !!kbDir && !!selectedFilePath,
  });

  return (
    <Card className="h-full flex flex-col rounded-none border-t-0 border-b-0 border-x-0 sm:border-x sm:rounded-lg overflow-hidden py-0 gap-0">
      <div className="flex flex-1 overflow-hidden min-h-0">
        {/* Left Sidebar: File Tree */}
        <div className="w-1/3 border-r bg-muted/10 flex flex-col shrink-0 min-h-0">
          <CardHeader className="py-4 border-b shrink-0">
            <CardTitle className="text-sm flex items-center gap-2">
              <FolderTree className="w-4 h-4" />
              Wiki Index
            </CardTitle>
          </CardHeader>
          <ScrollArea className="flex-1 min-h-0 overflow-hidden">
            {isLoadingTree ? (
              <div className="flex justify-center p-6">
                <Loader2 className="w-5 h-5 animate-spin text-muted-foreground" />
              </div>
            ) : !treeData?.files?.length ? (
              <div className="text-center p-6 text-sm text-muted-foreground">
                No wiki files found.
              </div>
            ) : (
              <div className="p-2 space-y-1">
                {treeData.files.map((file: WikiFileNode) => (
                  <Button
                    key={file.path}
                    variant={selectedFilePath === file.path ? 'secondary' : 'ghost'}
                    className={`w-full justify-start text-left text-sm h-8 px-2 font-normal ${
                      selectedFilePath === file.path ? 'font-medium' : ''
                    }`}
                    onClick={() => setSelectedFilePath(file.path)}
                    style={{ paddingLeft: `${(file.depth + 1) * 0.5}rem` }}
                  >
                    <FileText className="w-3.5 h-3.5 mr-2 shrink-0 opacity-70" />
                    <span className="truncate">{file.name}</span>
                  </Button>
                ))}
              </div>
            )}
          </ScrollArea>
        </div>

        {/* Right Content Area: Markdown Viewer */}
        <div className="w-2/3 flex flex-col bg-background min-w-0 min-h-0">
          {isLoadingFile ? (
            <div className="flex-1 flex items-center justify-center">
              <Loader2 className="w-8 h-8 animate-spin text-muted-foreground" />
            </div>
          ) : !selectedFilePath ? (
            <div className="flex-1 flex flex-col items-center justify-center text-muted-foreground">
              <FileText className="w-12 h-12 mb-4 opacity-20" />
              <p>Select a document from the index to view its content.</p>
            </div>
          ) : (
            <ScrollArea className="flex-1 min-h-0 overflow-hidden">
              <div className="p-8 prose prose-slate dark:prose-invert max-w-none">
                {fileData?.content ? (
                  <ReactMarkdown remarkPlugins={[remarkGfm]}>
                    {fileData.content}
                  </ReactMarkdown>
                ) : (
                  <div className="text-muted-foreground italic">Document is empty or cannot be read.</div>
                )}
              </div>
            </ScrollArea>
          )}
        </div>
      </div>
    </Card>
  );
}
