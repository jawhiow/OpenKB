'use client';

import { useEffect, useMemo, useRef, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import axios from 'axios';
import ReactMarkdown, { type Components } from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { Card, CardHeader, CardTitle } from '@/components/ui/card';
import { ScrollArea } from '@/components/ui/scroll-area';
import {
  ChevronDown,
  ChevronRight,
  FileText,
  Folder,
  FolderTree,
  List,
  Search,
  X,
} from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Skeleton } from '@/components/ui/skeleton';
import { cn } from '@/lib/utils';
import { usePersistentState } from '@/lib/use-persistent-state';

interface WikiFileNode {
  path: string;
  name: string;
  directory: string;
  depth: number;
  extension: string;
  size: number;
  modified: string;
}

interface TocItem {
  id: string;
  text: string;
  level: 1 | 2 | 3;
}

const FILES_PER_GROUP_INITIAL = 50;

/** Convert a heading string to a URL-safe slug. */
function slugify(text: string): string {
  return text
    .toLowerCase()
    .trim()
    .replace(/[^\p{L}\p{N}\s-]/gu, '')
    .replace(/\s+/g, '-')
    .replace(/-+/g, '-')
    .replace(/^-|-$/g, '');
}

/** Extract H1/H2/H3 from raw markdown (skipping fenced code). */
function extractToc(markdown: string): TocItem[] {
  const lines = markdown.split('\n');
  const items: TocItem[] = [];
  const slugCounts = new Map<string, number>();
  let inFence = false;

  for (const raw of lines) {
    const line = raw.trimEnd();
    if (/^\s*```/.test(line)) {
      inFence = !inFence;
      continue;
    }
    if (inFence) continue;
    const match = /^(#{1,3})\s+(.+?)\s*#*\s*$/.exec(line);
    if (!match) continue;
    const level = match[1].length as 1 | 2 | 3;
    const text = match[2].replace(/`/g, '').replace(/\*\*?/g, '').trim();
    if (!text) continue;
    const base = slugify(text) || `heading-${items.length + 1}`;
    const count = slugCounts.get(base) ?? 0;
    slugCounts.set(base, count + 1);
    const id = count === 0 ? base : `${base}-${count}`;
    items.push({ id, text, level });
  }
  return items;
}

/** React-markdown overrides that stamp ids matching extractToc. */
function buildMarkdownComponents(toc: TocItem[]): Components {
  const queue = new Map<number, string[]>();
  for (let level = 1 as 1 | 2 | 3; level <= 3; level += 1) {
    queue.set(level, toc.filter((item) => item.level === level).map((item) => item.id));
  }
  const nextId = (level: 1 | 2 | 3) => queue.get(level)?.shift();
  return {
    h1: ({ children, ...props }) => (
      <h1 id={nextId(1)} className="scroll-mt-6" {...props}>
        {children}
      </h1>
    ),
    h2: ({ children, ...props }) => (
      <h2 id={nextId(2)} className="scroll-mt-6" {...props}>
        {children}
      </h2>
    ),
    h3: ({ children, ...props }) => (
      <h3 id={nextId(3)} className="scroll-mt-6" {...props}>
        {children}
      </h3>
    ),
  };
}

interface FileGroup {
  /** Stable key for this directory ('' = root). */
  directory: string;
  /** Display label (e.g. "Root" / "concepts/ml"). */
  label: string;
  files: WikiFileNode[];
}

/** Group files by their `directory`; preserve backend sort order; root first. */
function groupFiles(files: WikiFileNode[]): FileGroup[] {
  const map = new Map<string, WikiFileNode[]>();
  for (const file of files) {
    const dir = file.directory ?? '';
    const bucket = map.get(dir);
    if (bucket) bucket.push(file);
    else map.set(dir, [file]);
  }
  const groups: FileGroup[] = [];
  for (const [directory, list] of map.entries()) {
    groups.push({
      directory,
      label: directory || 'Root',
      files: list,
    });
  }
  // Root first, then alphabetical by directory.
  groups.sort((a, b) => {
    if (a.directory === '' && b.directory !== '') return -1;
    if (b.directory === '' && a.directory !== '') return 1;
    return a.directory.localeCompare(b.directory);
  });
  return groups;
}

export function WikiTab({ kbDir, initialPath }: { kbDir: string; initialPath?: string | null }) {
  const [selectedFilePath, setSelectedFilePath] = useState<string | null>(initialPath ?? null);
  const [lastInitialPath, setLastInitialPath] = useState<string | null>(initialPath ?? null);
  const [activeHeadingId, setActiveHeadingId] = useState<string | null>(null);
  const [search, setSearch] = useState('');
  const [collapsedGroups, setCollapsedGroups] = usePersistentState<string[]>(
    `openkb:wiki-collapsed:${kbDir}`,
    [],
  );
  const [expandedListGroups, setExpandedListGroups] = useState<Set<string>>(new Set());
  const scrollRef = useRef<HTMLDivElement | null>(null);

  // Sync external initialPath (e.g., from Workflow detail → Wiki) without setState-in-effect.
  if (initialPath !== lastInitialPath) {
    setLastInitialPath(initialPath ?? null);
    if (initialPath && initialPath !== selectedFilePath) {
      setSelectedFilePath(initialPath);
    }
  }

  const { data: treeData, isLoading: isLoadingTree } = useQuery({
    queryKey: ['wikiTree', kbDir],
    queryFn: async () => {
      const res = await axios.get('/api/wiki/tree', { params: { kb_dir: kbDir } });
      return res.data;
    },
    enabled: !!kbDir,
  });

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

  const allFiles: WikiFileNode[] = useMemo(() => treeData?.files ?? [], [treeData?.files]);
  const trimmedSearch = search.trim().toLowerCase();
  const filteredFiles = useMemo(() => {
    if (!trimmedSearch) return allFiles;
    return allFiles.filter(
      (file) =>
        file.name.toLowerCase().includes(trimmedSearch) ||
        file.path.toLowerCase().includes(trimmedSearch),
    );
  }, [allFiles, trimmedSearch]);

  const groups = useMemo(() => groupFiles(filteredFiles), [filteredFiles]);

  // Auto-open the group containing the currently selected file.
  const collapsedSet = useMemo(() => new Set(collapsedGroups), [collapsedGroups]);
  const selectedDir = useMemo(() => {
    if (!selectedFilePath) return null;
    const file = allFiles.find((f) => f.path === selectedFilePath);
    return file?.directory ?? null;
  }, [selectedFilePath, allFiles]);

  const isGroupOpen = (directory: string): boolean => {
    if (trimmedSearch) return true; // expand all while searching
    if (directory === selectedDir) return true; // keep selected file visible
    return !collapsedSet.has(directory);
  };

  const toggleGroup = (directory: string) => {
    setCollapsedGroups((current) =>
      current.includes(directory)
        ? current.filter((d) => d !== directory)
        : [...current, directory],
    );
  };

  const markdown: string = fileData?.content ?? '';
  const toc = useMemo(() => extractToc(markdown), [markdown]);
  const markdownComponents = useMemo(() => buildMarkdownComponents(toc), [toc]);

  // Reset spy state when switching file.
  const fileKey = `${kbDir}|${selectedFilePath ?? ''}`;
  const [lastFileKey, setLastFileKey] = useState(fileKey);
  if (fileKey !== lastFileKey) {
    setLastFileKey(fileKey);
    setActiveHeadingId(toc[0]?.id ?? null);
    setExpandedListGroups(new Set()); // collapse "Show more" expansions when navigating
  }

  // Scroll the markdown panel back to the top whenever the selected file changes.
  useEffect(() => {
    if (scrollRef.current) scrollRef.current.scrollTop = 0;
  }, [fileKey]);

  // Scroll-spy: keep a set of currently visible headings; the topmost (by ToC order) wins.
  useEffect(() => {
    if (toc.length === 0) return;
    const root = scrollRef.current;
    if (!root) return;

    const visible = new Set<string>();
    const observer = new IntersectionObserver(
      (entries) => {
        for (const entry of entries) {
          const id = (entry.target as HTMLElement).id;
          if (!id) continue;
          if (entry.isIntersecting) visible.add(id);
          else visible.delete(id);
        }
        // Pick the first heading (in document order) that is still visible.
        for (const item of toc) {
          if (visible.has(item.id)) {
            setActiveHeadingId(item.id);
            return;
          }
        }
        // Nothing in viewport (e.g. between sections): leave current active untouched.
      },
      {
        root,
        // Trigger when a heading crosses the top 25% line; tighter than before so scrolling
        // updates the active row before the heading slides off-screen.
        rootMargin: '0px 0px -75% 0px',
        threshold: [0, 1],
      },
    );

    const elements: Element[] = [];
    for (const item of toc) {
      const el = root.querySelector(`#${CSS.escape(item.id)}`);
      if (el) {
        observer.observe(el);
        elements.push(el);
      }
    }
    return () => {
      for (const el of elements) observer.unobserve(el);
      observer.disconnect();
    };
  }, [toc, selectedFilePath]);

  const handleTocClick = (id: string) => {
    const root = scrollRef.current;
    if (!root) return;
    const el = root.querySelector<HTMLElement>(`#${CSS.escape(id)}`);
    if (!el) return;
    el.scrollIntoView({ behavior: 'smooth', block: 'start' });
    setActiveHeadingId(id);
  };

  const totalFiles = allFiles.length;
  const visibleFileCount = filteredFiles.length;

  return (
    <Card className="h-full flex flex-col rounded-none border-t-0 border-b-0 border-x-0 sm:border-x sm:rounded-lg overflow-hidden py-0 gap-0">
      <div className="flex flex-1 overflow-hidden min-h-0">
        {/* Left: file browser */}
        <div className="w-1/4 min-w-[260px] border-r bg-muted/10 flex flex-col shrink-0 min-h-0">
          <CardHeader className="py-3 border-b shrink-0">
            <CardTitle className="text-sm flex items-center justify-between gap-2">
              <span className="flex items-center gap-2">
                <FolderTree className="w-4 h-4" />
                Wiki Index
              </span>
              <span className="text-[11px] font-normal text-muted-foreground tabular-nums">
                {trimmedSearch ? `${visibleFileCount}/${totalFiles}` : `${totalFiles} file${totalFiles === 1 ? '' : 's'}`}
              </span>
            </CardTitle>
          </CardHeader>

          <div className="border-b p-2 shrink-0">
            <div className="relative">
              <Search className="pointer-events-none absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground" />
              <Input
                value={search}
                onChange={(event) => setSearch(event.target.value)}
                placeholder="Search files"
                className="h-8 pl-8 pr-7 text-xs"
                aria-label="Search wiki files"
              />
              {search ? (
                <button
                  type="button"
                  onClick={() => setSearch('')}
                  aria-label="Clear search"
                  className="absolute right-1.5 top-1/2 flex h-5 w-5 -translate-y-1/2 items-center justify-center rounded text-muted-foreground transition-colors hover:bg-foreground/10 hover:text-foreground"
                >
                  <X className="h-3 w-3" />
                </button>
              ) : null}
            </div>
          </div>

          <ScrollArea className="flex-1 min-h-0 overflow-hidden">
            {isLoadingTree ? (
              <div className="space-y-1.5 p-2">
                {Array.from({ length: 10 }).map((_, i) => (
                  <div key={i} className="flex items-center gap-2 px-2 py-1.5">
                    <Skeleton className="h-3.5 w-3.5 rounded" />
                    <Skeleton
                      className="h-3"
                      style={{ width: `${50 + ((i * 17) % 40)}%` }}
                    />
                  </div>
                ))}
              </div>
            ) : groups.length === 0 ? (
              <div className="p-6 text-center text-sm text-muted-foreground">
                {trimmedSearch ? (
                  <>
                    <p>No files match &ldquo;{search}&rdquo;.</p>
                    <Button
                      type="button"
                      size="sm"
                      variant="ghost"
                      className="mt-2"
                      onClick={() => setSearch('')}
                    >
                      Clear search
                    </Button>
                  </>
                ) : (
                  'No wiki files found.'
                )}
              </div>
            ) : (
              <div className="p-1.5 space-y-0.5">
                {groups.map((group) => (
                  <FileGroupSection
                    key={group.directory || '__root__'}
                    group={group}
                    open={isGroupOpen(group.directory)}
                    onToggle={() => toggleGroup(group.directory)}
                    selectedFilePath={selectedFilePath}
                    onSelect={setSelectedFilePath}
                    expanded={expandedListGroups.has(group.directory)}
                    onExpand={() =>
                      setExpandedListGroups((current) => {
                        const next = new Set(current);
                        next.add(group.directory);
                        return next;
                      })
                    }
                  />
                ))}
              </div>
            )}
          </ScrollArea>
        </div>

        {/* Center: Markdown viewer */}
        <div className="flex-1 flex flex-col bg-background min-w-0 min-h-0">
          {isLoadingFile ? (
            <div className="flex-1 overflow-hidden p-8">
              <Skeleton className="h-8 w-1/2" />
              <Skeleton className="mt-4 h-4 w-full" />
              <Skeleton className="mt-2 h-4 w-11/12" />
              <Skeleton className="mt-2 h-4 w-9/12" />
              <Skeleton className="mt-6 h-5 w-1/3" />
              <Skeleton className="mt-3 h-4 w-full" />
              <Skeleton className="mt-2 h-4 w-10/12" />
              <Skeleton className="mt-2 h-4 w-8/12" />
            </div>
          ) : !selectedFilePath ? (
            <div className="flex-1 flex flex-col items-center justify-center text-muted-foreground">
              <FileText className="w-12 h-12 mb-4 opacity-20" />
              <p>Select a document from the index to view its content.</p>
            </div>
          ) : (
            <div ref={scrollRef} className="flex-1 min-h-0 overflow-y-auto scroll-smooth">
              <div className="p-8 prose prose-slate dark:prose-invert max-w-3xl">
                {markdown ? (
                  <ReactMarkdown remarkPlugins={[remarkGfm]} components={markdownComponents}>
                    {markdown}
                  </ReactMarkdown>
                ) : (
                  <div className="text-muted-foreground italic">Document is empty or cannot be read.</div>
                )}
              </div>
            </div>
          )}
        </div>

        {/* Right: ToC (xl+ screens) */}
        {selectedFilePath && toc.length > 1 ? (
          <aside className="hidden xl:flex w-[220px] shrink-0 flex-col border-l bg-muted/10 min-h-0">
            <div className="flex items-center gap-2 border-b px-4 py-3 text-xs font-semibold uppercase tracking-wider text-muted-foreground">
              <List className="h-3.5 w-3.5" />
              On this page
            </div>
            <ScrollArea className="flex-1 min-h-0">
              <nav className="p-2 space-y-0.5 text-xs" aria-label="Document outline">
                {toc.map((item) => (
                  <button
                    key={item.id}
                    type="button"
                    onClick={() => handleTocClick(item.id)}
                    aria-current={activeHeadingId === item.id ? 'true' : undefined}
                    className={cn(
                      'block w-full truncate rounded px-2 py-1 text-left transition-colors',
                      'hover:bg-muted hover:text-foreground',
                      'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/50',
                      activeHeadingId === item.id
                        ? 'bg-primary/10 font-medium text-primary'
                        : 'text-muted-foreground',
                    )}
                    style={{ paddingLeft: `${(item.level - 1) * 0.75 + 0.5}rem` }}
                    title={item.text}
                  >
                    {item.text}
                  </button>
                ))}
              </nav>
            </ScrollArea>
          </aside>
        ) : null}
      </div>
    </Card>
  );
}

function FileGroupSection({
  group,
  open,
  onToggle,
  selectedFilePath,
  onSelect,
  expanded,
  onExpand,
}: {
  group: FileGroup;
  open: boolean;
  onToggle: () => void;
  selectedFilePath: string | null;
  onSelect: (path: string) => void;
  expanded: boolean;
  onExpand: () => void;
}) {
  const Chevron = open ? ChevronDown : ChevronRight;
  const showAll = expanded || group.files.length <= FILES_PER_GROUP_INITIAL;
  const visibleFiles = showAll ? group.files : group.files.slice(0, FILES_PER_GROUP_INITIAL);
  const hiddenCount = group.files.length - visibleFiles.length;

  return (
    <div className="rounded">
      <button
        type="button"
        onClick={onToggle}
        aria-expanded={open}
        className={cn(
          'flex w-full items-center gap-1.5 rounded px-1.5 py-1 text-left transition-colors',
          'hover:bg-muted/70 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/50',
        )}
      >
        <Chevron className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
        <Folder className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
        <span className="min-w-0 flex-1 truncate text-xs font-medium" title={group.directory || 'Root'}>
          {group.label}
        </span>
        <span className="shrink-0 text-[10px] text-muted-foreground tabular-nums">
          {group.files.length}
        </span>
      </button>
      {open ? (
        <div className="mt-0.5 space-y-0.5 pb-1">
          {visibleFiles.map((file) => {
            const isActive = selectedFilePath === file.path;
            return (
              <button
                key={file.path}
                type="button"
                onClick={() => onSelect(file.path)}
                className={cn(
                  'flex w-full items-center gap-1.5 rounded px-1.5 py-1 text-left text-xs transition-colors',
                  isActive
                    ? 'bg-primary text-primary-foreground'
                    : 'text-foreground/80 hover:bg-muted hover:text-foreground',
                )}
                style={{ paddingLeft: '1.75rem' }}
                title={file.path}
              >
                <FileText
                  className={cn(
                    'h-3 w-3 shrink-0',
                    isActive ? 'opacity-90' : 'opacity-60',
                  )}
                />
                <span className="min-w-0 flex-1 truncate">{file.name}</span>
              </button>
            );
          })}
          {hiddenCount > 0 ? (
            <button
              type="button"
              onClick={onExpand}
              className="ml-7 mt-0.5 text-[11px] text-muted-foreground transition-colors hover:text-foreground"
            >
              Show {hiddenCount} more
            </button>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}
