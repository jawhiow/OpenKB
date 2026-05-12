'use client';

import { useEffect, useMemo, useRef, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  ChatReference,
  ChatSessionDetail,
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
import { Bot, Copy, Loader2, MessageSquare, Send, Trash2, User } from 'lucide-react';

interface SessionMessage {
  id: string;
  role: 'user' | 'assistant';
  content: string;
  pending?: boolean;
}

function sessionTitle(session: { title?: string; id?: string }) {
  return String(session.title || session.id || 'Untitled session').trim();
}

function sessionMessages(session: ChatSessionDetail | null, pendingQuestion: string, streamingAnswer: string): SessionMessage[] {
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

export function SessionsTab({ kbDir }: { kbDir: string }) {
  const queryClient = useQueryClient();
  const [searchQuery, setSearchQuery] = useState('');
  const [activeSessionId, setActiveSessionId] = useState<string | null>(null);
  const [draft, setDraft] = useState('');
  const [saveQuery, setSaveQuery] = useState(false);
  const [pendingQuestion, setPendingQuestion] = useState('');
  const [streamingAnswer, setStreamingAnswer] = useState('');
  const [activeReferences, setActiveReferences] = useState<ChatReference[]>([]);
  const [errorMessage, setErrorMessage] = useState('');
  const [isStreaming, setIsStreaming] = useState(false);
  const abortRef = useRef<AbortController | null>(null);
  const threadRef = useRef<HTMLDivElement | null>(null);

  const sessionsQuery = useQuery({
    queryKey: ['chats', kbDir],
    queryFn: () => getChats(kbDir),
    enabled: !!kbDir,
  });

  const sessions = useMemo(() => sessionsQuery.data?.sessions ?? [], [sessionsQuery.data?.sessions]);
  const resolvedSessionId = activeSessionId ?? sessions[0]?.id ?? null;

  const sessionDetailQuery = useQuery({
    queryKey: ['chatSession', kbDir, resolvedSessionId],
    queryFn: () => getChatSession(kbDir, resolvedSessionId!),
    enabled: !!kbDir && !!resolvedSessionId,
  });

  const deleteMutation = useMutation({
    mutationFn: (sessionId: string) => deleteChatSession(kbDir, sessionId),
    onSuccess: async (_data, sessionId) => {
      if (activeSessionId === sessionId) {
        setActiveSessionId(null);
        setActiveReferences([]);
      }
      await queryClient.invalidateQueries({ queryKey: ['chats', kbDir] });
      await queryClient.invalidateQueries({ queryKey: ['chatSession', kbDir] });
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
    () => sessionMessages(activeSession, pendingQuestion, streamingAnswer),
    [activeSession, pendingQuestion, streamingAnswer],
  );

  useEffect(() => {
    if (threadRef.current) {
      threadRef.current.scrollTop = threadRef.current.scrollHeight;
    }
  }, [messages]);

  useEffect(() => {
    return () => {
      abortRef.current?.abort();
    };
  }, []);

  const handleSend = async (event: React.FormEvent) => {
    event.preventDefault();
    if (!kbDir || !draft.trim() || isStreaming) return;

    const question = draft.trim();
    setDraft('');
    setErrorMessage('');
    setPendingQuestion(question);
    setStreamingAnswer('');
    setIsStreaming(true);

    const controller = new AbortController();
    abortRef.current = controller;

    try {
      await streamQuery(
        {
          kb_dir: kbDir,
          question,
          session_id: activeSessionId ?? undefined,
          save: saveQuery,
        },
        {
          onSession: (sessionId) => {
            if (sessionId) setActiveSessionId(sessionId);
          },
          onDelta: (text) => {
            setStreamingAnswer((current) => current + text);
          },
          onDone: async (payload: StreamQueryDonePayload) => {
            setPendingQuestion('');
            setStreamingAnswer('');
            setActiveReferences(payload.references ?? []);
            if (payload.session_id) {
              setActiveSessionId(payload.session_id);
            }
            await queryClient.invalidateQueries({ queryKey: ['chats', kbDir] });
            if (payload.session_id) {
              await queryClient.invalidateQueries({ queryKey: ['chatSession', kbDir, payload.session_id] });
            }
          },
        },
        controller.signal,
      );
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : 'Query failed');
    } finally {
      setPendingQuestion('');
      setStreamingAnswer('');
      setIsStreaming(false);
      abortRef.current = null;
    }
  };

  const handleCopy = async () => {
    const lines = messages.map((message) => `${message.role === 'user' ? 'You' : 'OpenKB'}: ${message.content}`);
    await navigator.clipboard.writeText(lines.join('\n\n'));
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
                onClick={() => {
                  setActiveSessionId(null);
                  setActiveReferences([]);
                }}
              >
                New Session
              </Button>
              {filteredSessions.length ? (
                filteredSessions.map((session) => (
                  <Button
                    key={session.id}
                    variant={activeSessionId === session.id ? 'secondary' : 'ghost'}
                    className="h-auto w-full justify-start px-3 py-2 text-left"
                    onClick={() => setActiveSessionId(session.id)}
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
                  onClick={() => {
                    if (confirm(`Delete session ${sessionTitle(activeSession)}?`)) {
                      deleteMutation.mutate(activeSession.id);
                    }
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
            <ScrollArea className="h-full">
              <div ref={threadRef} className="space-y-5 p-5">
                {sessionDetailQuery.isLoading ? (
                  <div className="flex justify-center py-10">
                    <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
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
                        className={`max-w-[85%] rounded-xl px-4 py-3 text-sm whitespace-pre-wrap ${
                          message.role === 'user'
                            ? 'bg-primary text-primary-foreground'
                            : 'bg-muted/60 text-foreground'
                        }`}
                      >
                        {message.content}
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
                <div className="space-y-2 text-sm">
                  {activeReferences.map((reference, index) => (
                    <div key={`${reference.type}-${reference.path}-${index}`} className="rounded-lg bg-muted/50 px-3 py-2">
                      {referenceLabel(reference)}
                    </div>
                  ))}
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
