'use client';

import { useEffect, useMemo, useRef, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import ReactMarkdown, { type Components } from 'react-markdown';
import remarkGfm from 'remark-gfm';
import {
  ChatReference,
  ChatSessionDetail,
  createChatSession,
  deleteChatSession,
  getChatSession,
  getChats,
  streamQuery,
  StreamQueryDonePayload,
} from '@/lib/api';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { ScrollArea } from '@/components/ui/scroll-area';
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert';
import { Bot, Copy, ExternalLink, Loader2, MessageSquare, Send, Trash2, User } from 'lucide-react';
import { Skeleton } from '@/components/ui/skeleton';
import { toast } from '@/components/ui/toaster';
import { confirm as confirmDialog } from '@/components/ui/confirm-dialog';
import { cn } from '@/lib/utils';

interface SessionMessage {
  id: string;
  role: 'user' | 'assistant';
  content: string;
  pending?: boolean;
}

function sessionTitle(session: { title?: string; id?: string }) {
  return String(session.title || session.id || 'Untitled session').trim();
}

function sessionMessages(
  session: ChatSessionDetail | null,
  pendingQuestion: string,
  streamingAnswer: string,
  streamingStatus: string,
): SessionMessage[] {
  const messages: SessionMessage[] = [];
  const questions = session?.user_turns ?? [];
  const answers = session?.assistant_texts ?? [];

  questions.forEach((question, index) => {
    messages.push({
      id: `q-${index}`,
      role: 'user',
      content: question,
    });
    if (answers[index]) {
      messages.push({
        id: `a-${index}`,
        role: 'assistant',
        content: answers[index],
      });
    }
  });

  if (pendingQuestion) {
    messages.push({
      id: 'pending-user',
      role: 'user',
      content: pendingQuestion,
      pending: true,
    });
  }
  if (streamingAnswer) {
    messages.push({
      id: 'pending-assistant',
      role: 'assistant',
      content: streamingAnswer,
      pending: true,
    });
  } else if (streamingStatus) {
    messages.push({
      id: 'pending-assistant-status',
      role: 'assistant',
      content: streamingStatus,
      pending: true,
    });
  }
  return messages;
}

function referenceLabel(reference: ChatReference): string {
  if (reference.type === 'wiki_file' && reference.path) return reference.path;
  if (reference.type === 'source_pages' && reference.path) {
    return reference.pages ? `${reference.path} pages ${reference.pages}` : reference.path;
  }
  if (reference.type === 'long_document_search') {
    const parts = [
      reference.query ? `query="${reference.query}"` : '',
      reference.doc_name ? `doc="${reference.doc_name}"` : '',
    ].filter(Boolean);
    return parts.length ? `search_long_documents(${parts.join(', ')})` : 'search_long_documents';
  }
  return reference.path || reference.type || 'reference';
}

const markdownComponents: Components = {
  a: ({ children, href, ...props }) => (
    <a href={href} target="_blank" rel="noreferrer" {...props}>
      {children}
    </a>
  ),
  pre: ({ children, ...props }) => (
    <pre className="overflow-x-auto rounded-md border bg-background/80 p-3 text-xs" {...props}>
      {children}
    </pre>
  ),
  code: ({ children, className, ...props }) => (
    <code className={cn('rounded bg-foreground/10 px-1 py-0.5 text-[0.92em]', className)} {...props}>
      {children}
    </code>
  ),
};

function ChatMarkdown({ content, inverted }: { content: string; inverted: boolean }) {
  return (
    <div
      className={cn(
        'prose prose-sm max-w-none break-words prose-p:my-1 prose-ul:my-1 prose-ol:my-1 prose-pre:my-2 prose-table:my-2',
        'prose-headings:my-2 prose-headings:text-sm prose-blockquote:my-2 prose-blockquote:pl-3',
        inverted ? 'prose-invert text-primary-foreground' : 'dark:prose-invert',
      )}
    >
      <ReactMarkdown remarkPlugins={[remarkGfm]} components={markdownComponents}>
        {content}
      </ReactMarkdown>
    </div>
  );
}

export function SessionsTab({
  kbDir,
  onNavigateToWiki,
}: {
  kbDir: string;
  onNavigateToWiki?: (path: string) => void;
}) {
  const queryClient = useQueryClient();
  const [searchQuery, setSearchQuery] = useState('');
  const [activeSessionId, setActiveSessionId] = useState<string | null | undefined>(undefined);
  const [draft, setDraft] = useState('');
  const [saveQuery, setSaveQuery] = useState(false);
  const [pendingQuestion, setPendingQuestion] = useState('');
  const [streamingAnswer, setStreamingAnswer] = useState('');
  const [streamingStatus, setStreamingStatus] = useState('');
  const [activeReferences, setActiveReferences] = useState<ChatReference[]>([]);
  const [errorMessage, setErrorMessage] = useState('');
  const [isStreaming, setIsStreaming] = useState(false);
  const abortRef = useRef<AbortController | null>(null);
  const threadScrollRef = useRef<HTMLDivElement | null>(null);

  const sessionsQuery = useQuery({
    queryKey: ['chats', kbDir],
    queryFn: () => getChats(kbDir),
    enabled: !!kbDir,
  });

  const sessions = useMemo(() => sessionsQuery.data?.sessions ?? [], [sessionsQuery.data?.sessions]);
  const resolvedSessionId = activeSessionId === undefined ? sessions[0]?.id ?? null : activeSessionId;

  const sessionDetailQuery = useQuery({
    queryKey: ['chatSession', kbDir, resolvedSessionId],
    queryFn: () => getChatSession(kbDir, resolvedSessionId!),
    enabled: !!kbDir && !!resolvedSessionId,
  });

  const deleteMutation = useMutation({
    mutationFn: (sessionId: string) => deleteChatSession(kbDir, sessionId),
    onSuccess: async (_data, sessionId) => {
      if (resolvedSessionId === sessionId) {
        setActiveSessionId(undefined);
        setActiveReferences([]);
      }
      await queryClient.invalidateQueries({ queryKey: ['chats', kbDir] });
      await queryClient.invalidateQueries({ queryKey: ['chatSession', kbDir] });
      toast.success('Session deleted');
    },
    onError: (error) => {
      toast.error('Failed to delete session', error instanceof Error ? error.message : undefined);
    },
  });

  const createMutation = useMutation({
    mutationFn: () => createChatSession(kbDir),
    onSuccess: async (session) => {
      setActiveSessionId(session.id);
      setActiveReferences([]);
      setPendingQuestion('');
      setStreamingAnswer('');
      setStreamingStatus('');
      setErrorMessage('');
      setDraft('');
      await queryClient.invalidateQueries({ queryKey: ['chats', kbDir] });
      queryClient.setQueryData(['chatSession', kbDir, session.id], session);
    },
    onError: (error) => {
      toast.error('Failed to create session', error instanceof Error ? error.message : undefined);
    },
  });

  const filteredSessions = useMemo(() => {
    const query = searchQuery.trim().toLowerCase();
    if (!query) return sessions;
    return sessions.filter((session) =>
      [session.id, session.title, session.updated_at, session.model]
        .filter(Boolean)
        .join(' ')
        .toLowerCase()
        .includes(query),
    );
  }, [searchQuery, sessions]);

  const activeSession = sessionDetailQuery.data ?? null;
  const messages = useMemo(
    () => sessionMessages(activeSession, pendingQuestion, streamingAnswer, streamingStatus),
    [activeSession, pendingQuestion, streamingAnswer, streamingStatus],
  );

  useEffect(() => {
    const viewport = threadScrollRef.current?.querySelector<HTMLElement>('[data-slot="scroll-area-viewport"]');
    if (!viewport) return;
    const frame = requestAnimationFrame(() => {
      viewport.scrollTop = viewport.scrollHeight;
    });
    return () => cancelAnimationFrame(frame);
  }, [messages, resolvedSessionId, sessionDetailQuery.isLoading]);

  useEffect(() => {
    return () => {
      abortRef.current?.abort();
    };
  }, []);

  const handleNewSession = () => {
    abortRef.current?.abort();
    createMutation.mutate();
  };

  const handleSend = async (event: React.FormEvent) => {
    event.preventDefault();
    if (!kbDir || !draft.trim() || isStreaming) return;

    const question = draft.trim();
    setDraft('');
    setErrorMessage('');
    setPendingQuestion(question);
    setStreamingAnswer('');
    setStreamingStatus('Starting query...');
    setIsStreaming(true);

    const controller = new AbortController();
    abortRef.current = controller;

    try {
      await streamQuery(
        {
          kb_dir: kbDir,
          question,
          session_id: resolvedSessionId ?? undefined,
          save: saveQuery,
        },
        {
          onSession: (sessionId) => {
            if (sessionId) setActiveSessionId(sessionId);
          },
          onStatus: (message) => {
            if (message) setStreamingStatus(message);
          },
          onDelta: (text) => {
            setStreamingStatus('');
            setStreamingAnswer((current) => current + text);
          },
          onDone: async (payload: StreamQueryDonePayload) => {
            setPendingQuestion('');
            setStreamingAnswer('');
            setStreamingStatus('');
            setActiveReferences(payload.references ?? []);
            if (payload.session_id) {
              setActiveSessionId(payload.session_id);
            }
            await queryClient.invalidateQueries({ queryKey: ['chats', kbDir] });
            await queryClient.invalidateQueries({ queryKey: ['llm-usage', kbDir] });
            if (payload.session_id) {
              await queryClient.invalidateQueries({ queryKey: ['chatSession', kbDir, payload.session_id] });
            }
          },
        },
        controller.signal,
      );
    } catch (error) {
      const message = error instanceof Error ? error.message : 'Query failed';
      setErrorMessage(message);
      toast.error('Query failed', message);
    } finally {
      setPendingQuestion('');
      setStreamingAnswer('');
      setStreamingStatus('');
      setIsStreaming(false);
      abortRef.current = null;
    }
  };

  const handleDraftKeyDown = (event: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key !== 'Enter' || event.shiftKey || event.metaKey || event.ctrlKey || event.altKey) return;
    event.preventDefault();
    event.currentTarget.form?.requestSubmit();
  };

  const handleCopy = async () => {
    const lines = messages.map((message) => `${message.role === 'user' ? 'You' : 'OpenKB'}: ${message.content}`);
    try {
      await navigator.clipboard.writeText(lines.join('\n\n'));
      toast.success('Conversation copied');
    } catch (error) {
      toast.error('Copy failed', error instanceof Error ? error.message : undefined);
    }
  };

  return (
    <Card className="h-full flex flex-col rounded-none border-t-0 border-b-0 border-x-0 sm:border-x sm:rounded-lg overflow-hidden min-h-0">
      <div className="grid min-h-0 flex-1 grid-cols-[280px_minmax(0,1fr)_300px]">
        <aside className="border-r bg-muted/10">
          <CardHeader className="border-b py-4">
            <CardTitle className="flex items-center gap-2 text-sm">
              <MessageSquare className="h-4 w-4" />
              Sessions
            </CardTitle>
          </CardHeader>
          <div className="border-b p-3">
            <Input
              placeholder="Search sessions"
              value={searchQuery}
              onChange={(event) => setSearchQuery(event.target.value)}
            />
          </div>
          <ScrollArea className="h-[calc(100%-105px)]">
            <div className="space-y-1 p-2">
              <Button
                variant="outline"
                className="w-full justify-start"
                onClick={handleNewSession}
                disabled={createMutation.isPending || isStreaming}
              >
                {createMutation.isPending ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : null}
                New Session
              </Button>
              {sessionsQuery.isLoading ? (
                <div className="space-y-2 px-1 pt-2">
                  {Array.from({ length: 5 }).map((_, i) => (
                    <div key={i} className="rounded-md border border-border/60 px-3 py-2">
                      <Skeleton className="h-3.5 w-3/4" />
                      <Skeleton className="mt-2 h-3 w-1/2" />
                    </div>
                  ))}
                </div>
              ) : filteredSessions.length ? (
                filteredSessions.map((session) => (
                  <Button
                    key={session.id}
                    variant={resolvedSessionId === session.id ? 'secondary' : 'ghost'}
                    className="h-auto w-full justify-start px-3 py-2 text-left"
                    onClick={() => {
                      setActiveSessionId(session.id);
                      setActiveReferences([]);
                      setErrorMessage('');
                    }}
                  >
                    <div className="min-w-0">
                      <div className="truncate font-medium">{sessionTitle(session)}</div>
                      <div className="mt-1 text-xs text-muted-foreground">
                        {session.turn_count} turns · {session.updated_at || 'n/a'}
                      </div>
                    </div>
                  </Button>
                ))
              ) : (
                <div className="p-4 text-sm text-muted-foreground">No sessions found.</div>
              )}
            </div>
          </ScrollArea>
        </aside>

        <section className="flex min-h-0 flex-col">
          <div className="flex items-center justify-between border-b px-5 py-4">
            <div>
              <h3 className="font-semibold">{activeSession ? sessionTitle(activeSession) : 'Knowledge Chat'}</h3>
              <p className="text-sm text-muted-foreground">
                {activeSession ? `${activeSession.turn_count} turns` : 'Ask the knowledge base or reopen a session'}
              </p>
            </div>
            <div className="flex gap-2">
              <Button variant="outline" onClick={handleCopy} disabled={!messages.length}>
                <Copy className="mr-2 h-4 w-4" />
                Copy
              </Button>
              {activeSession ? (
                <Button
                  variant="destructive"
                  onClick={async () => {
                    const ok = await confirmDialog({
                      title: 'Delete session?',
                      description: `"${sessionTitle(activeSession)}" will be removed permanently.`,
                      confirmLabel: 'Delete',
                      variant: 'danger',
                    });
                    if (ok) deleteMutation.mutate(activeSession.id);
                  }}
                  disabled={deleteMutation.isPending}
                >
                  <Trash2 className="mr-2 h-4 w-4" />
                  Delete
                </Button>
              ) : null}
            </div>
          </div>

          {errorMessage ? (
            <div className="px-5 pt-4">
              <Alert variant="destructive">
                <AlertTitle>Session request failed</AlertTitle>
                <AlertDescription>{errorMessage}</AlertDescription>
              </Alert>
            </div>
          ) : null}

          <div className="min-h-0 flex-1">
            <ScrollArea ref={threadScrollRef} className="h-full">
              <div className="space-y-5 p-5">
                {sessionDetailQuery.isLoading ? (
                  <div className="space-y-4">
                    {[0, 1, 2].map((i) => (
                      <div key={i} className={i % 2 === 0 ? 'flex justify-end' : 'flex'}>
                        <div className="max-w-[70%] space-y-2 rounded-xl bg-muted/50 p-3">
                          <Skeleton className="h-3 w-32" />
                          <Skeleton className="h-3 w-48" />
                          <Skeleton className="h-3 w-40" />
                        </div>
                      </div>
                    ))}
                  </div>
                ) : messages.length ? (
                  messages.map((message) => (
                    <div
                      key={message.id}
                      className={`flex gap-3 ${message.role === 'user' ? 'justify-end' : 'justify-start'}`}
                    >
                      {message.role === 'assistant' ? (
                        <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-primary/10">
                          <Bot className="h-4 w-4 text-primary" />
                        </div>
                      ) : null}
                      <div
                        className={`max-w-[85%] rounded-xl px-4 py-3 text-sm ${
                          message.role === 'user'
                            ? 'bg-primary text-primary-foreground'
                            : 'bg-muted/60 text-foreground'
                        }`}
                      >
                        <ChatMarkdown content={message.content} inverted={message.role === 'user'} />
                        {message.pending ? <span className="ml-1 inline-block h-4 w-1.5 animate-pulse bg-current align-middle" /> : null}
                      </div>
                      {message.role === 'user' ? (
                        <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-muted">
                          <User className="h-4 w-4 text-muted-foreground" />
                        </div>
                      ) : null}
                    </div>
                  ))
                ) : (
                  <div className="flex min-h-[300px] flex-col items-center justify-center text-center text-muted-foreground">
                    <Bot className="mb-4 h-12 w-12 opacity-20" />
                    <p className="font-medium">Select a session or ask a question</p>
                    <p className="mt-1 max-w-md text-sm">
                      Answers will continue in session history and can be reopened from the left sidebar.
                    </p>
                  </div>
                )}
              </div>
            </ScrollArea>
          </div>

          <form onSubmit={handleSend} className="border-t px-5 py-4">
            <textarea
              rows={3}
              value={draft}
              onChange={(event) => setDraft(event.target.value)}
              onKeyDown={handleDraftKeyDown}
              placeholder="Ask this knowledge base"
              className="min-h-[88px] w-full rounded-xl border border-border bg-background px-3 py-2 text-sm outline-none ring-offset-background placeholder:text-muted-foreground focus-visible:ring-2 focus-visible:ring-ring"
              disabled={!kbDir || isStreaming}
            />
            <div className="mt-3 flex items-center justify-between">
              <label className="flex items-center gap-2 text-sm text-muted-foreground">
                <input
                  type="checkbox"
                  checked={saveQuery}
                  onChange={(event) => setSaveQuery(event.target.checked)}
                  disabled={isStreaming}
                />
                Save exploration
              </label>
              <Button type="submit" disabled={!kbDir || !draft.trim() || isStreaming}>
                {isStreaming ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <Send className="mr-2 h-4 w-4" />}
                Send
              </Button>
            </div>
          </form>
        </section>

        <aside className="border-l bg-[rgba(27,52,42,0.02)]">
          <CardHeader className="border-b py-4">
            <CardTitle className="text-sm">Details</CardTitle>
          </CardHeader>
          <CardContent className="space-y-5 p-4">
            <div className="rounded-xl border bg-background p-4">
              <div className="mb-2 text-xs font-semibold uppercase tracking-[0.14em] text-muted-foreground">Session</div>
              {activeSession ? (
                <div className="space-y-2 text-sm">
                  <div>ID: {activeSession.id}</div>
                  <div>Updated: {activeSession.updated_at || 'n/a'}</div>
                  <div>Turns: {activeSession.turn_count}</div>
                  <div>Model: {activeSession.model || 'n/a'}</div>
                </div>
              ) : (
                <div className="text-sm text-muted-foreground">No active session selected.</div>
              )}
            </div>

            <div className="rounded-xl border bg-background p-4">
              <div className="mb-2 text-xs font-semibold uppercase tracking-[0.14em] text-muted-foreground">References</div>
              {activeReferences.length ? (
                <div className="space-y-1.5 text-sm">
                  {activeReferences.map((reference, index) => {
                    const label = referenceLabel(reference);
                    const isWikiNav = reference.type === 'wiki_file' && !!reference.path && !!onNavigateToWiki;
                    if (isWikiNav) {
                      return (
                        <button
                          key={`${reference.type}-${reference.path}-${index}`}
                          type="button"
                          onClick={() => onNavigateToWiki!(reference.path!)}
                          title={`Open ${reference.path} in Wiki`}
                          className="group flex w-full items-center gap-2 rounded-lg bg-muted/40 px-3 py-2 text-left transition-colors hover:bg-primary/10 hover:text-primary focus-visible:bg-primary/10 focus-visible:text-primary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/50"
                        >
                          <span className="min-w-0 flex-1 truncate font-mono text-xs">{label}</span>
                          <ExternalLink className="h-3.5 w-3.5 shrink-0 opacity-60 transition-opacity group-hover:opacity-100" />
                        </button>
                      );
                    }
                    return (
                      <div
                        key={`${reference.type}-${reference.path}-${index}`}
                        className="rounded-lg bg-muted/40 px-3 py-2 font-mono text-xs"
                      >
                        {label}
                      </div>
                    );
                  })}
                </div>
              ) : (
                <div className="text-sm text-muted-foreground">No references captured for the latest answer.</div>
              )}
            </div>
          </CardContent>
        </aside>
      </div>
    </Card>
  );
}
