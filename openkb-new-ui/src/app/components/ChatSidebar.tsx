'use client';

import { useState, useRef, useEffect } from 'react';
import { streamQuery } from '@/lib/api';
import { Card, CardContent, CardHeader, CardTitle, CardFooter } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { ScrollArea } from '@/components/ui/scroll-area';
import { Send, Loader2, Bot, User } from 'lucide-react';

interface ChatMessage {
  id: string;
  role: 'user' | 'assistant';
  content: string;
  isStreaming?: boolean;
}

export function ChatSidebar({ kbDir }: { kbDir: string }) {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState('');
  const [isTyping, setIsTyping] = useState(false);
  const scrollRef = useRef<HTMLDivElement>(null);
  const abortRef = useRef<AbortController | null>(null);

  // Auto-scroll to bottom
  useEffect(() => {
    if (scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, [messages]);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!input.trim() || !kbDir || isTyping) return;

    const userMessage: ChatMessage = {
      id: Date.now().toString(),
      role: 'user',
      content: input.trim()
    };

    setMessages(prev => [...prev, userMessage]);
    setInput('');
    setIsTyping(true);

    const assistantMsgId = (Date.now() + 1).toString();
    setMessages(prev => [...prev, {
      id: assistantMsgId,
      role: 'assistant',
      content: '',
      isStreaming: true
    }]);

    const controller = new AbortController();
    abortRef.current = controller;

    try {
      await streamQuery(
        {
          kb_dir: kbDir,
          question: userMessage.content,
        },
        {
          onDelta: (text) => {
            setMessages(prev => prev.map(msg =>
              msg.id === assistantMsgId
                ? { ...msg, content: msg.content + text }
                : msg
            ));
          },
          onDone: () => {
            setMessages(prev => prev.map(msg =>
              msg.id === assistantMsgId
                ? { ...msg, isStreaming: false }
                : msg
            ));
            setIsTyping(false);
            abortRef.current = null;
          },
        },
        controller.signal,
      );
    } catch (error) {
      console.error("Failed to start chat stream:", error);
      setMessages(prev => prev.map(msg =>
        msg.id === assistantMsgId
          ? {
              ...msg,
              isStreaming: false,
              content: msg.content + "\n\n*[Error: Query request failed]*",
            }
          : msg
      ));
      setIsTyping(false);
      abortRef.current = null;
    }
  };

  useEffect(() => {
    return () => {
      abortRef.current?.abort();
    };
  }, []);

  return (
    <Card className="h-full flex flex-col rounded-none border-t-0 border-b-0 border-r-0">
      <CardHeader className="py-4 border-b">
        <CardTitle className="text-lg flex items-center gap-2">
          <Bot className="w-5 h-5" />
          Assistant
        </CardTitle>
      </CardHeader>

      <CardContent className="flex-1 p-0 overflow-hidden">
        <ScrollArea className="h-full px-4 py-4" ref={scrollRef}>
          {!kbDir ? (
            <div className="h-full flex items-center justify-center text-muted-foreground text-sm">
              Please select a Knowledge Base first.
            </div>
          ) : messages.length === 0 ? (
            <div className="h-full flex flex-col items-center justify-center text-muted-foreground space-y-4">
              <Bot className="w-12 h-12 opacity-20" />
              <p className="text-sm">Ask questions about your documents.</p>
            </div>
          ) : (
            <div className="space-y-6">
              {messages.map((msg) => (
                <div
                  key={msg.id}
                  className={`flex gap-3 ${msg.role === 'user' ? 'justify-end' : 'justify-start'}`}
                >
                  {msg.role === 'assistant' && (
                    <div className="w-8 h-8 rounded-full bg-primary/10 flex items-center justify-center shrink-0">
                      <Bot className="w-4 h-4 text-primary" />
                    </div>
                  )}
                  <div
                    className={`rounded-lg px-4 py-2 max-w-[85%] text-sm ${
                      msg.role === 'user'
                        ? 'bg-primary text-primary-foreground'
                        : 'bg-muted/50 whitespace-pre-wrap'
                    }`}
                  >
                    {msg.content}
                    {msg.isStreaming && (
                      <span className="inline-block w-1.5 h-4 ml-1 bg-current animate-pulse align-middle" />
                    )}
                  </div>
                  {msg.role === 'user' && (
                    <div className="w-8 h-8 rounded-full bg-muted flex items-center justify-center shrink-0">
                      <User className="w-4 h-4 text-muted-foreground" />
                    </div>
                  )}
                </div>
              ))}
            </div>
          )}
        </ScrollArea>
      </CardContent>

      <CardFooter className="p-4 border-t">
        <form onSubmit={handleSubmit} className="flex w-full gap-2">
          <Input
            placeholder="Ask a question..."
            value={input}
            onChange={(e) => setInput(e.target.value)}
            disabled={!kbDir || isTyping}
            className="flex-1"
          />
          <Button type="submit" size="icon" disabled={!input.trim() || !kbDir || isTyping}>
            {isTyping ? <Loader2 className="w-4 h-4 animate-spin" /> : <Send className="w-4 h-4" />}
          </Button>
        </form>
      </CardFooter>
    </Card>
  );
}
