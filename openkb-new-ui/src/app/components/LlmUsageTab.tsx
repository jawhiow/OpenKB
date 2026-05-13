'use client';

import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { AlertCircle, Download, Loader2, RefreshCcw, Search } from 'lucide-react';
import { exportLlmUsage, getLlmUsage, LlmUsageItem } from '@/lib/api';
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Input } from '@/components/ui/input';
import { ScrollArea } from '@/components/ui/scroll-area';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table';

function formatDateTime(value?: string): string {
  if (!value) return '';
  try {
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return value;
    return date.toLocaleString();
  } catch {
    return value;
  }
}

function statusTone(status?: string): string {
  const value = String(status || '').toLowerCase();
  if (value === 'ok' || value === 'success' || value === 'succeeded') {
    return 'bg-emerald-100 text-emerald-700 dark:bg-emerald-500/15 dark:text-emerald-300';
  }
  if (value === 'failed' || value === 'error') {
    return 'bg-rose-100 text-rose-700 dark:bg-rose-500/15 dark:text-rose-300';
  }
  if (value === 'cached') {
    return 'bg-blue-100 text-blue-700 dark:bg-blue-500/15 dark:text-blue-300';
  }
  return 'bg-muted text-muted-foreground';
}

function downloadCsv(items: LlmUsageItem[], filename: string) {
  if (!items.length) return;
  const headers = Array.from(
    items.reduce<Set<string>>((acc, item) => {
      Object.keys(item).forEach((key) => acc.add(key));
      return acc;
    }, new Set()),
  );
  const escapeCsv = (value: unknown): string => {
    if (value === null || value === undefined) return '';
    const str = typeof value === 'string' ? value : JSON.stringify(value);
    if (/[",\n]/.test(str)) {
      return `"${str.replace(/"/g, '""')}"`;
    }
    return str;
  };
  const rows = items.map((item) => headers.map((h) => escapeCsv((item as Record<string, unknown>)[h])).join(','));
  const csv = [headers.join(','), ...rows].join('\n');
  const blob = new Blob([csv], { type: 'text/csv;charset=utf-8;' });
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  document.body.removeChild(link);
  URL.revokeObjectURL(url);
}

export function LlmUsageTab({ kbDir }: { kbDir: string | null }) {
  const [searchInput, setSearchInput] = useState('');
  const [search, setSearch] = useState('');
  const [page, setPage] = useState(1);
  const [pageSize] = useState(50);
  const [errorMessage, setErrorMessage] = useState('');
  const [isExporting, setIsExporting] = useState(false);

  const { data, isLoading, isFetching, refetch } = useQuery({
    queryKey: ['llm-usage', kbDir, search, page, pageSize],
    queryFn: () => getLlmUsage(kbDir!, { q: search, page, page_size: pageSize }),
    enabled: !!kbDir,
    refetchInterval: 5000,
    refetchOnMount: 'always',
    refetchOnWindowFocus: true,
  });

  const handleSearch = (event: React.FormEvent) => {
    event.preventDefault();
    setSearch(searchInput.trim());
    setPage(1);
  };

  const handleRefresh = () => {
    if (page !== 1) {
      setPage(1);
      return;
    }
    refetch();
  };

  const handleExport = async () => {
    if (!kbDir) return;
    setErrorMessage('');
    setIsExporting(true);
    try {
      const exportData = await exportLlmUsage(kbDir, search);
      const stamp = new Date().toISOString().replace(/[:.]/g, '-');
      downloadCsv(exportData.items, `llm-usage-${stamp}.csv`);
    } catch (error) {
      setErrorMessage((error as Error).message);
    } finally {
      setIsExporting(false);
    }
  };

  if (!kbDir) {
    return (
      <div className="flex h-full items-center justify-center text-muted-foreground text-sm">
        Select a knowledge base to view LLM usage records.
      </div>
    );
  }

  const items = data?.items ?? [];
  const total = data?.total ?? 0;
  const pages = data?.pages ?? 1;
  const start = data?.start ?? 0;
  const end = data?.end ?? 0;

  return (
    <Card className="h-full flex flex-col overflow-hidden py-0 gap-0 border-border/70 shadow-sm">
      <CardHeader className="shrink-0 border-b bg-muted/20 py-4">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <CardTitle className="text-base">LLM Usage</CardTitle>
            <CardDescription className="text-xs">{total} record(s)</CardDescription>
          </div>
          <div className="flex items-center gap-2">
            <Button variant="outline" size="sm" onClick={handleRefresh} disabled={isFetching}>
              {isFetching ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <RefreshCcw className="h-3.5 w-3.5" />}
              Refresh
            </Button>
            <Button size="sm" onClick={handleExport} disabled={isExporting || items.length === 0}>
              {isExporting ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Download className="h-3.5 w-3.5" />}
              Export CSV
            </Button>
          </div>
        </div>

        <form onSubmit={handleSearch} className="mt-3 flex flex-wrap items-center gap-2">
          <div className="relative flex-1 min-w-[200px] max-w-md">
            <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 h-3.5 w-3.5 text-muted-foreground" />
            <Input
              type="search"
              placeholder="Search feature, model, status, or error"
              value={searchInput}
              onChange={(event) => setSearchInput(event.target.value)}
              className="h-8 pl-8 text-sm"
            />
          </div>
          <Button type="submit" size="sm" variant="secondary">
            Search
          </Button>
          {search ? (
            <span className="text-xs text-muted-foreground">
              Filter: <strong className="text-foreground">{search}</strong>
            </span>
          ) : null}
          {total > 0 ? (
            <span className="text-xs text-muted-foreground ml-auto">
              Showing {start}-{end} of {total}
            </span>
          ) : null}
        </form>

        {errorMessage ? (
          <Alert variant="destructive" className="mt-3">
            <AlertCircle className="h-4 w-4" />
            <AlertTitle>Export failed</AlertTitle>
            <AlertDescription>{errorMessage}</AlertDescription>
          </Alert>
        ) : null}
      </CardHeader>

      <CardContent className="flex-1 overflow-hidden p-0 flex flex-col min-h-0">
        <ScrollArea className="flex-1 min-h-0 overflow-hidden">
          {isLoading ? (
            <div className="p-8 flex justify-center">
              <Loader2 className="h-4 w-4 animate-spin text-muted-foreground" />
            </div>
          ) : items.length === 0 ? (
            <div className="p-8 text-center text-sm text-muted-foreground">No usage records.</div>
          ) : (
            <Table>
              <TableHeader className="sticky top-0 z-10 bg-background">
                <TableRow>
                  <TableHead className="w-44">Time</TableHead>
                  <TableHead>Feature</TableHead>
                  <TableHead>Model</TableHead>
                  <TableHead className="w-24">API</TableHead>
                  <TableHead className="w-24 text-right">Duration</TableHead>
                  <TableHead className="w-24">Status</TableHead>
                  <TableHead>Error</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {items.map((item, index) => (
                  <TableRow key={(item.id as string | number) ?? `${item.created_at}-${index}`}>
                    <TableCell className="text-xs whitespace-nowrap">{formatDateTime(item.created_at)}</TableCell>
                    <TableCell className="text-sm">{item.feature || '—'}</TableCell>
                    <TableCell className="text-sm font-mono text-xs">{item.model || '—'}</TableCell>
                    <TableCell className="text-xs">{item.wire_api || '—'}</TableCell>
                    <TableCell className="text-xs text-right tabular-nums">
                      {item.duration_ms !== undefined ? `${item.duration_ms} ms` : '—'}
                    </TableCell>
                    <TableCell>
                      <span
                        className={`text-[10px] uppercase tracking-wider px-1.5 py-0.5 rounded ${statusTone(item.status)}`}
                      >
                        {item.status || '—'}
                      </span>
                    </TableCell>
                    <TableCell className="text-xs text-muted-foreground max-w-xs truncate" title={item.error || ''}>
                      {item.error || ''}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
        </ScrollArea>

        {pages > 1 ? (
          <div className="border-t bg-muted/10 px-4 py-2 flex items-center justify-between gap-2 shrink-0">
            <span className="text-xs text-muted-foreground">
              Page {page} of {pages}
            </span>
            <div className="flex items-center gap-1">
              <Button
                variant="ghost"
                size="sm"
                onClick={() => setPage(1)}
                disabled={page === 1}
                className="h-7 px-2"
              >
                First
              </Button>
              <Button
                variant="ghost"
                size="sm"
                onClick={() => setPage((p) => Math.max(1, p - 1))}
                disabled={page === 1}
                className="h-7 px-2"
              >
                Prev
              </Button>
              <Button
                variant="ghost"
                size="sm"
                onClick={() => setPage((p) => Math.min(pages, p + 1))}
                disabled={page >= pages}
                className="h-7 px-2"
              >
                Next
              </Button>
              <Button
                variant="ghost"
                size="sm"
                onClick={() => setPage(pages)}
                disabled={page >= pages}
                className="h-7 px-2"
              >
                Last
              </Button>
            </div>
          </div>
        ) : null}
      </CardContent>
    </Card>
  );
}
