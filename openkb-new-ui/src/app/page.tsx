'use client';

import { useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { getKbs } from '@/lib/api';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import {
  Loader2,
  PanelLeftClose,
  PanelLeftOpen,
  Sparkles,
  Database,
  Gauge,
  Workflow,
  Briefcase,
  MessageSquare,
  Settings,
  BookOpen,
  ScanLine,
  ShieldCheck,
  SlidersHorizontal,
  Activity,
  Search,
  Pin,
  PinOff,
} from 'lucide-react';
import { cn } from '@/lib/utils';
import { usePersistentState } from '@/lib/use-persistent-state';
import { ThemeToggle, useTheme } from '@/lib/theme';
import { CommandPalette, openCommandPalette, type CommandItem } from '@/components/ui/command-palette';
import { DocumentsTab } from './components/DocumentsTab';
import { GlobalJobTracker } from './components/JobTracker';
import { SessionsTab } from './components/SessionsTab';
import { SettingsTab } from './components/SettingsTab';
import { WikiTab } from './components/WikiTab';
import { JobsTab } from './components/JobsTab';
import { OcrTab } from './components/OcrTab';
import { QualityTab } from './components/QualityTab';
import { ScoringTab } from './components/ScoringTab';
import { LlmUsageTab } from './components/LlmUsageTab';
import { OverviewTab } from './components/OverviewTab';

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

export default function Home() {
  const [selectedKb, setSelectedKb] = usePersistentState<string | null>('openkb:selected-kb', null);
  const [activeTab, setActiveTab] = usePersistentState<string>('openkb:active-tab', 'overview');
  const [sidebarCollapsed, setSidebarCollapsed] = usePersistentState<boolean>(
    'openkb:sidebar-collapsed',
    false,
  );
  const [pinnedKbs, setPinnedKbs] = usePersistentState<string[]>('openkb:pinned-kbs', []);
  const [kbSearch, setKbSearch] = useState('');
  const [globalActiveJobId, setGlobalActiveJobId] = useState<string | null>(null);
  const [wikiInitialPath, setWikiInitialPath] = useState<string | null>(null);

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

  // KB sidebar: pinned (incl. default) first, then alphabetical. Search narrows both groups.
  const pinnedSet = new Set(pinnedKbs);
  const searchQuery = kbSearch.trim().toLowerCase();
  const matchesSearch = (kb: KnownKb) => {
    if (!searchQuery) return true;
    const name = (kb.path.split('/').pop() || kb.path).toLowerCase();
    return name.includes(searchQuery) || kb.path.toLowerCase().includes(searchQuery);
  };
  const sortedKbs = [...availableKbs].sort((a, b) => {
    const aName = a.path.split('/').pop() || a.path;
    const bName = b.path.split('/').pop() || b.path;
    return aName.localeCompare(bName);
  });
  const pinnedList = sortedKbs.filter((kb) => (pinnedSet.has(kb.path) || kb.is_default) && matchesSearch(kb));
  const otherList = sortedKbs.filter((kb) => !pinnedSet.has(kb.path) && !kb.is_default && matchesSearch(kb));

  const togglePin = (path: string) => {
    setPinnedKbs((current) =>
      current.includes(path) ? current.filter((p) => p !== path) : [...current, path],
    );
  };

  const { setTheme } = useTheme();

  // Command palette: rebuilt every time the palette opens to capture fresh KBs / state.
  const tabDefinitions: Array<{ value: string; label: string; icon: React.ReactNode }> = [
    { value: 'overview', label: 'Overview', icon: <Gauge className="h-3.5 w-3.5" /> },
    { value: 'documents', label: 'Workflow', icon: <Workflow className="h-3.5 w-3.5" /> },
    { value: 'ocr', label: 'OCR', icon: <ScanLine className="h-3.5 w-3.5" /> },
    { value: 'jobs', label: 'Jobs', icon: <Briefcase className="h-3.5 w-3.5" /> },
    { value: 'sessions', label: 'Sessions', icon: <MessageSquare className="h-3.5 w-3.5" /> },
    { value: 'settings', label: 'Settings', icon: <Settings className="h-3.5 w-3.5" /> },
    { value: 'wiki', label: 'Wiki', icon: <BookOpen className="h-3.5 w-3.5" /> },
    { value: 'quality', label: 'Quality', icon: <ShieldCheck className="h-3.5 w-3.5" /> },
    { value: 'scoring', label: 'Scoring', icon: <SlidersHorizontal className="h-3.5 w-3.5" /> },
    { value: 'usage', label: 'LLM Usage', icon: <Activity className="h-3.5 w-3.5" /> },
  ];

  const buildCommands = (): CommandItem[] => {
    const commands: CommandItem[] = [];

    // Switch knowledge base
    for (const kb of availableKbs) {
      const name = kb.path.split('/').pop() || kb.path;
      const isActive = resolvedSelectedKb === kb.path;
      commands.push({
        id: `kb:${kb.path}`,
        label: name,
        description: kb.path,
        group: 'Knowledge Bases',
        icon: <Database className="h-3.5 w-3.5" />,
        keywords: kb.path,
        hint: isActive ? 'current' : undefined,
        perform: () => setSelectedKb(kb.path),
      });
    }

    // Navigate to tab
    for (const tab of tabDefinitions) {
      commands.push({
        id: `tab:${tab.value}`,
        label: tab.label,
        description: 'Switch tab',
        group: 'Navigate',
        icon: tab.icon,
        hint: activeTab === tab.value ? 'current' : undefined,
        perform: () => setActiveTab(tab.value),
      });
    }

    // Sidebar toggle
    commands.push({
      id: 'ui:toggle-sidebar',
      label: sidebarCollapsed ? 'Expand sidebar' : 'Collapse sidebar',
      group: 'View',
      icon: sidebarCollapsed ? <PanelLeftOpen className="h-3.5 w-3.5" /> : <PanelLeftClose className="h-3.5 w-3.5" />,
      perform: () => setSidebarCollapsed((v) => !v),
    });

    // Pin / unpin current KB
    if (resolvedSelectedKb) {
      const isPinned = pinnedKbs.includes(resolvedSelectedKb);
      const name = resolvedSelectedKb.split('/').pop() || resolvedSelectedKb;
      commands.push({
        id: 'kb:toggle-pin',
        label: isPinned ? `Unpin "${name}"` : `Pin "${name}"`,
        group: 'View',
        icon: isPinned ? <PinOff className="h-3.5 w-3.5" /> : <Pin className="h-3.5 w-3.5" />,
        perform: () => togglePin(resolvedSelectedKb),
      });
    }

    // Theme switch
    for (const variant of ['light', 'dark', 'system'] as const) {
      commands.push({
        id: `theme:${variant}`,
        label: `Theme: ${variant.charAt(0).toUpperCase() + variant.slice(1)}`,
        group: 'Theme',
        perform: () => setTheme(variant),
      });
    }

    return commands;
  };

  if (isLoadingKbs) {
    return (
      <div className="flex h-screen items-center justify-center bg-gradient-to-br from-background via-background to-muted/30">
        <div className="flex flex-col items-center gap-3">
          <Loader2 className="h-10 w-10 animate-spin text-primary" />
          <p className="text-sm text-muted-foreground">Loading knowledge bases…</p>
        </div>
      </div>
    );
  }

  return (
    <div className="flex flex-col h-screen bg-gradient-to-br from-background via-background to-muted/30">
      {/* Top Header */}
      <header className="flex h-14 items-center justify-between px-4 sm:px-6 border-b bg-background/70 backdrop-blur-md shrink-0 supports-[backdrop-filter]:bg-background/60">
        <div className="flex items-center gap-3">
          <Button
            variant="ghost"
            size="icon-sm"
            onClick={() => setSidebarCollapsed((v) => !v)}
            aria-label={sidebarCollapsed ? 'Expand sidebar' : 'Collapse sidebar'}
            title={sidebarCollapsed ? 'Expand sidebar' : 'Collapse sidebar'}
          >
            {sidebarCollapsed ? <PanelLeftOpen /> : <PanelLeftClose />}
          </Button>
          <div className="flex items-center gap-2">
            <div className="flex h-7 w-7 items-center justify-center rounded-md bg-gradient-to-br from-primary to-primary/70 text-primary-foreground shadow-sm">
              <Sparkles className="h-4 w-4" />
            </div>
            <h1 className="text-lg font-semibold tracking-tight">OpenKB</h1>
            <span className="hidden sm:inline-flex text-[11px] uppercase tracking-wider font-medium text-muted-foreground px-2 py-0.5 bg-muted/70 rounded-full border">
              Staged Workbench
            </span>
          </div>
        </div>
        <div className="flex items-center gap-3">
          {globalActiveJobId && (
            <GlobalJobTracker
              jobId={globalActiveJobId}
              onTerminal={() => {
                if (resolvedSelectedKb) {
                  queryClient.invalidateQueries({ queryKey: ['documents', resolvedSelectedKb] });
                  queryClient.invalidateQueries({ queryKey: ['kbStats', resolvedSelectedKb] });
                  queryClient.invalidateQueries({ queryKey: ['llm-usage', resolvedSelectedKb] });
                }
              }}
              onComplete={() => {
                // Auto-dismiss after 3s on success
                setTimeout(() => setGlobalActiveJobId(null), 3000);
              }}
              onDismiss={() => setGlobalActiveJobId(null)}
            />
          )}
          <Button
            type="button"
            variant="outline"
            size="sm"
            onClick={() => openCommandPalette()}
            className="hidden sm:inline-flex h-8 gap-2 px-2 text-xs text-muted-foreground"
            aria-label="Open command palette"
            title="Open command palette (Ctrl+K)"
          >
            <Search className="h-3.5 w-3.5" />
            <span className="hidden lg:inline">Quick switch</span>
            <kbd className="rounded border bg-muted px-1.5 py-0.5 text-[10px] font-medium">
              ⌘K
            </kbd>
          </Button>
          <Button
            type="button"
            variant="ghost"
            size="icon-sm"
            onClick={() => openCommandPalette()}
            className="sm:hidden"
            aria-label="Open command palette"
          >
            <Search className="h-4 w-4" />
          </Button>
          <ThemeToggle />
        </div>
      </header>

      {/* Main Workspace */}
      <div className="flex flex-1 overflow-hidden">

        {/* Left Sidebar: KB Selection */}
        <aside
          className={cn(
            'group/sidebar relative border-r bg-muted/30 backdrop-blur-sm flex flex-col shrink-0 transition-[width] duration-300 ease-in-out',
            sidebarCollapsed ? 'w-14' : 'w-64',
          )}
        >
          <div className={cn(
            'flex items-center border-b shrink-0 h-14',
            sidebarCollapsed ? 'justify-center px-2' : 'justify-between px-4',
          )}>
            {sidebarCollapsed ? (
              <div className="flex h-8 w-8 items-center justify-center rounded-md text-muted-foreground" title="Knowledge Bases">
                <Database className="h-4 w-4" />
              </div>
            ) : (
              <div className="flex items-center gap-2 min-w-0">
                <Database className="h-4 w-4 text-muted-foreground shrink-0" />
                <div className="min-w-0">
                  <h2 className="font-semibold text-sm leading-tight truncate">Knowledge Bases</h2>
                  <p className="text-[11px] text-muted-foreground truncate">Select a workspace</p>
                </div>
              </div>
            )}
          </div>

          {!sidebarCollapsed && availableKbs.length > 3 && (
            <div className="border-b px-3 py-2 shrink-0">
              <div className="relative">
                <Search className="pointer-events-none absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground" />
                <Input
                  value={kbSearch}
                  onChange={(event) => setKbSearch(event.target.value)}
                  placeholder="Search workspaces"
                  className="h-8 pl-8 pr-2 text-xs"
                  aria-label="Search knowledge bases"
                />
              </div>
            </div>
          )}

          <div className={cn(
            'overflow-y-auto flex-1 min-h-0',
            sidebarCollapsed ? 'p-2 space-y-1' : 'p-2',
          )}>
            {availableKbs.length === 0 && !sidebarCollapsed && (
              <div className="text-center text-xs text-muted-foreground px-2 py-8">
                No knowledge bases found.
              </div>
            )}

            {sidebarCollapsed ? (
              // Collapsed: pinned first, then others; no sections
              [...pinnedList, ...otherList].map((kb) => (
                <KbSidebarItem
                  key={kb.path}
                  kb={kb}
                  collapsed
                  isActive={resolvedSelectedKb === kb.path}
                  isPinned={pinnedSet.has(kb.path)}
                  onSelect={setSelectedKb}
                  onTogglePin={togglePin}
                />
              ))
            ) : (
              <>
                {pinnedList.length > 0 && (
                  <SidebarSection label="Pinned">
                    {pinnedList.map((kb) => (
                      <KbSidebarItem
                        key={kb.path}
                        kb={kb}
                        isActive={resolvedSelectedKb === kb.path}
                        isPinned={pinnedSet.has(kb.path)}
                        onSelect={setSelectedKb}
                        onTogglePin={togglePin}
                      />
                    ))}
                  </SidebarSection>
                )}
                {otherList.length > 0 && (
                  <SidebarSection label={pinnedList.length > 0 ? 'All workspaces' : undefined}>
                    {otherList.map((kb) => (
                      <KbSidebarItem
                        key={kb.path}
                        kb={kb}
                        isActive={resolvedSelectedKb === kb.path}
                        isPinned={pinnedSet.has(kb.path)}
                        onSelect={setSelectedKb}
                        onTogglePin={togglePin}
                      />
                    ))}
                  </SidebarSection>
                )}
                {searchQuery && pinnedList.length === 0 && otherList.length === 0 && (
                  <div className="text-center text-xs text-muted-foreground px-2 py-6">
                    No workspaces match &ldquo;{kbSearch}&rdquo;.
                  </div>
                )}
              </>
            )}
          </div>

          {!sidebarCollapsed && availableKbs.length > 0 && (
            <div className="border-t px-3 py-2 text-[11px] text-muted-foreground shrink-0 flex items-center justify-between">
              <span>{availableKbs.length} workspace{availableKbs.length === 1 ? '' : 's'}</span>
              {pinnedList.length > 0 && <span>{pinnedList.length} pinned</span>}
            </div>
          )}
        </aside>

        {/* Middle Area: Main Content Tabs */}
        <div className="flex-1 flex flex-col overflow-hidden min-w-0">
          <Tabs value={activeTab} onValueChange={setActiveTab} className="flex-1 flex flex-col p-4 sm:p-6 overflow-hidden">
            <TabsList className="w-fit mb-5 shrink-0 gap-1 bg-muted/50 backdrop-blur-sm">
              <TabsTrigger value="overview" className="gap-1.5">
                <Gauge className="h-3.5 w-3.5" />
                Overview
              </TabsTrigger>
              <TabsTrigger value="documents" className="gap-1.5">
                <Workflow className="h-3.5 w-3.5" />
                Workflow
              </TabsTrigger>
              <TabsTrigger value="ocr" className="gap-1.5">
                <ScanLine className="h-3.5 w-3.5" />
                OCR
              </TabsTrigger>
              <TabsTrigger value="jobs" className="gap-1.5">
                <Briefcase className="h-3.5 w-3.5" />
                Jobs
              </TabsTrigger>
              <TabsTrigger value="sessions" className="gap-1.5">
                <MessageSquare className="h-3.5 w-3.5" />
                Sessions
              </TabsTrigger>
              <TabsTrigger value="settings" className="gap-1.5">
                <Settings className="h-3.5 w-3.5" />
                Settings
              </TabsTrigger>
              <TabsTrigger value="quality" className="gap-1.5">
                <ShieldCheck className="h-3.5 w-3.5" />
                Quality
              </TabsTrigger>
              <TabsTrigger value="scoring" className="gap-1.5">
                <SlidersHorizontal className="h-3.5 w-3.5" />
                Scoring
              </TabsTrigger>
              <TabsTrigger value="usage" className="gap-1.5">
                <Activity className="h-3.5 w-3.5" />
                LLM Usage
              </TabsTrigger>
              <TabsTrigger value="wiki" className="gap-1.5">
                <BookOpen className="h-3.5 w-3.5" />
                Wiki
              </TabsTrigger>
            </TabsList>

            <TabsContent value="overview" className="flex-1 overflow-hidden m-0 outline-none">
              <OverviewTab kbDir={resolvedSelectedKb} onOpenTab={setActiveTab} />
            </TabsContent>

            <TabsContent value="documents" className="flex-1 overflow-hidden m-0 outline-none">
              {resolvedSelectedKb ? (
                <DocumentsTab
                  key={resolvedSelectedKb}
                  kbDir={resolvedSelectedKb}
                  onJobStarted={setGlobalActiveJobId}
                  onNavigateToWiki={(path) => {
                    setWikiInitialPath(path);
                    setActiveTab('wiki');
                  }}
                />
              ) : (
                <EmptyState message="Select a knowledge base to use the workflow workbench." />
              )}
            </TabsContent>

            <TabsContent value="jobs" className="flex-1 overflow-hidden m-0 outline-none">
              <JobsTab key={`jobs-${resolvedSelectedKb ?? 'none'}`} kbDir={resolvedSelectedKb} />
            </TabsContent>

            <TabsContent value="ocr" className="flex-1 overflow-hidden m-0 outline-none">
              <OcrTab key={`ocr-${resolvedSelectedKb ?? 'none'}`} kbDir={resolvedSelectedKb} />
            </TabsContent>

            <TabsContent value="sessions" keepMounted className="flex-1 overflow-hidden m-0 outline-none">
              {resolvedSelectedKb ? (
                <SessionsTab
                  key={`sessions-${resolvedSelectedKb}`}
                  kbDir={resolvedSelectedKb}
                  onNavigateToWiki={(path) => {
                    setWikiInitialPath(path);
                    setActiveTab('wiki');
                  }}
                />
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

            <TabsContent value="quality" className="flex-1 overflow-hidden m-0 outline-none">
              <QualityTab
                key={`quality-${resolvedSelectedKb ?? 'none'}`}
                kbDir={resolvedSelectedKb}
                onJobStarted={setGlobalActiveJobId}
              />
            </TabsContent>

            <TabsContent value="scoring" className="flex-1 overflow-hidden m-0 outline-none">
              <ScoringTab
                key={`scoring-${resolvedSelectedKb ?? 'none'}`}
                kbDir={resolvedSelectedKb}
              />
            </TabsContent>

            <TabsContent value="usage" className="flex-1 overflow-hidden m-0 outline-none">
              <LlmUsageTab
                key={`usage-${resolvedSelectedKb ?? 'none'}`}
                kbDir={resolvedSelectedKb}
              />
            </TabsContent>

            <TabsContent value="wiki" className="flex-1 overflow-hidden m-0 outline-none">
              {resolvedSelectedKb ? (
                <WikiTab
                  key={`wiki-${resolvedSelectedKb}`}
                  kbDir={resolvedSelectedKb}
                  initialPath={wikiInitialPath}
                />
              ) : (
                <EmptyState message="Select a knowledge base to browse wiki files." />
              )}
            </TabsContent>
          </Tabs>
        </div>

      </div>

      <CommandPalette getCommands={buildCommands} />
    </div>
  );
}

function EmptyState({ message }: { message: string }) {
  return (
    <div className="flex h-full items-center justify-center text-muted-foreground">
      <div className="flex flex-col items-center gap-2 text-center">
        <div className="flex h-10 w-10 items-center justify-center rounded-full bg-muted">
          <Sparkles className="h-4 w-4" />
        </div>
        <p className="text-sm">{message}</p>
      </div>
    </div>
  );
}

function SidebarSection({ label, children }: { label?: string; children: React.ReactNode }) {
  return (
    <div className="mb-2 last:mb-0">
      {label ? (
        <div className="px-2 pb-1 pt-1 text-[10px] font-semibold uppercase tracking-wider text-muted-foreground/70">
          {label}
        </div>
      ) : null}
      <div className="space-y-0.5">{children}</div>
    </div>
  );
}

function KbSidebarItem({
  kb,
  isActive,
  isPinned,
  collapsed = false,
  onSelect,
  onTogglePin,
}: {
  kb: KnownKb;
  isActive: boolean;
  isPinned: boolean;
  collapsed?: boolean;
  onSelect: (path: string) => void;
  onTogglePin: (path: string) => void;
}) {
  const name = kb.path.split('/').pop() || kb.path;
  const initial = name.charAt(0).toUpperCase();

  if (collapsed) {
    return (
      <Button
        variant={isActive ? 'default' : 'ghost'}
        className={cn(
          'w-full h-9 px-0 justify-center transition-all',
          isActive && 'shadow-sm',
        )}
        onClick={() => onSelect(kb.path)}
        title={`${name}\n${kb.path}`}
      >
        <span className="font-semibold text-sm">{initial}</span>
      </Button>
    );
  }

  return (
    <div className="group/kb relative">
      <Button
        variant={isActive ? 'default' : 'ghost'}
        className={cn(
          'w-full justify-start text-left truncate gap-2 h-9 pr-8 transition-all',
          isActive && 'shadow-sm',
        )}
        onClick={() => onSelect(kb.path)}
        title={kb.path}
      >
        <span
          className={cn(
            'flex h-5 w-5 items-center justify-center rounded text-[10px] font-semibold shrink-0',
            isActive
              ? 'bg-primary-foreground/20 text-primary-foreground'
              : 'bg-muted text-muted-foreground',
          )}
        >
          {initial}
        </span>
        <span className="truncate flex-1">{name}</span>
        {kb.is_default && !isActive && (
          <span className="text-[10px] text-muted-foreground/70 shrink-0">default</span>
        )}
      </Button>
      <button
        type="button"
        onClick={(event) => {
          event.stopPropagation();
          onTogglePin(kb.path);
        }}
        aria-label={isPinned ? `Unpin ${name}` : `Pin ${name}`}
        title={isPinned ? 'Unpin from top' : 'Pin to top'}
        className={cn(
          'absolute right-1.5 top-1/2 -translate-y-1/2 flex h-6 w-6 items-center justify-center rounded transition-opacity',
          'focus-visible:opacity-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/50',
          isActive
            ? 'text-primary-foreground/80 hover:bg-primary-foreground/15 opacity-80'
            : 'text-muted-foreground hover:bg-foreground/10 hover:text-foreground',
          isPinned ? 'opacity-100' : 'opacity-0 group-hover/kb:opacity-100',
        )}
      >
        {isPinned ? <Pin className="h-3.5 w-3.5 fill-current" /> : <PinOff className="h-3.5 w-3.5" />}
      </button>
    </div>
  );
}
