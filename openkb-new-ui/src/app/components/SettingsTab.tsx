'use client';

import { useCallback, useMemo, useRef, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  ConfigData,
  createKnowledgeBase,
  createModelPoolProfile,
  deleteModelPoolProfile,
  exportConfig,
  getConfig,
  getModelPool,
  getPageIndexLocalStatus,
  importConfig,
  ModelPoolProfile,
  probeAllModelPoolProfiles,
  probeModelPoolProfile,
  selectKbDirectory,
  setModelPoolProfileEnabled,
  testLlmConfig,
  updateConfig,
  updateModelPoolProfile,
} from '@/lib/api';
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { CheckCircle2, Loader2, Plus, RefreshCcw, Save, Server, Settings2, ShieldAlert, Upload } from 'lucide-react';
import { toast } from '@/components/ui/toaster';
import { confirm as confirmDialog } from '@/components/ui/confirm-dialog';

type SettingsSection = 'general' | 'model-pool';

interface ProfileDraft {
  id?: string;
  name: string;
  model: string;
  wire_api: string;
  base_url: string;
  provider: string;
  reasoning_effort: string;
  thinking_enabled: boolean;
  enabled: boolean;
  api_key: string;
  models_text: string;
}

function parseModelRows(text: string): Array<{ name: string; weight: number }> {
  return String(text)
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter(Boolean)
    .map((line) => {
      const [namePart, weightPart] = line.split(/[,\s]+/, 2);
      return {
        name: (namePart || '').trim(),
        weight: Math.max(Number(weightPart || 100), 1),
      };
    })
    .filter((item) => item.name);
}

function profileDraftFrom(profile?: ModelPoolProfile | null): ProfileDraft {
  return {
    id: profile?.id,
    name: profile?.name || '',
    model: profile?.model || '',
    wire_api: profile?.wire_api || 'chat_completions',
    base_url: profile?.base_url || '',
    provider: profile?.provider || 'generic',
    reasoning_effort: profile?.reasoning_effort || '',
    thinking_enabled: Boolean(profile?.thinking_enabled),
    enabled: profile?.enabled !== false,
    api_key: profile?.api_key || '',
    models_text:
      profile?.models?.length
        ? profile.models.map((model) => `${model.name}, ${model.weight}`).join('\n')
        : profile?.model
          ? `${profile.model}, 100`
          : '',
  };
}

function modelHealthTone(health: string) {
  if (health === 'healthy') return 'bg-emerald-50 text-emerald-700 border-emerald-200';
  if (health === 'degraded') return 'bg-amber-50 text-amber-700 border-amber-200';
  if (health === 'offline') return 'bg-red-50 text-red-700 border-red-200';
  if (health === 'disabled') return 'bg-stone-100 text-stone-700 border-stone-200';
  return 'bg-slate-100 text-slate-700 border-slate-200';
}

export function SettingsTab({
  kbDir,
  onKbChanged,
}: {
  kbDir: string | null;
  onKbChanged: (kbDir: string) => void;
}) {
  const queryClient = useQueryClient();
  const importInputRef = useRef<HTMLInputElement | null>(null);
  const [section, setSection] = useState<SettingsSection>('general');
  const [kbPath, setKbPath] = useState(() => kbDir ?? '');
  const [configDraft, setConfigDraft] = useState<ConfigData | null>(null);
  const [profileDialogOpen, setProfileDialogOpen] = useState(false);
  const [profileDraft, setProfileDraft] = useState<ProfileDraft>(profileDraftFrom());
  const [errorMessage, setErrorMessage] = useState('');
  const [successMessage, setSuccessMessage] = useState('');
  const [modelSearch, setModelSearch] = useState('');
  const [modelHealthFilter, setModelHealthFilter] = useState('all');
  const [localProbingProfileIds, setLocalProbingProfileIds] = useState<string[]>([]);

  const configQuery = useQuery({
    queryKey: ['config', kbDir],
    queryFn: () => getConfig(kbDir!),
    enabled: !!kbDir,
  });

  const pageIndexQuery = useQuery({
    queryKey: ['pageindexLocalStatus', kbDir],
    queryFn: () => getPageIndexLocalStatus(kbDir!),
    enabled: !!kbDir,
  });

  const modelPoolQuery = useQuery({
    queryKey: ['modelPool', kbDir],
    queryFn: () => getModelPool(kbDir!),
    enabled: !!kbDir,
  });

  const effectiveConfig = configDraft ?? configQuery.data ?? null;

  const clearFlash = () => {
    setErrorMessage('');
    setSuccessMessage('');
  };

  const refreshKbQueries = useCallback(async (nextKbDir?: string) => {
    const targetKb = nextKbDir ?? kbDir;
    await queryClient.invalidateQueries({ queryKey: ['kbs'] });
    if (targetKb) {
      await queryClient.invalidateQueries({ queryKey: ['config', targetKb] });
      await queryClient.invalidateQueries({ queryKey: ['modelPool', targetKb] });
      await queryClient.invalidateQueries({ queryKey: ['pageindexLocalStatus', targetKb] });
      await queryClient.invalidateQueries({ queryKey: ['kbStats', targetKb] });
      await queryClient.invalidateQueries({ queryKey: ['documents', targetKb] });
      await queryClient.invalidateQueries({ queryKey: ['chats', targetKb] });
    }
  }, [kbDir, queryClient]);

  const saveSettingsMutation = useMutation({
    mutationFn: (payload: Record<string, unknown>) => updateConfig(kbDir!, payload),
    onSuccess: async (config) => {
      setConfigDraft(config);
      clearFlash();
      setSuccessMessage('Settings saved.');
      await refreshKbQueries();
    },
    onError: (error) => {
      setSuccessMessage('');
      setErrorMessage(error instanceof Error ? error.message : 'Failed to save settings');
    },
  });

  const useKbMutation = useMutation({
    mutationFn: (path: string) => selectKbDirectory(path),
    onSuccess: async ({ kb_dir }) => {
      clearFlash();
      setSuccessMessage('Knowledge base selected.');
      onKbChanged(kb_dir);
      await refreshKbQueries(kb_dir);
    },
    onError: (error) => {
      setSuccessMessage('');
      setErrorMessage(error instanceof Error ? error.message : 'Failed to switch knowledge base');
    },
  });

  const createKbMutation = useMutation({
    mutationFn: (payload: Record<string, unknown>) => createKnowledgeBase(payload),
    onSuccess: async ({ kb_dir }) => {
      clearFlash();
      setSuccessMessage('Knowledge base created.');
      onKbChanged(kb_dir);
      await refreshKbQueries(kb_dir);
    },
    onError: (error) => {
      setSuccessMessage('');
      setErrorMessage(error instanceof Error ? error.message : 'Failed to create knowledge base');
    },
  });

  const testLlmMutation = useMutation({
    mutationFn: (payload: Record<string, unknown>) => testLlmConfig(payload),
    onSuccess: (result) => {
      clearFlash();
      setSuccessMessage(result.message || 'LLM test succeeded.');
    },
    onError: (error) => {
      setSuccessMessage('');
      setErrorMessage(error instanceof Error ? error.message : 'LLM test failed');
    },
  });

  const probeAllMutation = useMutation({
    mutationFn: (source: 'manual' | 'auto' = 'manual') => probeAllModelPoolProfiles(kbDir!, source),
    onMutate: (source) => {
      const currentProfiles = modelPoolQuery.data?.profiles ?? [];
      const ids = currentProfiles.filter((profile) => profile.enabled).map((profile) => profile.id);
      setLocalProbingProfileIds((current) => Array.from(new Set([...current, ...ids])));
      return { source, ids };
    },
    onSuccess: async (_data, source) => {
      if (source !== 'auto') {
        clearFlash();
        setSuccessMessage('Model pool probe completed.');
      }
      await refreshKbQueries();
    },
    onError: (error, source) => {
      if (source !== 'auto') {
        setSuccessMessage('');
        setErrorMessage(error instanceof Error ? error.message : 'Probe failed');
      }
    },
    onSettled: (_data, _error, _source, context) => {
      const ids = context?.ids ?? [];
      if (!ids.length) return;
      setLocalProbingProfileIds((current) => current.filter((id) => !ids.includes(id)));
    },
  });

  const probeProfileMutation = useMutation({
    mutationFn: async (profileId: string) => {
      await probeModelPoolProfile(kbDir!, profileId);
      await refreshKbQueries();
      return profileId;
    },
    onMutate: (profileId) => {
      setLocalProbingProfileIds((current) => Array.from(new Set([...current, profileId])));
      return { profileId };
    },
    onError: (error) => {
      setSuccessMessage('');
      setErrorMessage(error instanceof Error ? error.message : 'Probe failed');
    },
    onSettled: (_data, _error, _profileId, context) => {
      if (!context?.profileId) return;
      setLocalProbingProfileIds((current) => current.filter((id) => id !== context.profileId));
    },
  });

  const saveProfileMutation = useMutation({
    mutationFn: async (draft: ProfileDraft) => {
      const payload = {
        name: draft.name,
        model: draft.model,
        wire_api: draft.wire_api,
        base_url: draft.base_url,
        provider: draft.provider,
        reasoning_effort: draft.reasoning_effort,
        thinking_enabled: draft.thinking_enabled,
        enabled: draft.enabled,
        api_key: draft.api_key,
        models: parseModelRows(draft.models_text),
      };
      return draft.id
        ? updateModelPoolProfile(kbDir!, draft.id, payload)
        : createModelPoolProfile(kbDir!, payload);
    },
    onSuccess: async () => {
      setProfileDialogOpen(false);
      clearFlash();
      setSuccessMessage('Model profile saved.');
      await refreshKbQueries();
    },
    onError: (error) => {
      setSuccessMessage('');
      setErrorMessage(error instanceof Error ? error.message : 'Failed to save model profile');
    },
  });

  const filteredProfiles = useMemo(() => {
    const modelPool = modelPoolQuery.data;
    const query = modelSearch.trim().toLowerCase();
    if (!modelPool) return [];
    return modelPool.profiles.filter((profile) => {
      const haystack = [
        profile.name,
        profile.id,
        profile.model,
        profile.base_url,
        ...profile.tags,
        ...profile.features,
        ...profile.probe_models,
      ]
        .join(' ')
        .toLowerCase();
      const matchesQuery = !query || haystack.includes(query);
      const matchesHealth = modelHealthFilter === 'all' || profile.health === modelHealthFilter;
      return matchesQuery && matchesHealth;
    });
  }, [modelHealthFilter, modelPoolQuery.data, modelSearch]);

  const busy =
    saveSettingsMutation.isPending ||
    useKbMutation.isPending ||
    createKbMutation.isPending ||
    testLlmMutation.isPending ||
    probeAllMutation.isPending ||
    probeProfileMutation.isPending ||
    saveProfileMutation.isPending;

  const updateDraft = <K extends keyof ConfigData>(key: K, value: ConfigData[K]) => {
    setConfigDraft((current) => ({ ...(current ?? effectiveConfig ?? {} as ConfigData), [key]: value }));
  };

  const handleExport = async () => {
    if (!kbDir) return;
    try {
      clearFlash();
      const payload = await exportConfig(kbDir);
      const blob = new Blob([JSON.stringify(payload, null, 2)], { type: 'application/json' });
      const url = URL.createObjectURL(blob);
      const link = document.createElement('a');
      link.href = url;
      link.download = 'openkb-settings-config.json';
      link.click();
      URL.revokeObjectURL(url);
      setSuccessMessage('Settings exported.');
    } catch (error) {
      setSuccessMessage('');
      setErrorMessage(error instanceof Error ? error.message : 'Export failed');
    }
  };

  const handleImport = async (event: React.ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0];
    if (!file || !kbDir) return;
    try {
      clearFlash();
      const parsed = JSON.parse(await file.text()) as Record<string, unknown>;
      const config = await importConfig(kbDir, parsed);
      setConfigDraft(config);
      await refreshKbQueries();
      setSuccessMessage('Settings imported.');
    } catch (error) {
      setSuccessMessage('');
      setErrorMessage(error instanceof Error ? error.message : 'Import failed');
    } finally {
      event.target.value = '';
    }
  };

  const handleSaveSettings = () => {
    if (!effectiveConfig || !kbDir) return;
    saveSettingsMutation.mutate({
      language: effectiveConfig.language,
      pageindex_threshold: effectiveConfig.pageindex_threshold,
      compile_max_concurrency: effectiveConfig.compile_max_concurrency,
      ingest_gate_enabled: effectiveConfig.ingest_gate_enabled,
      ingest_gate_pass_threshold: effectiveConfig.ingest_gate_pass_threshold,
      ingest_gate_hold_threshold: effectiveConfig.ingest_gate_hold_threshold,
      ingest_gate_hard_reject_enabled: effectiveConfig.ingest_gate_hard_reject_enabled,
      ingest_gate_log_all_decisions: effectiveConfig.ingest_gate_log_all_decisions,
      ingest_gate_allow_force_pass: effectiveConfig.ingest_gate_allow_force_pass,
      ingest_gate_allow_force_reject: effectiveConfig.ingest_gate_allow_force_reject,
      ocr_enabled: effectiveConfig.ocr_enabled,
      ocr_detection_mode: effectiveConfig.ocr_detection_mode,
      ocr_default_model: effectiveConfig.ocr_default_model,
      ocr_chunk_pages: effectiveConfig.ocr_chunk_pages,
      ocr_auto_recommend: effectiveConfig.ocr_auto_recommend,
      paddleocr_token: effectiveConfig.paddleocr_token,
      pageindex_local_enabled: effectiveConfig.pageindex_local_enabled,
      pageindex_local_model: effectiveConfig.pageindex_local_model,
      pageindex_local_installation_state: effectiveConfig.pageindex_local_installation_state,
      pageindex_local_repo_dir: effectiveConfig.pageindex_local_repo_dir,
      pageindex_local_python_path: effectiveConfig.pageindex_local_python_path,
      pageindex_local_script_path: effectiveConfig.pageindex_local_script_path,
    });
  };

  const handleTestLlm = () => {
    if (!effectiveConfig || !kbDir) return;
    testLlmMutation.mutate({
      kb_dir: kbDir,
      model: effectiveConfig.model,
      wire_api: effectiveConfig.wire_api,
      base_url: effectiveConfig.base_url,
      api_key: effectiveConfig.api_key,
      language: effectiveConfig.language,
      pageindex_threshold: effectiveConfig.pageindex_threshold,
      compile_max_concurrency: effectiveConfig.compile_max_concurrency,
      ocr_enabled: effectiveConfig.ocr_enabled,
    });
  };

  const modelPool = modelPoolQuery.data;

  return (
    <Card className="h-full flex flex-col rounded-none border-t-0 border-b-0 border-x-0 sm:border-x sm:rounded-lg overflow-hidden min-h-0">
      <CardHeader className="border-b">
        <div className="flex items-center justify-between gap-4">
          <div>
            <CardTitle className="text-xl">Settings</CardTitle>
            <p className="mt-1 text-sm text-muted-foreground">
              Manage knowledge base selection, runtime config, and model pool endpoints.
            </p>
          </div>
          <Button variant="ghost" onClick={() => refreshKbQueries()} disabled={busy}>
            <RefreshCcw className="mr-2 h-4 w-4" />
            Refresh
          </Button>
        </div>
      </CardHeader>

      {(errorMessage || successMessage) ? (
        <div className="border-b px-6 py-4">
          {errorMessage ? (
            <Alert variant="destructive">
              <ShieldAlert />
              <AlertTitle>Settings action failed</AlertTitle>
              <AlertDescription>{errorMessage}</AlertDescription>
            </Alert>
          ) : (
            <Alert>
              <CheckCircle2 />
              <AlertTitle>Updated</AlertTitle>
              <AlertDescription>{successMessage}</AlertDescription>
            </Alert>
          )}
        </div>
      ) : null}

      <CardContent className="min-h-0 flex-1 p-0">
        <Tabs value={section} onValueChange={(value) => setSection(value as SettingsSection)} className="flex h-full flex-col">
          <div className="border-b px-6 pt-4">
            <TabsList className="bg-muted/60">
              <TabsTrigger value="general">General</TabsTrigger>
              <TabsTrigger value="model-pool">Model Pool</TabsTrigger>
            </TabsList>
          </div>

          <TabsContent value="general" className="m-0 min-h-0 flex-1 overflow-auto p-6">
            <div className="grid gap-6 xl:grid-cols-[minmax(0,1.3fr)_minmax(320px,0.7fr)]">
              <section className="space-y-6">
                <div className="rounded-2xl border bg-background p-5">
                  <div className="mb-4 flex items-center justify-between">
                    <div>
                      <h3 className="font-semibold">Knowledge Base</h3>
                      <p className="text-sm text-muted-foreground">Use an existing KB or create a new one.</p>
                    </div>
                    <div className="flex gap-2">
                      <Button variant="outline" onClick={handleExport} disabled={!kbDir}>
                        Export
                      </Button>
                      <Button variant="outline" onClick={() => importInputRef.current?.click()} disabled={!kbDir}>
                        <Upload className="mr-2 h-4 w-4" />
                        Import
                      </Button>
                      <input ref={importInputRef} type="file" accept="application/json,.json" className="hidden" onChange={handleImport} />
                    </div>
                  </div>

                  <div className="grid gap-4 md:grid-cols-2">
                    <Field label="Path" fullWidth>
                      <Input value={kbPath} onChange={(event) => setKbPath(event.target.value)} placeholder="/path/to/kb" />
                    </Field>
                    <Field label="Language">
                      <Input
                        value={configDraft?.language ?? 'en'}
                        onChange={(event) => updateDraft('language', event.target.value)}
                        disabled={!configDraft}
                      />
                    </Field>
                    <Field label="PageIndex Threshold">
                      <Input
                        type="number"
                        min={1}
                        value={configDraft?.pageindex_threshold ?? 20}
                        onChange={(event) => updateDraft('pageindex_threshold', Number(event.target.value || 20))}
                        disabled={!configDraft}
                      />
                    </Field>
                    <Field label="Compile Concurrency">
                      <Input
                        type="number"
                        min={1}
                        value={configDraft?.compile_max_concurrency ?? 2}
                        onChange={(event) => updateDraft('compile_max_concurrency', Number(event.target.value || 2))}
                        disabled={!configDraft}
                      />
                    </Field>
                  </div>

                  <div className="mt-4 flex flex-wrap gap-2">
                    <Button onClick={() => useKbMutation.mutate(kbPath)} disabled={!kbPath.trim() || busy}>
                      {useKbMutation.isPending ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : null}
                      Use KB
                    </Button>
                    <Button
                      variant="outline"
                      onClick={() =>
                        createKbMutation.mutate({
                          path: kbPath,
                          model: configDraft?.model || 'gpt-5.4-mini',
                          language: configDraft?.language || 'en',
                          pageindex_threshold: configDraft?.pageindex_threshold || 20,
                          compile_max_concurrency: configDraft?.compile_max_concurrency || 2,
                          ingest_gate_enabled: configDraft?.ingest_gate_enabled ?? false,
                          ingest_gate_pass_threshold: configDraft?.ingest_gate_pass_threshold ?? 75,
                          ingest_gate_hold_threshold: configDraft?.ingest_gate_hold_threshold ?? 60,
                          ingest_gate_hard_reject_enabled: configDraft?.ingest_gate_hard_reject_enabled ?? true,
                          ingest_gate_log_all_decisions: configDraft?.ingest_gate_log_all_decisions ?? true,
                          ingest_gate_allow_force_pass: configDraft?.ingest_gate_allow_force_pass ?? true,
                          ingest_gate_allow_force_reject: configDraft?.ingest_gate_allow_force_reject ?? true,
                          wire_api: configDraft?.wire_api || 'chat_completions',
                          base_url: configDraft?.base_url || '',
                          api_key: configDraft?.api_key || '',
                        })
                      }
                      disabled={!kbPath.trim() || busy}
                    >
                      {createKbMutation.isPending ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <Plus className="mr-2 h-4 w-4" />}
                      Create KB
                    </Button>
                  </div>
                </div>

                <div className="rounded-2xl border bg-background p-5">
                  <div className="mb-4 flex items-center justify-between">
                    <div>
                      <h3 className="font-semibold">Runtime Config</h3>
                      <p className="text-sm text-muted-foreground">OCR, ingest gate, and local PageIndex settings.</p>
                    </div>
                    <div className="flex gap-2">
                      <Button variant="outline" onClick={handleTestLlm} disabled={!configDraft || !kbDir || busy}>
                        <Server className="mr-2 h-4 w-4" />
                        Test LLM
                      </Button>
                      <Button onClick={handleSaveSettings} disabled={!configDraft || !kbDir || busy}>
                        {saveSettingsMutation.isPending ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <Save className="mr-2 h-4 w-4" />}
                        Save Settings
                      </Button>
                    </div>
                  </div>

                  <div className="grid gap-4 md:grid-cols-2">
                    <CheckField
                      label="Ingest Gate"
                      checked={configDraft?.ingest_gate_enabled ?? false}
                      onChange={(checked) => updateDraft('ingest_gate_enabled', checked)}
                      disabled={!configDraft}
                    />
                    <CheckField
                      label="OCR Enabled"
                      checked={configDraft?.ocr_enabled ?? true}
                      onChange={(checked) => updateDraft('ocr_enabled', checked)}
                      disabled={!configDraft}
                    />
                    <Field label="Gate Pass Threshold">
                      <Input
                        type="number"
                        min={0}
                        max={100}
                        value={configDraft?.ingest_gate_pass_threshold ?? 75}
                        onChange={(event) => updateDraft('ingest_gate_pass_threshold', Number(event.target.value || 75))}
                        disabled={!configDraft}
                      />
                    </Field>
                    <Field label="Gate Hold Threshold">
                      <Input
                        type="number"
                        min={0}
                        max={100}
                        value={configDraft?.ingest_gate_hold_threshold ?? 60}
                        onChange={(event) => updateDraft('ingest_gate_hold_threshold', Number(event.target.value || 60))}
                        disabled={!configDraft}
                      />
                    </Field>
                    <Field label="OCR Detection">
                      <select
                        value={configDraft?.ocr_detection_mode ?? 'auto_recommend'}
                        onChange={(event) => updateDraft('ocr_detection_mode', event.target.value)}
                        disabled={!configDraft}
                        className="h-9 w-full rounded-md border border-border bg-background px-3 text-sm"
                      >
                        <option value="auto_recommend">auto_recommend</option>
                        <option value="always_ask">always_ask</option>
                        <option value="disabled">disabled</option>
                      </select>
                    </Field>
                    <Field label="OCR Default Model">
                      <select
                        value={configDraft?.ocr_default_model ?? 'PaddleOCR-VL-1.5'}
                        onChange={(event) => updateDraft('ocr_default_model', event.target.value)}
                        disabled={!configDraft}
                        className="h-9 w-full rounded-md border border-border bg-background px-3 text-sm"
                      >
                        <option value="PaddleOCR-VL-1.5">PaddleOCR-VL-1.5</option>
                        <option value="PP-StructureV3">PP-StructureV3</option>
                      </select>
                    </Field>
                    <Field label="OCR Chunk Pages">
                      <Input
                        type="number"
                        min={1}
                        max={100}
                        value={configDraft?.ocr_chunk_pages ?? 100}
                        onChange={(event) => updateDraft('ocr_chunk_pages', Number(event.target.value || 100))}
                        disabled={!configDraft}
                      />
                    </Field>
                    <CheckField
                      label="OCR Auto Recommend"
                      checked={configDraft?.ocr_auto_recommend ?? true}
                      onChange={(checked) => updateDraft('ocr_auto_recommend', checked)}
                      disabled={!configDraft}
                    />
                    <CheckField
                      label="Hard Reject"
                      checked={configDraft?.ingest_gate_hard_reject_enabled ?? true}
                      onChange={(checked) => updateDraft('ingest_gate_hard_reject_enabled', checked)}
                      disabled={!configDraft}
                    />
                    <CheckField
                      label="Gate Audit Log"
                      checked={configDraft?.ingest_gate_log_all_decisions ?? true}
                      onChange={(checked) => updateDraft('ingest_gate_log_all_decisions', checked)}
                      disabled={!configDraft}
                    />
                    <CheckField
                      label="Allow Force Pass"
                      checked={configDraft?.ingest_gate_allow_force_pass ?? true}
                      onChange={(checked) => updateDraft('ingest_gate_allow_force_pass', checked)}
                      disabled={!configDraft}
                    />
                    <CheckField
                      label="Allow Force Reject"
                      checked={configDraft?.ingest_gate_allow_force_reject ?? true}
                      onChange={(checked) => updateDraft('ingest_gate_allow_force_reject', checked)}
                      disabled={!configDraft}
                    />
                  </div>
                </div>
              </section>

              <section className="space-y-6">
                <div className="rounded-2xl border bg-background p-5">
                  <div className="mb-4 flex items-center gap-2">
                    <Settings2 className="h-4 w-4" />
                    <h3 className="font-semibold">PageIndex Local Runtime</h3>
                  </div>
                  <div className="grid gap-4">
                    <CheckField
                      label="Local PageIndex Enabled"
                      checked={configDraft?.pageindex_local_enabled ?? false}
                      onChange={(checked) => updateDraft('pageindex_local_enabled', checked)}
                      disabled={!configDraft}
                    />
                    <Field label="Local Model">
                      <Input
                        value={configDraft?.pageindex_local_model ?? ''}
                        onChange={(event) => updateDraft('pageindex_local_model', event.target.value)}
                        disabled={!configDraft}
                      />
                    </Field>
                    <Field label="Installation State">
                      <select
                        value={configDraft?.pageindex_local_installation_state ?? 'not_installed'}
                        onChange={(event) => updateDraft('pageindex_local_installation_state', event.target.value)}
                        disabled={!configDraft}
                        className="h-9 w-full rounded-md border border-border bg-background px-3 text-sm"
                      >
                        <option value="not_installed">not_installed</option>
                        <option value="installing">installing</option>
                        <option value="installed">installed</option>
                        <option value="failed">failed</option>
                      </select>
                    </Field>
                    <Field label="Repo Dir">
                      <Input
                        value={configDraft?.pageindex_local_repo_dir ?? ''}
                        onChange={(event) => updateDraft('pageindex_local_repo_dir', event.target.value)}
                        disabled={!configDraft}
                      />
                    </Field>
                    <Field label="Python Path">
                      <Input
                        value={configDraft?.pageindex_local_python_path ?? ''}
                        onChange={(event) => updateDraft('pageindex_local_python_path', event.target.value)}
                        disabled={!configDraft}
                      />
                    </Field>
                    <Field label="Script Path">
                      <Input
                        value={configDraft?.pageindex_local_script_path ?? ''}
                        onChange={(event) => updateDraft('pageindex_local_script_path', event.target.value)}
                        disabled={!configDraft}
                      />
                    </Field>
                    <Field label="PaddleOCR Token">
                      <Input
                        type="password"
                        value={configDraft?.paddleocr_token ?? ''}
                        onChange={(event) => updateDraft('paddleocr_token', event.target.value)}
                        disabled={!configDraft}
                      />
                    </Field>
                  </div>
                </div>

                <div className="rounded-2xl border bg-background p-5">
                  <div className="mb-4 flex items-center justify-between">
                    <h3 className="font-semibold">Runtime Status</h3>
                    {pageIndexQuery.isFetching ? <Loader2 className="h-4 w-4 animate-spin text-muted-foreground" /> : null}
                  </div>
                  <div className="space-y-3 text-sm">
                    <StatusRow label="Ready" value={pageIndexQuery.data?.ready ? 'yes' : 'no'} />
                    <StatusRow label="Root" value={pageIndexQuery.data?.root || 'n/a'} />
                    <StatusRow
                      label="Version"
                      value={String(pageIndexQuery.data?.manifest?.version || configDraft?.pageindex_local_version || 'n/a')}
                    />
                    <StatusRow label="Install State" value={pageIndexQuery.data?.installation_state || 'n/a'} />
                  </div>
                </div>
              </section>
            </div>
          </TabsContent>

          <TabsContent value="model-pool" className="m-0 min-h-0 flex-1 overflow-auto p-6">
            <div className="space-y-6">
              <div className="rounded-2xl border bg-background p-5">
                <div className="flex flex-col gap-4 xl:flex-row xl:items-center xl:justify-between">
                  <div>
                    <h3 className="font-semibold">Model Pool</h3>
                    <p className="text-sm text-muted-foreground">
                      Weighted endpoint routing, health checks, and profile overrides.
                    </p>
                  </div>
                  <div className="flex flex-wrap gap-2">
                    <Button
                      variant="outline"
                      onClick={() =>
                        saveSettingsMutation.mutate({
                          model_pool_enabled: !modelPool?.enabled,
                        })
                      }
                      disabled={!kbDir || busy}
                    >
                      {modelPool?.enabled ? 'Disable Pool' : 'Enable Pool'}
                    </Button>
                      <Button variant="outline" onClick={() => probeAllMutation.mutate('manual')} disabled={!kbDir || busy}>
                      {probeAllMutation.isPending ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <RefreshCcw className="mr-2 h-4 w-4" />}
                      Probe All
                    </Button>
                    <Button
                      onClick={() => {
                        setProfileDraft(profileDraftFrom());
                        setProfileDialogOpen(true);
                      }}
                      disabled={!kbDir || busy}
                    >
                      <Plus className="mr-2 h-4 w-4" />
                      Add Endpoint
                    </Button>
                  </div>
                </div>

                <div className="mt-4 grid gap-4 lg:grid-cols-[minmax(0,1fr)_240px_1fr]">
                  <Input
                    placeholder="Search name, model, or base URL"
                    value={modelSearch}
                    onChange={(event) => setModelSearch(event.target.value)}
                  />
                  <select
                    value={modelHealthFilter}
                    onChange={(event) => setModelHealthFilter(event.target.value)}
                    className="h-9 rounded-md border border-border bg-background px-3 text-sm"
                  >
                    <option value="all">All Health</option>
                    <option value="healthy">Healthy</option>
                    <option value="degraded">Degraded</option>
                    <option value="offline">Offline</option>
                    <option value="disabled">Disabled</option>
                    <option value="unknown">Unknown</option>
                  </select>
                  <div className="grid grid-cols-3 gap-2 text-sm">
                    <SummaryStat label="Healthy" value={modelPool?.summary?.healthy ?? 0} />
                    <SummaryStat label="Offline" value={modelPool?.summary?.offline ?? 0} />
                    <SummaryStat label="Disabled" value={modelPool?.summary?.disabled ?? 0} />
                  </div>
                </div>
              </div>

              <div className="grid gap-4 xl:grid-cols-2">
                {modelPoolQuery.isLoading ? (
                  <div className="col-span-full flex justify-center py-12">
                    <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
                  </div>
                ) : filteredProfiles.length ? (
                  filteredProfiles.map((profile) => (
                    <div key={profile.id} className="rounded-2xl border bg-background p-5 shadow-sm">
                      {(() => {
                        const isProbing = profile.probing || localProbingProfileIds.includes(profile.id);
                        return (
                          <>
                      <div className="flex items-start justify-between gap-4">
                        <div>
                          <div className="flex items-center gap-2">
                            <h4 className="font-semibold">{profile.name}</h4>
                            {profile.is_active ? (
                              <span className="rounded-full bg-primary/10 px-2 py-0.5 text-xs text-primary">active</span>
                            ) : null}
                            {isProbing ? (
                              <span className="inline-flex items-center gap-1 rounded-full bg-sky-50 px-2 py-0.5 text-xs text-sky-700">
                                <Loader2 className="h-3 w-3 animate-spin" />
                                probing
                              </span>
                            ) : null}
                          </div>
                          <p className="mt-1 text-sm text-muted-foreground">{profile.base_url || '(default provider)'}</p>
                        </div>
                        <span className={`rounded-full border px-2 py-1 text-xs ${modelHealthTone(profile.health)}`}>
                          {profile.health}
                        </span>
                      </div>

                      <div className="mt-4 space-y-2 text-sm">
                        <div>Model: {profile.model}</div>
                        <div>Wire API: {profile.wire_api}</div>
                        <div>Provider: {profile.provider}</div>
                        <div>Last checked: {profile.last_checked_at || 'never'}</div>
                        <div>Last probe started: {profile.last_probe_started_at || (isProbing ? 'just now' : 'never')}</div>
                        <div>Probe source: {profile.probe_source || 'n/a'}</div>
                        {profile.last_error ? <div className="text-red-700">Last error: {profile.last_error}</div> : null}
                      </div>

                      <div className="mt-4 rounded-xl bg-muted/40 p-3 text-sm">
                        <div className="mb-2 font-medium">Routes</div>
                        <div className="space-y-2">
                          {profile.routes.length ? (
                            profile.routes.map((route) => (
                              <div key={route.id} className="flex items-center justify-between gap-3 rounded-lg bg-background px-3 py-2">
                                <span>{route.model}</span>
                                <span className="text-xs text-muted-foreground">
                                  w{route.weight} · {route.health}
                                  {route.latency_ms !== null ? ` · ${route.latency_ms}ms` : ''}
                                </span>
                              </div>
                            ))
                          ) : (
                            <div className="text-muted-foreground">No routes configured.</div>
                          )}
                        </div>
                      </div>

                      <div className="mt-4 flex flex-wrap gap-2">
                        <Button
                          variant="outline"
                          onClick={() => probeProfileMutation.mutate(profile.id)}
                          disabled={!kbDir || busy || isProbing}
                        >
                          Probe
                        </Button>
                        <Button
                          variant="outline"
                          onClick={() => {
                            setProfileDraft(profileDraftFrom(profile));
                            setProfileDialogOpen(true);
                          }}
                          disabled={!kbDir || busy}
                        >
                          Edit
                        </Button>
                        <Button
                          variant="outline"
                          onClick={async () => {
                            await setModelPoolProfileEnabled(kbDir!, profile.id, !profile.enabled);
                            await refreshKbQueries();
                          }}
                          disabled={!kbDir || busy}
                        >
                          {profile.enabled ? 'Disable' : 'Enable'}
                        </Button>
                        <Button
                          variant="destructive"
                          onClick={async () => {
                            const ok = await confirmDialog({
                              title: 'Delete model profile?',
                              description: `Profile "${profile.name}" will be removed from the pool.`,
                              confirmLabel: 'Delete',
                              variant: 'danger',
                            });
                            if (!ok) return;
                            try {
                              await deleteModelPoolProfile(kbDir!, profile.id);
                              await refreshKbQueries();
                              toast.success('Model profile deleted');
                            } catch (error) {
                              toast.error(
                                'Failed to delete profile',
                                error instanceof Error ? error.message : undefined,
                              );
                            }
                          }}
                          disabled={!kbDir || busy}
                        >
                          Delete
                        </Button>
                      </div>
                          </>
                        );
                      })()}
                    </div>
                  ))
                ) : (
                  <div className="col-span-full rounded-2xl border border-dashed bg-background px-6 py-12 text-center text-muted-foreground">
                    No matching model profiles.
                  </div>
                )}
              </div>
            </div>
          </TabsContent>
        </Tabs>
      </CardContent>

      <Dialog open={profileDialogOpen} onOpenChange={setProfileDialogOpen}>
        <DialogContent className="max-w-2xl">
          <DialogHeader>
            <DialogTitle>{profileDraft.id ? 'Edit Model Endpoint' : 'Add Model Endpoint'}</DialogTitle>
            <DialogDescription>One endpoint can expose multiple weighted models.</DialogDescription>
          </DialogHeader>
          <div className="grid gap-4 md:grid-cols-2">
            <Field label="Name">
              <Input value={profileDraft.name} onChange={(event) => setProfileDraft((current) => ({ ...current, name: event.target.value }))} />
            </Field>
            <Field label="Provider">
              <select
                value={profileDraft.provider}
                onChange={(event) => setProfileDraft((current) => ({ ...current, provider: event.target.value }))}
                className="h-9 w-full rounded-md border border-border bg-background px-3 text-sm"
              >
                <option value="generic">generic</option>
                <option value="deepseek">deepseek</option>
              </select>
            </Field>
            <Field label="Wire API">
              <select
                value={profileDraft.wire_api}
                onChange={(event) => setProfileDraft((current) => ({ ...current, wire_api: event.target.value }))}
                className="h-9 w-full rounded-md border border-border bg-background px-3 text-sm"
              >
                <option value="responses">responses</option>
                <option value="chat_completions">chat_completions</option>
              </select>
            </Field>
            <Field label="Reasoning Effort">
              <select
                value={profileDraft.reasoning_effort}
                onChange={(event) => setProfileDraft((current) => ({ ...current, reasoning_effort: event.target.value }))}
                className="h-9 w-full rounded-md border border-border bg-background px-3 text-sm"
              >
                <option value="">default</option>
                <option value="low">low</option>
                <option value="medium">medium</option>
                <option value="high">high</option>
              </select>
            </Field>
            <Field label="Base URL" fullWidth>
              <Input value={profileDraft.base_url} onChange={(event) => setProfileDraft((current) => ({ ...current, base_url: event.target.value }))} />
            </Field>
            <Field label="Primary Model">
              <Input value={profileDraft.model} onChange={(event) => setProfileDraft((current) => ({ ...current, model: event.target.value }))} />
            </Field>
            <Field label="API Key">
              <Input
                type="password"
                value={profileDraft.api_key}
                onChange={(event) => setProfileDraft((current) => ({ ...current, api_key: event.target.value }))}
              />
            </Field>
            <Field label="Models" fullWidth>
              <textarea
                value={profileDraft.models_text}
                onChange={(event) => setProfileDraft((current) => ({ ...current, models_text: event.target.value }))}
                className="min-h-[140px] w-full rounded-md border border-border bg-background px-3 py-2 text-sm outline-none ring-offset-background placeholder:text-muted-foreground focus-visible:ring-2 focus-visible:ring-ring"
                placeholder={'gpt-5.4-mini, 100\ngpt-4o-mini, 50'}
              />
            </Field>
            <CheckField
              label="Thinking Enabled"
              checked={profileDraft.thinking_enabled}
              onChange={(checked) => setProfileDraft((current) => ({ ...current, thinking_enabled: checked }))}
            />
            <CheckField
              label="Profile Enabled"
              checked={profileDraft.enabled}
              onChange={(checked) => setProfileDraft((current) => ({ ...current, enabled: checked }))}
            />
          </div>
          <DialogFooter showCloseButton>
            <Button onClick={() => saveProfileMutation.mutate(profileDraft)} disabled={!profileDraft.name.trim() || !profileDraft.model.trim() || saveProfileMutation.isPending}>
              {saveProfileMutation.isPending ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <Save className="mr-2 h-4 w-4" />}
              Save Profile
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </Card>
  );
}

function Field({
  label,
  children,
  fullWidth = false,
}: {
  label: string;
  children: React.ReactNode;
  fullWidth?: boolean;
}) {
  return (
    <label className={`grid gap-2 text-sm ${fullWidth ? 'md:col-span-2' : ''}`}>
      <span className="font-medium">{label}</span>
      {children}
    </label>
  );
}

function CheckField({
  label,
  checked,
  onChange,
  disabled = false,
}: {
  label: string;
  checked: boolean;
  onChange: (checked: boolean) => void;
  disabled?: boolean;
}) {
  return (
    <label className="flex items-center justify-between rounded-xl border bg-muted/30 px-4 py-3 text-sm">
      <span className="font-medium">{label}</span>
      <input type="checkbox" checked={checked} onChange={(event) => onChange(event.target.checked)} disabled={disabled} />
    </label>
  );
}

function StatusRow({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-start justify-between gap-4 rounded-xl bg-muted/30 px-4 py-3">
      <span className="text-muted-foreground">{label}</span>
      <span className="max-w-[60%] break-all text-right font-medium">{value}</span>
    </div>
  );
}

function SummaryStat({ label, value }: { label: string; value: number }) {
  return (
    <div className="rounded-xl bg-muted/40 px-3 py-2 text-center">
      <div className="text-xs uppercase tracking-[0.12em] text-muted-foreground">{label}</div>
      <div className="mt-1 text-lg font-semibold">{value}</div>
    </div>
  );
}
