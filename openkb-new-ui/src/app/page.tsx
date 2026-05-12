'use client';

import { useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { getKbs, getKbStats } from '@/lib/api';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { Button } from '@/components/ui/button';
import { Loader2 } from 'lucide-react';
import { DocumentsTab } from './components/DocumentsTab';
import { ChatSidebar } from './components/ChatSidebar';
import { GlobalJobTracker } from './components/JobTracker';
import { SessionsTab } from './components/SessionsTab';
import { SettingsTab } from './components/SettingsTab';
import { WikiTab } from './components/WikiTab';

interface KnownKb {
  path: string;
  exists: boolean;
  is_kb: boolean;
  is_default: boolean;
}

interface KbListResponse {
  default_kb: string | null;
  known_kbs: KnownKb[];
}

interface KbStatusResponse {
  kb_dir: string;
  directories: Record<string, number>;
  total_indexed: number;
  last_compile: string | null;
  last_lint: string | null;
}

export default function Home() {
  const [selectedKb, setSelectedKb] = useState<string | null>(null);
  const [activeTab, setActiveTab] = useState('overview');
  const [globalActiveJobId, setGlobalActiveJobId] = useState<string | null>(null);

  const queryClient = useQueryClient();

  const { data: kbs, isLoading: isLoadingKbs } = useQuery<KbListResponse>({
    queryKey: ['kbs'],
    queryFn: getKbs,
  });

  const availableKbs = (kbs?.known_kbs ?? []).filter((kb) => kb.exists && kb.is_kb);
  const preferredKb = availableKbs.find((kb) => kb.path === kbs?.default_kb) ?? availableKbs[0] ?? null;
  const resolvedSelectedKb = selectedKb && availableKbs.some((kb) => kb.path === selectedKb)
    ? selectedKb
    : preferredKb?.path ?? null;

  const { data: kbStats, isLoading: isLoadingStats } = useQuery<KbStatusResponse | null>({
    queryKey: ['kbStats', resolvedSelectedKb],
    queryFn: () => getKbStats(resolvedSelectedKb!),
    enabled: !!resolvedSelectedKb,
  });

  if (isLoadingKbs) {
    return (
      <div className="flex h-screen items-center justify-center">
        <Loader2 className="h-8 w-8 animate-spin" />
      </div>
    );
  }

  return (
    <div className="flex flex-col h-screen bg-background">
      {/* Top Header */}
      <header className="flex h-14 items-center justify-between px-6 border-b shrink-0">
        <div className="flex items-center gap-4">
          <h1 className="text-xl font-bold tracking-tight">OpenKB</h1>
          <span className="text-sm text-muted-foreground px-2 py-1 bg-muted rounded-md">Staged Workbench</span>
        </div>
        <div>
          {globalActiveJobId && (
            <GlobalJobTracker
              jobId={globalActiveJobId}
              onComplete={() => {
                // Auto-dismiss after 3s on success
                setTimeout(() => setGlobalActiveJobId(null), 3000);
                if (resolvedSelectedKb) {
                  queryClient.invalidateQueries({ queryKey: ['documents', resolvedSelectedKb] });
                  queryClient.invalidateQueries({ queryKey: ['kbStats', resolvedSelectedKb] });
                }
              }}
              onDismiss={() => setGlobalActiveJobId(null)}
            />
          )}
        </div>
      </header>

      {/* Main Workspace */}
      <div className="flex flex-1 overflow-hidden">

        {/* Left Sidebar: KB Selection */}
        <div className="w-64 border-r bg-muted/20 flex flex-col shrink-0">
          <div className="p-4 border-b shrink-0">
            <h2 className="font-semibold">Knowledge Bases</h2>
            <p className="text-xs text-muted-foreground">Select a workspace</p>
          </div>
          <div className="p-4 space-y-2 overflow-y-auto flex-1 min-h-0">
            {availableKbs.map((kb) => (
              <Button
                key={kb.path}
                variant={resolvedSelectedKb === kb.path ? 'default' : 'ghost'}
                className="w-full justify-start text-left truncate"
                onClick={() => setSelectedKb(kb.path)}
                title={kb.path}
              >
                {kb.path.split('/').pop() || kb.path}
              </Button>
            ))}
          </div>
        </div>

        {/* Middle Area: Main Content Tabs */}
        <div className="flex-1 flex flex-col overflow-hidden min-w-0">
          <Tabs value={activeTab} onValueChange={setActiveTab} className="flex-1 flex flex-col p-6 overflow-hidden">
            <TabsList className="w-fit mb-6 shrink-0">
              <TabsTrigger value="overview">Overview</TabsTrigger>
              <TabsTrigger value="documents">Workflow</TabsTrigger>
              <TabsTrigger value="sessions">Sessions</TabsTrigger>
              <TabsTrigger value="settings">Settings</TabsTrigger>
              <TabsTrigger value="wiki">Wiki</TabsTrigger>
            </TabsList>

            <TabsContent value="overview" className="flex-1 overflow-hidden m-0 outline-none flex flex-col">
              <Card className="flex-1 flex flex-col overflow-hidden min-h-0">
                <CardHeader className="shrink-0">
                  <CardTitle>Statistics</CardTitle>
                  <CardDescription>
                    {isLoadingStats ? 'Loading stats...' : 'Overview of the current knowledge base'}
                  </CardDescription>
                </CardHeader>
                <CardContent className="overflow-y-auto flex-1">
                  {resolvedSelectedKb && kbStats ? (
                    <div className="grid grid-cols-2 lg:grid-cols-4 gap-4 text-center">
                      <div className="bg-muted p-4 rounded-lg">
                        <p className="text-3xl font-bold">{kbStats.total_indexed || 0}</p>
                        <p className="text-sm text-muted-foreground">Tracked Documents</p>
                      </div>
                      <div className="bg-muted p-4 rounded-lg">
                        <p className="text-3xl font-bold">{kbStats.directories?.raw || 0}</p>
                        <p className="text-sm text-muted-foreground">Inventory Inputs</p>
                      </div>
                      <div className="bg-muted p-4 rounded-lg">
                        <p className="text-3xl font-bold">{kbStats.directories?.summaries || 0}</p>
                        <p className="text-sm text-muted-foreground">Summary Pages</p>
                      </div>
                      <div className="bg-muted p-4 rounded-lg">
                        <p className="text-3xl font-bold">{kbStats.directories?.reports || 0}</p>
                        <p className="text-sm text-muted-foreground">Reports</p>
                      </div>
                    </div>
                  ) : (
                    <p className="text-muted-foreground">Select a knowledge base to view stats.</p>
                  )}
                </CardContent>
              </Card>
            </TabsContent>

            <TabsContent value="documents" className="flex-1 overflow-hidden m-0 outline-none">
              {resolvedSelectedKb ? (
                <DocumentsTab
                  key={resolvedSelectedKb}
                  kbDir={resolvedSelectedKb}
                  onJobStarted={setGlobalActiveJobId}
                />
              ) : (
                <EmptyState message="Select a knowledge base to use the workflow workbench." />
              )}
            </TabsContent>

            <TabsContent value="sessions" className="flex-1 overflow-hidden m-0 outline-none">
              {resolvedSelectedKb ? (
                <SessionsTab key={`sessions-${resolvedSelectedKb}`} kbDir={resolvedSelectedKb} />
              ) : (
                <EmptyState message="Select a knowledge base to browse chat sessions." />
              )}
            </TabsContent>

            <TabsContent value="settings" className="flex-1 overflow-hidden m-0 outline-none">
              <SettingsTab
                key={`settings-${resolvedSelectedKb ?? 'none'}`}
                kbDir={resolvedSelectedKb}
                onKbChanged={(kbPath) => {
                  setSelectedKb(kbPath);
                  queryClient.invalidateQueries({ queryKey: ['kbs'] });
                  queryClient.invalidateQueries({ queryKey: ['kbStats', kbPath] });
                }}
              />
            </TabsContent>

            <TabsContent value="wiki" className="flex-1 overflow-hidden m-0 outline-none">
              {resolvedSelectedKb ? (
                <WikiTab key={`wiki-${resolvedSelectedKb}`} kbDir={resolvedSelectedKb} />
              ) : (
                <EmptyState message="Select a knowledge base to browse wiki files." />
              )}
            </TabsContent>
          </Tabs>
        </div>

        {/* Right Sidebar: Chat Assistant */}
        <div className="w-[350px] border-l shrink-0 bg-background">
          <ChatSidebar kbDir={resolvedSelectedKb || ''} />
        </div>
      </div>
    </div>
  );
}

function EmptyState({ message }: { message: string }) {
  return (
    <div className="flex h-full items-center justify-center text-muted-foreground">
      {message}
    </div>
  );
}
