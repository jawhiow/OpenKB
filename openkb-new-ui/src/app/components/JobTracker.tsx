'use client';

import { useEffect, useRef } from 'react';
import { useQuery } from '@tanstack/react-query';
import axios from 'axios';
import { Loader2, CheckCircle2, XCircle, X } from 'lucide-react';
import { Progress } from '@/components/ui/progress';
import { notifyIfBackground } from '@/lib/notifications';

interface JobTrackerProps {
  jobId: string | null;
  onComplete?: () => void;
  onTerminal?: () => void;
  onDismiss?: () => void;
}

export function GlobalJobTracker({ jobId, onComplete, onTerminal, onDismiss }: JobTrackerProps) {
  const completionNotifiedRef = useRef<string | null>(null);

  const { data: job, isError } = useQuery({
    queryKey: ['job', jobId],
    queryFn: async () => {
      if (!jobId) return null;
      const res = await axios.get(`/api/jobs/${jobId}`);
      return res.data;
    },
    enabled: !!jobId,
    refetchInterval: (query) => {
      const status = query.state.data?.status;
      if (status === 'succeeded' || status === 'failed' || status === 'stopped') {
        return false;
      }
      return 1000;
    },
  });

  useEffect(() => {
    completionNotifiedRef.current = null;
  }, [jobId]);

  useEffect(() => {
    if (!job?.status || !jobId) return;
    if (completionNotifiedRef.current === jobId) return;

    const terminal = job.status === 'succeeded' || job.status === 'failed' || job.status === 'stopped';
    if (!terminal) return;

    completionNotifiedRef.current = jobId;
    onTerminal?.();
    const jobType = (job.type || 'Job').replace(/_/g, ' ');
    if (job.status === 'succeeded') {
      onComplete?.();
      void notifyIfBackground(`✓ ${jobType} complete`, {
        body: job.message || 'Background job finished successfully.',
        tag: `openkb-job-${jobId}`,
      });
    } else if (job.status === 'failed') {
      void notifyIfBackground(`✗ ${jobType} failed`, {
        body: job.error || job.message || 'Background job failed.',
        tag: `openkb-job-${jobId}`,
      });
    } else if (job.status === 'stopped') {
      void notifyIfBackground(`■ ${jobType} stopped`, {
        body: 'Background job was stopped.',
        tag: `openkb-job-${jobId}`,
      });
    }
  }, [job?.status, job?.type, job?.message, job?.error, jobId, onComplete, onTerminal]);

  if (!jobId && !job) return null;

  const isCompleted = job?.status === 'succeeded';
  const isFailed = job?.status === 'failed' || isError;
  const isStopped = job?.status === 'stopped';
  const isRunning = !isCompleted && !isFailed && !isStopped && job;

  const progressCurrent = job?.progress?.current ?? 0;
  const progressTotal = job?.progress?.total ?? 0;
  // Progress requires `number | null`
  const progressValue: number | null = progressTotal > 0 ? (progressCurrent / progressTotal) * 100 : (isRunning ? null : 0);

  // Styling based on status
  let bgColor = 'bg-blue-50/80 border-blue-200';
  let textColor = 'text-blue-700';
  let Icon = Loader2;
  let iconClass = 'animate-spin text-blue-500';

  if (isCompleted) {
    bgColor = 'bg-green-50/80 border-green-200';
    textColor = 'text-green-700';
    Icon = CheckCircle2;
    iconClass = 'text-green-500';
  } else if (isFailed) {
    bgColor = 'bg-red-50/80 border-red-200';
    textColor = 'text-red-700';
    Icon = XCircle;
    iconClass = 'text-red-500';
  } else if (isStopped) {
    bgColor = 'bg-yellow-50/80 border-yellow-200';
    textColor = 'text-yellow-700';
    Icon = XCircle;
    iconClass = 'text-yellow-500';
  }

  return (
    <div className={`flex items-center gap-3 px-4 py-1.5 rounded-full border shadow-sm text-sm backdrop-blur-sm transition-all duration-300 ${bgColor}`}>
      <Icon className={`w-4 h-4 shrink-0 ${iconClass}`} />

      <div className="flex flex-col min-w-[150px] max-w-[300px]">
        <div className="flex justify-between items-center gap-4">
          <span className={`font-medium capitalize truncate ${textColor}`}>
            {job?.type || 'Processing'} {job?.status !== 'running' ? job?.status : ''}
          </span>
          {isRunning && progressTotal > 0 && (
            <span className="text-xs text-blue-600 font-medium">
              {Math.round(progressValue || 0)}%
            </span>
          )}
        </div>

        {isRunning && (
          <div className="mt-1">
            <Progress value={progressValue} className="h-1" />
            <div className="text-[10px] text-blue-600 truncate mt-0.5" title={job?.message || ''}>
              {job?.message || 'Processing...'}
            </div>
          </div>
        )}

        {isFailed && (
          <div className="text-[10px] text-red-600 truncate mt-0.5" title={job?.error || 'Unknown error'}>
            {job?.error || 'Unknown error'}
          </div>
        )}
      </div>

      {(isCompleted || isFailed || isStopped) && onDismiss && (
        <button
          onClick={onDismiss}
          className={`ml-1 shrink-0 rounded-full p-0.5 hover:bg-black/5 transition-colors ${textColor}`}
        >
          <X className="w-3.5 h-3.5" />
        </button>
      )}
    </div>
  );
}
