'use client';

import React, { useEffect, useState } from 'react';
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
  ChevronDown,
  ChevronUp,
  Pin,
  PinOff,
  Building2,
  TrendingUp,
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
import { EntitiesTab } from './components/EntitiesTab';
import { MarketTab } from './components/MarketTab';

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
  const [mobileNavOpen, setMobileNavOpen] = useState(false);

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
  const tabDefinitions: Array<{ value: string; label: string; icon: React.ReactElement<{ className?: string }> }> = [
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
    { value: 'entities', label: 'Entities', icon: <Building2 className="h-3.5 w-3.5" /> },
    { value: 'market', label: 'Market', icon: <TrendingUp className="h-3.5 w-3.5" /> },
  ];

  const mobileNavItems = tabDefinitions.filter((tab) =>
    ['documents', 'jobs', 'sessions', 'wiki'].includes(tab.value),
  );

  useEffect(() => {
    const isMobile = window.matchMedia('(max-width: 767px)').matches;
    if (isMobile && activeTab === 'overview') {
      setActiveTab('documents');
    }
  }, [activeTab, setActiveTab]);

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
    <div className="flex h-dvh flex-col bg-background">
      {/* Top Header */}
      <header className="sticky top-0 z-50 hidden h-12 shrink-0 items-center justify-between gap-2 border-b bg-background/95 px-3 backdrop-blur-xl sm:flex sm:h-14 sm:px-6 sm:gap-3">
        <div className="flex min-w-0 items-center gap-2 sm:gap-4">
          <Button
            variant="ghost"
            size="icon"
            onClick={() => setSidebarCollapsed((v) => !v)}
            aria-label={sidebarCollapsed ? 'Expand sidebar' : 'Collapse sidebar'}
            className="hidden h-9 w-9 rounded-xl hover:bg-accent/50 md:inline-flex"
          >
            {sidebarCollapsed ? <PanelLeftOpen className="h-5 w-5" /> : <PanelLeftClose className="h-5 w-5" />}
          </Button>
          <div className="flex items-center gap-1.5 sm:gap-3">
            <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-gradient-to-br from-primary to-primary/80 text-primary-foreground shadow-sm shadow-primary/20 sm:h-9 sm:w-9">
              <Sparkles className="h-4 w-4 sm:h-5 sm:w-5" />
            </div>
            <div className="hidden flex-col leading-tight sm:flex">
              <h1 className="text-lg font-bold tracking-tight bg-clip-text text-transparent bg-gradient-to-r from-foreground to-foreground/70">OpenKB</h1>
              <span className="text-[10px] uppercase tracking-[0.1em] font-bold text-primary/80">
                Staged Workbench
              </span>
            </div>
          </div>
        </div>
        <div className="flex items-center gap-2 sm:gap-3">
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
            variant="secondary"
            size="sm"
            onClick={() => openCommandPalette()}
            className="hidden sm:inline-flex h-9 gap-2 px-3 text-xs font-medium bg-secondary/50 border-none hover:bg-secondary"
            aria-label="Open command palette"
          >
            <Search className="h-3.5 w-3.5" />
            <span className="hidden lg:inline">Quick search...</span>
            <kbd className="hidden xl:inline-flex h-5 items-center gap-1 rounded border bg-background px-1.5 font-mono text-[10px] font-medium opacity-100">
              <span className="text-xs">⌘</span>K
            </kbd>
          </Button>
          <ThemeToggle />
        </div>
      </header>

      {/* Main Workspace */}
      <div className="flex flex-1 overflow-hidden">

        {/* Left Sidebar: KB Selection */}
        <aside
          className={cn(
            'group/sidebar relative hidden border-r bg-sidebar/30 backdrop-blur-xl flex-col shrink-0 transition-all duration-300 ease-in-out md:flex',
            sidebarCollapsed ? 'w-[72px]' : 'w-72',
          )}
        >
          <div className={cn(
            'flex items-center shrink-0 h-16',
            sidebarCollapsed ? 'justify-center px-2' : 'px-6',
          )}>
            {sidebarCollapsed ? (
              <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-accent/50 text-accent-foreground" title="Knowledge Bases">
                <Database className="h-5 w-5" />
              </div>
            ) : (
              <div className="flex items-center gap-3 w-full">
                <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-primary/10 text-primary">
                  <Database className="h-4 w-4" />
                </div>
                <div className="flex flex-col min-w-0">
                  <h2 className="font-bold text-sm tracking-tight truncate uppercase text-muted-foreground/80">Workspaces</h2>
                  <p className="text-[11px] text-muted-foreground/60 font-medium truncate">Select context</p>
                </div>
              </div>
            )}
          </div>

          {!sidebarCollapsed && availableKbs.length > 3 && (
            <div className="px-4 py-2 shrink-0">
              <div className="relative group">
                <Search className="pointer-events-none absolute left-3 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground/50 transition-colors group-focus-within:text-primary" />
                <Input
                  value={kbSearch}
                  onChange={(event) => setKbSearch(event.target.value)}
                  placeholder="Filter workspaces..."
                  className="h-9 pl-9 pr-3 text-xs bg-accent/30 border-transparent hover:bg-accent/50 focus:bg-background focus:ring-1 focus:ring-primary/20 transition-all"
                  aria-label="Search knowledge bases"
                />
              </div>
            </div>
          )}

          <div className={cn(
            'overflow-y-auto flex-1 min-h-0 custom-scrollbar',
            sidebarCollapsed ? 'p-3 space-y-2' : 'px-3 py-2',
          )}>
            {availableKbs.length === 0 && !sidebarCollapsed && (
              <div className="flex flex-col items-center justify-center text-center p-8 opacity-50">
                <div className="h-12 w-12 rounded-full bg-muted flex items-center justify-center mb-3">
                   <Database className="h-6 w-6" />
                </div>
                <p className="text-xs font-medium">No knowledge bases</p>
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
                  <SidebarSection label={pinnedList.length > 0 ? 'All Workspaces' : undefined}>
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
                  <div className="text-center p-8 opacity-50">
                    <p className="text-xs font-medium">No matches</p>
                  </div>
                )}
              </>
            )}
          </div>

          {!sidebarCollapsed && availableKbs.length > 0 && (
            <div className="mt-auto border-t bg-accent/20 px-4 py-3 text-[10px] font-bold uppercase tracking-wider text-muted-foreground/60 flex items-center justify-between">
              <span>{availableKbs.length} TOTAL</span>
              {pinnedList.length > 0 && <span className="text-primary/70">{pinnedList.length} PINNED</span>}
            </div>
          )}
        </aside>

        {/* Middle Area: Main Content Tabs */}
        <main className="flex-1 flex flex-col overflow-hidden min-w-0 bg-background/50 backdrop-blur-sm">
          <Tabs value={activeTab} onValueChange={setActiveTab} className="flex-1 flex flex-col overflow-hidden">
            <div className="hidden px-6 pt-6 pb-2 shrink-0 overflow-x-auto no-scrollbar md:block">
              <TabsList className="h-11 p-1 bg-accent/30 backdrop-blur-xl border border-border/50 rounded-2xl">
                <TabTrigger value="overview" icon={<Gauge />} label="Overview" />
                <TabTrigger value="documents" icon={<Workflow />} label="Workflow" />
                <TabTrigger value="ocr" icon={<ScanLine />} label="OCR" />
                <TabTrigger value="jobs" icon={<Briefcase />} label="Jobs" />
                <TabTrigger value="sessions" icon={<MessageSquare />} label="Sessions" />
                <TabTrigger value="settings" icon={<Settings />} label="Settings" />
                <TabTrigger value="quality" icon={<ShieldCheck />} label="Quality" />
                <TabTrigger value="scoring" icon={<SlidersHorizontal />} label="Scoring" />
                <TabTrigger value="usage" icon={<Activity />} label="LLM Usage" />
                <TabTrigger value="entities" icon={<Building2 />} label="Entities" />
                <TabTrigger value="market" icon={<TrendingUp />} label="Market" />
                <TabTrigger value="wiki" icon={<BookOpen />} label="Wiki" />
              </TabsList>
            </div>

            <div className={cn('flex-1 overflow-hidden px-1 pt-1 md:px-6 md:pb-6 md:pt-0', mobileNavOpen ? 'pb-16' : 'pb-3')}>
              <div className="relative h-full overflow-hidden rounded-none border-0 bg-card/50 shadow-none md:rounded-[2rem] md:border md:shadow-2xl md:shadow-foreground/5">
                <TabsContent value="overview" className="h-full m-0 outline-none data-[state=active]:flex">
                  <OverviewTab kbDir={resolvedSelectedKb} onOpenTab={setActiveTab} />
                </TabsContent>

                <TabsContent value="documents" className="h-full m-0 outline-none data-[state=active]:flex">
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

                <TabsContent value="jobs" className="h-full m-0 outline-none data-[state=active]:flex">
                  <JobsTab key={`jobs-${resolvedSelectedKb ?? 'none'}`} kbDir={resolvedSelectedKb} />
                </TabsContent>

                <TabsContent value="ocr" className="h-full m-0 outline-none data-[state=active]:flex">
                  <OcrTab key={`ocr-${resolvedSelectedKb ?? 'none'}`} kbDir={resolvedSelectedKb} />
                </TabsContent>

                <TabsContent value="sessions" keepMounted className="h-full m-0 outline-none data-[state=active]:flex">
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

                <TabsContent value="settings" className="h-full m-0 outline-none data-[state=active]:flex">
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

                <TabsContent value="quality" className="h-full m-0 outline-none data-[state=active]:flex">
                  <QualityTab
                    key={`quality-${resolvedSelectedKb ?? 'none'}`}
                    kbDir={resolvedSelectedKb}
                    onJobStarted={setGlobalActiveJobId}
                  />
                </TabsContent>

                <TabsContent value="scoring" className="h-full m-0 outline-none data-[state=active]:flex">
                  <ScoringTab
                    key={`scoring-${resolvedSelectedKb ?? 'none'}`}
                    kbDir={resolvedSelectedKb}
                  />
                </TabsContent>

                <TabsContent value="usage" className="h-full m-0 outline-none data-[state=active]:flex">
                  <LlmUsageTab
                    key={`usage-${resolvedSelectedKb ?? 'none'}`}
                    kbDir={resolvedSelectedKb}
                  />
                </TabsContent>

                <TabsContent value="entities" className="h-full m-0 outline-none data-[state=active]:flex">
                  <EntitiesTab
                    key={`entities-${resolvedSelectedKb ?? 'none'}`}
                    kbDir={resolvedSelectedKb}
                  />
                </TabsContent>

                <TabsContent value="market" className="h-full m-0 outline-none data-[state=active]:flex">
                  <MarketTab
                    key={`market-${resolvedSelectedKb ?? 'none'}`}
                    kbDir={resolvedSelectedKb}
                  />
                </TabsContent>

                <TabsContent value="wiki" className="h-full m-0 outline-none data-[state=active]:flex">
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
              </div>
            </div>
          </Tabs>
        </main>

      </div>

      <div className="pointer-events-none fixed inset-x-0 bottom-2 z-40 flex justify-center md:hidden">
        <div className="pointer-events-auto flex flex-col items-center gap-2">
          {mobileNavOpen ? (
            <nav className="rounded-2xl border bg-background/95 p-1 shadow-[0_-8px_24px_rgba(15,23,42,0.08)] backdrop-blur-xl" aria-label="Primary mobile navigation">
              <div className="grid grid-cols-4 gap-1">
                {mobileNavItems.map((tab) => {
                  const active = activeTab === tab.value;
                  const mobileLabel = tab.label === 'Workflow' ? 'Review' : tab.label;
                  return (
                    <button
                      key={tab.value}
                      type="button"
                      onClick={() => {
                        setActiveTab(tab.value);
                        setMobileNavOpen(false);
                      }}
                      className={cn(
                        'flex h-9 w-11 items-center justify-center rounded-lg transition-all active:scale-95',
                        active ? 'bg-primary/10 text-primary shadow-sm' : 'text-muted-foreground hover:bg-accent hover:text-foreground',
                      )}
                      aria-current={active ? 'page' : undefined}
                      aria-label={mobileLabel}
                    >
                      {React.cloneElement(tab.icon, { className: 'h-4.5 w-4.5' })}
                      <span className="sr-only">{mobileLabel}</span>
                    </button>
                  );
                })}
              </div>
            </nav>
          ) : null}
          <Button
            type="button"
            variant="outline"
            size="icon"
            onClick={() => setMobileNavOpen((open) => !open)}
            className="h-8 w-8 rounded-full border bg-background/95 shadow-sm backdrop-blur-xl"
            aria-label={mobileNavOpen ? 'Hide navigation' : 'Show navigation'}
            aria-expanded={mobileNavOpen}
          >
            {mobileNavOpen ? <ChevronDown className="h-4 w-4" /> : <ChevronUp className="h-4 w-4" />}
          </Button>
        </div>
      </div>

      <CommandPalette getCommands={buildCommands} />
    </div>
  );
}

function TabTrigger({ value, icon, label }: { value: string; icon: React.ReactElement<{ className?: string }>; label: string }) {
  return (
    <TabsTrigger
      value={value}
      className="gap-2 px-4 py-1.5 rounded-xl transition-all data-[state=active]:bg-background data-[state=active]:text-primary data-[state=active]:shadow-lg shadow-primary/10 text-xs font-bold uppercase tracking-wider"
    >
      {React.cloneElement(icon, { className: "h-3.5 w-3.5" })}
      {label}
    </TabsTrigger>
  );
}

function EmptyState({ message }: { message: string }) {
  return (
    <div className="flex h-full items-center justify-center text-muted-foreground p-12">
      <div className="flex flex-col items-center gap-4 text-center max-w-xs">
        <div className="flex h-16 w-16 items-center justify-center rounded-[1.5rem] bg-accent/50 text-primary shadow-xl shadow-primary/5">
          <Sparkles className="h-8 w-8" />
        </div>
        <div className="space-y-1">
          <p className="text-sm font-bold text-foreground">Awaiting Context</p>
          <p className="text-xs leading-relaxed opacity-60 font-medium">{message}</p>
        </div>
      </div>
    </div>
  );
}

function SidebarSection({ label, children }: { label?: string; children: React.ReactNode }) {
  return (
    <div className="mb-6 last:mb-0">
      {label ? (
        <div className="px-3 pb-2 text-[10px] font-bold uppercase tracking-[0.15em] text-muted-foreground/40">
          {label}
        </div>
      ) : null}
      <div className="space-y-1">{children}</div>
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
          'w-full h-12 px-0 justify-center rounded-xl transition-all',
          isActive ? 'shadow-lg shadow-primary/20 scale-105' : 'hover:bg-accent/50',
        )}
        onClick={() => onSelect(kb.path)}
        title={`${name}\n${kb.path}`}
      >
        <span className="font-bold text-base">{initial}</span>
      </Button>
    );
  }

  return (
    <div className="group/kb relative">
      <Button
        variant={isActive ? 'default' : 'ghost'}
        className={cn(
          'w-full justify-start text-left truncate gap-3 h-10 pr-10 rounded-xl transition-all border border-transparent',
          isActive
            ? 'shadow-lg shadow-primary/10 font-bold scale-[1.02] border-primary/20'
            : 'hover:bg-accent/50 font-medium text-muted-foreground hover:text-foreground',
        )}
        onClick={() => onSelect(kb.path)}
        title={kb.path}
      >
        <span
          className={cn(
            'flex h-6 w-6 items-center justify-center rounded-lg text-[10px] font-bold shrink-0 transition-colors',
            isActive
              ? 'bg-primary-foreground/20 text-primary-foreground'
              : 'bg-accent/50 text-muted-foreground group-hover/kb:bg-accent group-hover/kb:text-foreground',
          )}
        >
          {initial}
        </span>
        <span className="truncate flex-1 tracking-tight">{name}</span>
        {kb.is_default && !isActive && (
          <span className="text-[9px] uppercase tracking-tighter font-black opacity-30">DEF</span>
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
          'absolute right-2 top-1/2 -translate-y-1/2 flex h-7 w-7 items-center justify-center rounded-lg transition-all',
          isActive
            ? 'text-primary-foreground/60 hover:text-primary-foreground hover:bg-primary-foreground/10'
            : 'text-muted-foreground/30 hover:text-primary hover:bg-primary/10',
          isPinned ? 'opacity-100' : 'opacity-0 group-hover/kb:opacity-100',
        )}
      >
        {isPinned ? <Pin className="h-3.5 w-3.5 fill-current" /> : <PinOff className="h-3.5 w-3.5" />}
      </button>
    </div>
  );
}
