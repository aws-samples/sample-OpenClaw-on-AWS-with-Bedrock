/**
 * React Query hooks for all API endpoints.
 * Each hook wraps a useQuery/useMutation call to the FastAPI backend.
 * When the backend is running, data comes from DynamoDB.
 * When it's not, the API calls fail and we fall back gracefully.
 */
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { api } from '../api/client';
import type { Department, Position, Employee, Agent, Binding, LiveSession, AuditEntry, SoulLayer } from '../types';

// === Organization ===

export function useDepartments() {
  return useQuery<Department[]>({
    queryKey: ['departments'],
    queryFn: () => api.get('/org/departments'),
  });
}

export function usePositions() {
  return useQuery<Position[]>({
    queryKey: ['positions'],
    queryFn: () => api.get('/org/positions'),
  });
}

export function useCreateDepartment() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (data: Partial<Department>) => api.post<Department>('/org/departments', data),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['departments'] }); qc.invalidateQueries({ queryKey: ['dashboard'] }); },
  });
}

export function useUpdateDepartment() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, ...data }: { id: string } & Partial<Department>) => api.put<Department>(`/org/departments/${id}`, data),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['departments'] }),
  });
}

export function useDeleteDepartment() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (deptId: string) => api.del<{ ok: boolean }>(`/org/departments/${deptId}`),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['departments'] }); qc.invalidateQueries({ queryKey: ['dashboard'] }); },
  });
}

export function useCreatePosition() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (data: Partial<Position>) => api.post<Position>('/org/positions', data),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['positions'] }); qc.invalidateQueries({ queryKey: ['dashboard'] }); },
  });
}

export function useUpdatePosition() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, ...data }: { id: string } & Partial<Position>) => api.put<Position>(`/org/positions/${id}`, data),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['positions'] }),
  });
}

export function useDeletePosition() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (posId: string) => api.del<{ ok: boolean }>(`/org/positions/${posId}`),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['positions'] }); qc.invalidateQueries({ queryKey: ['dashboard'] }); },
  });
}

export function useEmployees() {
  return useQuery<Employee[]>({
    queryKey: ['employees'],
    queryFn: () => api.get('/org/employees'),
  });
}

export function useEmployeeActivities() {
  return useQuery<Record<string, any>[]>({
    queryKey: ['employee-activities'],
    queryFn: () => api.get('/org/employees/activity'),
  });
}

export function useUsageTrend() {
  return useQuery<{ date: string; openclawCost: number; chatgptEquivalent: number; totalRequests: number }[]>({
    queryKey: ['usage-trend'],
    queryFn: () => api.get('/usage/trend'),
  });
}

export function useCreateEmployee() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (data: Partial<Employee>) => api.post<Employee>('/org/employees', data),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['employees'] }); qc.invalidateQueries({ queryKey: ['dashboard'] }); },
  });
}

export function useUpdateEmployee() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, ...data }: { id: string } & Partial<Employee>) => api.put<Employee>(`/org/employees/${id}`, data),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['employees'] }),
  });
}

export function useDeleteEmployee() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ empId, force = false }: { empId: string; force?: boolean }) =>
      api.del<{ ok: boolean; agentBindings: number; imMappings: number }>(`/org/employees/${empId}?force=${force}`),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['employees'] });
      qc.invalidateQueries({ queryKey: ['bindings'] });
      qc.invalidateQueries({ queryKey: ['user-mappings'] });
      qc.invalidateQueries({ queryKey: ['dashboard'] });
    },
  });
}

// === Agents ===

export function useAgents() {
  return useQuery<Agent[]>({
    queryKey: ['agents'],
    queryFn: () => api.get('/agents'),
  });
}

export function useAgent(agentId: string) {
  return useQuery<Agent>({
    queryKey: ['agent', agentId],
    queryFn: () => api.get(`/agents/${agentId}`),
    enabled: !!agentId,
  });
}

export function useAgentSoul(agentId: string) {
  return useQuery<SoulLayer[]>({
    queryKey: ['agent-soul', agentId],
    queryFn: () => api.get(`/agents/${agentId}/soul`),
    enabled: !!agentId,
  });
}

export function useCreateAgent() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (data: Partial<Agent>) => api.post<Agent>('/agents', data),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['agents'] }),
  });
}

// === Bindings ===

export function useBindings() {
  return useQuery<Binding[]>({
    queryKey: ['bindings'],
    queryFn: () => api.get('/bindings'),
  });
}

export function useCreateBinding() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (data: Partial<Binding>) => api.post<Binding>('/bindings', data),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['bindings'] }),
  });
}

export function useBulkProvision() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (data: { positionId: string; defaultChannel: string }) =>
      api.post<{ position: string; provisioned: number; details: { employee: string; agent: string; channel: string }[]; alreadyBound: number }>('/bindings/provision-by-position', data),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['bindings'] });
      qc.invalidateQueries({ queryKey: ['agents'] });
      qc.invalidateQueries({ queryKey: ['employees'] });
      qc.invalidateQueries({ queryKey: ['dashboard'] });
    },
  });
}

// === Monitor ===

export function useSessions() {
  return useQuery<LiveSession[]>({
    queryKey: ['sessions'],
    queryFn: () => api.get('/monitor/sessions'),
    refetchInterval: 10_000,
  });
}

export function useSessionDetail(sessionId: string) {
  return useQuery<{
    session: LiveSession;
    conversation: { role: string; content: string; ts: string; toolCall?: { tool: string; status: string; duration: string } }[];
    quality: Record<string, number>;
    planE: { turn: number; result: string; detail: string }[];
  }>({
    queryKey: ['session-detail', sessionId],
    queryFn: () => api.get(`/monitor/sessions/${sessionId}`),
    enabled: !!sessionId,
  });
}

export interface AlertRule {
  id: string; type: string; condition: string; action: string;
  status: 'ok' | 'warning' | 'info'; lastChecked: string; detail: string;
}

export function useAlertRules() {
  return useQuery<AlertRule[]>({
    queryKey: ['alert-rules'],
    queryFn: () => api.get('/monitor/alerts'),
    refetchInterval: 30_000,
  });
}

export interface RuntimeEvent {
  type: string; message: string; timestamp: string; tenant?: string; raw?: string;
}

export function useRuntimeEvents(minutes: number = 30) {
  return useQuery<{ events: RuntimeEvent[]; summary: Record<string, number> }>({
    queryKey: ['runtime-events', minutes],
    queryFn: () => api.get(`/monitor/events?minutes=${minutes}`),
    refetchInterval: 15_000,
  });
}

export function useMonitorActionItems() {
  return useQuery<{ items: any[] }>({
    queryKey: ['monitor-action-items'],
    queryFn: () => api.get('/monitor/action-items'),
    refetchInterval: 30_000,
  });
}

export function useMonitorSystemStatus() {
  return useQuery<{ agents: any; sessions: any; system: any }>({
    queryKey: ['monitor-system-status'],
    queryFn: () => api.get('/monitor/system-status'),
    refetchInterval: 30_000,
  });
}

export function useMonitorAgentActivity() {
  return useQuery<{ agents: any[] }>({
    queryKey: ['monitor-agent-activity'],
    queryFn: () => api.get('/monitor/agent-activity'),
    refetchInterval: 30_000,
  });
}

// === Audit ===

export function useAuditEntries(params?: { limit?: number; eventType?: string }) {
  const qs = new URLSearchParams();
  if (params?.limit) qs.set('limit', String(params.limit));
  if (params?.eventType && params.eventType !== 'all') qs.set('eventType', params.eventType);
  return useQuery<AuditEntry[]>({
    queryKey: ['audit', params],
    queryFn: () => api.get(`/audit/entries?${qs}`),
  });
}

export interface AuditInsight {
  id: string; severity: 'high' | 'medium' | 'low'; category: string;
  title: string; description: string; recommendation: string;
  affectedUsers: string[]; detectedAt: string; source: string;
}

export function useAuditInsights() {
  return useQuery<{ insights: AuditInsight[]; summary: { totalInsights: number; high: number; medium: number; low: number; lastScanAt: string; scanSources: string[] } }>({
    queryKey: ['audit-insights'],
    queryFn: () => api.get('/audit/insights'),
  });
}

export function useRunAuditScan() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => api.post('/audit/run-scan', {}),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['audit-insights'] }),
  });
}

export function useAuditReviews() {
  return useQuery<{ reviews: any[] }>({
    queryKey: ['audit-reviews'],
    queryFn: () => api.get('/audit/review-queue'),
  });
}

export function useAuditCompliance() {
  return useQuery<any>({
    queryKey: ['audit-compliance'],
    queryFn: () => api.get('/audit/compliance-stats'),
  });
}

export function useAuditAnalyze() {
  return useMutation<any, Error, { entryId: string }>({
    mutationFn: (body) => api.post('/audit/analyze', body),
  });
}

export interface AgentHealthItem {
  agentId: string; agentName: string; employeeName: string; positionName: string;
  status: string; qualityScore: number | null; channels: string[]; skillCount: number;
  requestsToday: number; costToday: number; avgResponseSec: number; toolSuccessRate: number;
  soulVersion: string; lastActive: string; uptime: string;
}

export function useMonitorHealth() {
  return useQuery<{ agents: AgentHealthItem[]; system: Record<string, any> }>({
    queryKey: ['monitor-health'],
    queryFn: () => api.get('/monitor/health'),
    refetchInterval: 30_000,
  });
}

// === Dashboard ===

export interface DashboardData {
  departments: number; positions: number; employees: number;
  agents: number; activeAgents: number; bindings: number;
  sessions: number; totalTurns: number;
}

export function useDashboard() {
  return useQuery<DashboardData>({
    queryKey: ['dashboard'],
    queryFn: () => api.get('/dashboard'),
  });
}


// === Usage (multi-dimension) ===

export function useUsageSummary() {
  return useQuery<{ totalInputTokens: number; totalOutputTokens: number; totalCost: number; totalRequests: number; tenantCount: number }>({
    queryKey: ['usage-summary'],
    queryFn: () => api.get('/usage/summary'),
  });
}

export function useUsageByDepartment() {
  return useQuery<{ department: string; inputTokens: number; outputTokens: number; requests: number; cost: number; agents: number }[]>({
    queryKey: ['usage-by-dept'],
    queryFn: () => api.get('/usage/by-department'),
  });
}

export function useUsageByAgent() {
  return useQuery<{ agentId: string; agentName: string; employeeName: string; positionName: string; inputTokens: number; outputTokens: number; requests: number; cost: number }[]>({
    queryKey: ['usage-by-agent'],
    queryFn: () => api.get('/usage/by-agent'),
  });
}

export function useAgentDailyUsage(agentId: string) {
  return useQuery<{ date: string; inputTokens: number; outputTokens: number; requests: number; cost: number }[]>({
    queryKey: ['agent-daily-usage', agentId],
    queryFn: () => api.get(`/usage/agent/${agentId}`),
    enabled: !!agentId,
  });
}

export function useUsageByModel() {
  return useQuery<{ model: string; inputTokens: number; outputTokens: number; requests: number; cost: number }[]>({
    queryKey: ['usage-by-model'],
    queryFn: () => api.get('/usage/by-model'),
  });
}

export function useUsageBudgets() {
  return useQuery<{ department: string; budget: number; used: number; projected: number; status: string }[]>({
    queryKey: ['usage-budgets'],
    queryFn: () => api.get('/usage/budgets'),
  });
}

export function useMyBudget(empId: string) {
  return useQuery<{ budget: number; used: number; remaining: number; source: string; projected: number }>({
    queryKey: ['my-budget', empId],
    queryFn: () => api.get(`/usage/my-budget?emp_id=${empId}`),
    enabled: !!empId,
  });
}

export function useDepartmentBudget(deptName: string) {
  return useQuery<{ department: string; budget: number; used: number; projected: number; members: any[] }>({
    queryKey: ['dept-budget', deptName],
    queryFn: () => api.get(`/usage/department-budget?department=${encodeURIComponent(deptName)}`),
    enabled: !!deptName,
  });
}

export function useUpdateBudgets() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (data: { global?: number; departments?: Record<string, number>; employees?: Record<string, number> }) =>
      api.put('/usage/budgets', data),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['usage-budgets'] }),
  });
}

// === Settings ===

export function useModelConfig() {
  return useQuery<{
    default: { modelId: string; modelName: string; inputRate: number; outputRate: number };
    fallback: { modelId: string; modelName: string; inputRate: number; outputRate: number };
    positionOverrides: Record<string, any>; employeeOverrides: Record<string, any>;
    availableModels: { modelId: string; modelName: string; inputRate: number; outputRate: number; enabled: boolean }[];
  }>({
    queryKey: ['model-config'],
    queryFn: () => api.get('/settings/model'),
  });
}

export function useSecurityConfig() {
  return useQuery<{
    alwaysBlocked: string[]; piiDetection: { enabled: boolean; mode: string };
    dataSovereignty: { enabled: boolean; region: string }; conversationRetention: { days: number };
    dockerSandbox: boolean; fastPathRouting: boolean; verboseAudit: boolean;
  }>({
    queryKey: ['security-config'],
    queryFn: () => api.get('/settings/security'),
  });
}

export function useUpdateModelConfig() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (data: Record<string, any>) => api.put('/settings/model/default', data),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['model-config'] }),
  });
}

export function useUpdateFallbackModel() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (data: Record<string, any>) => api.put('/settings/model/fallback', data),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['model-config'] }),
  });
}

export function useSetPositionModel() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ posId, ...data }: { posId: string } & Record<string, any>) => api.put(`/settings/model/position/${posId}`, data),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['model-config'] }),
  });
}

export function useRemovePositionModel() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (posId: string) => api.del(`/settings/model/position/${posId}`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['model-config'] }),
  });
}

export function useEnableModel() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (data: Record<string, any>) => api.put('/settings/model/default', data),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['model-config'] }),
  });
}

export function useUpdateSecurityConfig() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (data: Record<string, any>) => api.put('/settings/security', data),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['security-config'] }),
  });
}

export function useServiceStatus() {
  return useQuery<{
    gateway: { status: string; port: number; uptime: string; requestsToday: number };
    auth_agent: { status: string; uptime: string; approvalsProcessed: number };
    bedrock: { status: string; region: string; latencyMs: number; vpcEndpoint: boolean };
    dynamodb: { status: string; table: string; itemCount: number };
    s3: { status: string; bucket: string };
  }>({
    queryKey: ['service-status'],
    queryFn: () => api.get('/settings/services'),
  });
}

// === SOUL save ===

export function useSaveSoul() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ agentId, layer, content }: { agentId: string; layer: string; content: string }) =>
      api.put<{ saved: boolean; layer: string; version: number }>(`/agents/${agentId}/soul`, { layer, content }),
    onSuccess: (_, vars) => {
      qc.invalidateQueries({ queryKey: ['agent-soul', vars.agentId] });
      qc.invalidateQueries({ queryKey: ['agent', vars.agentId] });
    },
  });
}

// === Workspace file operations ===

export function useWorkspaceTree(agentId: string) {
  return useQuery({
    queryKey: ['workspace-tree', agentId],
    queryFn: () => api.get(`/workspace/tree?agent_id=${agentId}`),
    enabled: !!agentId,
  });
}

export function useWorkspaceFile(key: string) {
  return useQuery<{ key: string; content: string; size: number }>({
    queryKey: ['workspace-file', key],
    queryFn: () => api.get(`/workspace/file?key=${encodeURIComponent(key)}`),
    enabled: !!key,
  });
}

export function useSaveWorkspaceFile() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ key, content }: { key: string; content: string }) =>
      api.put<{ key: string; saved: boolean }>('/workspace/file', { key, content }),
    onSuccess: (_, vars) => qc.invalidateQueries({ queryKey: ['workspace-file', vars.key] }),
  });
}


// === Skills ===

export interface SkillManifest {
  id: string; name: string; version: string; description: string; author: string;
  layer: 1 | 2 | 3; category: string; scope: string;
  status?: string; bundleSizeMB?: number; approvalRequired?: boolean; approvalNote?: string;
  requires: { env: string[]; tools: string[] };
  permissions: { allowedRoles: string[]; blockedRoles: string[] };
}

export interface SkillApiKey {
  id: string; skillName: string; envVar: string; ssmPath: string;
  status: string; lastRotated: string; createdBy: string;
  awsService?: string; note?: string;
}

export function useSkills() {
  return useQuery<SkillManifest[]>({
    queryKey: ['skills'],
    queryFn: () => api.get('/skills'),
  });
}

export function useSkillKeys() {
  return useQuery<SkillApiKey[]>({
    queryKey: ['skill-keys'],
    queryFn: () => api.get('/skills/keys/all'),
  });
}

export function useAssignSkill() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ skillName, positionId }: { skillName: string; positionId: string }) =>
      api.post<{ assigned: boolean; agentsPropagated: number }>(`/skills/${skillName}/assign`, { positionId }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['skills'] });
      qc.invalidateQueries({ queryKey: ['positions'] });
      qc.invalidateQueries({ queryKey: ['agents'] });
    },
  });
}

export function useUnassignSkill() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ skillName, positionId }: { skillName: string; positionId: string }) =>
      api.del<{ unassigned: boolean }>(`/skills/${skillName}/assign?positionId=${positionId}`),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['skills'] });
      qc.invalidateQueries({ queryKey: ['positions'] });
      qc.invalidateQueries({ queryKey: ['agents'] });
    },
  });
}


// === Skill Submission + Review ===

export function usePendingSkills() {
  return useQuery<any[]>({
    queryKey: ['pending-skills'],
    queryFn: () => api.get('/tools-skills/pending'),
  });
}

export function useSubmitSkill() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (data: { name: string; description: string; category: string; toolJs: string; setupGuide?: string; requiredEnv?: string[]; requiredTools?: string[] }) =>
      api.post<{ submitted: boolean; skillName: string }>('/portal/skills/submit', data),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['pending-skills'] }); qc.invalidateQueries({ queryKey: ['skills'] }); },
  });
}

export function useRequestSkill() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ skillName, reason }: { skillName: string; reason?: string }) =>
      api.post<{ requested: boolean }>(`/portal/skills/${skillName}/request`, { reason }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['approvals'] }),
  });
}

export function useReviewSkill() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ skillName, action, reason }: { skillName: string; action: 'approve' | 'reject'; reason?: string }) =>
      api.post<{ action: string }>(`/tools-skills/${skillName}/review`, { action, reason }),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['pending-skills'] }); qc.invalidateQueries({ queryKey: ['skills'] }); },
  });
}

export function useSkillCode(skillName: string, source: string = 'shared') {
  return useQuery<{ toolJs: string; manifest: any; setupGuide: string }>({
    queryKey: ['skill-code', skillName, source],
    queryFn: () => api.get(`/tools-skills/${skillName}/code?source=${source}`),
    enabled: !!skillName,
  });
}

export function useApproveSkillInstall() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ skillName, approvalId }: { skillName: string; approvalId: string }) =>
      api.post<{ approved: boolean }>(`/tools-skills/${skillName}/approve-install`, { approvalId }),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['approvals'] }); qc.invalidateQueries({ queryKey: ['employees'] }); },
  });
}

// === Approvals ===

export interface ApprovalRequest {
  id: string; tenant: string; tenantId: string; tool: string; reason: string;
  risk: 'high' | 'medium' | 'low'; timestamp: string; status: 'pending' | 'approved' | 'denied';
  reviewer?: string; resolvedAt?: string;
}

export function useApprovals() {
  return useQuery<{ pending: ApprovalRequest[]; resolved: ApprovalRequest[] }>({
    queryKey: ['approvals'],
    queryFn: () => api.get('/approvals'),
  });
}

export function useApproveRequest() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => api.post(`/approvals/${id}/approve`, {}),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['approvals'] }),
  });
}

export function useDenyRequest() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => api.post(`/approvals/${id}/deny`, {}),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['approvals'] }),
  });
}


// === Knowledge Base ===

export interface KnowledgeBaseItem {
  id: string; name: string; scope: string; scopeName: string;
  docCount: number; sizeMB: number; sizeBytes: number;
  status: string; lastUpdated: string; accessibleBy: string;
  s3Prefix: string;
  files?: { name: string; size: number; key: string }[];
}

export function useKnowledgeBases() {
  return useQuery<KnowledgeBaseItem[]>({
    queryKey: ['knowledge'],
    queryFn: () => api.get('/knowledge'),
  });
}

export function useKnowledgeSearch(query: string) {
  return useQuery<{ doc: string; kb: string; kbName: string; score: number; snippet: string; key: string }[]>({
    queryKey: ['knowledge-search', query],
    queryFn: () => api.get(`/knowledge/search?query=${encodeURIComponent(query)}`),
    enabled: !!query,
  });
}

export function useUploadKnowledgeDoc() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (data: { kbId: string; filename: string; content: string }) =>
      api.post<{ key: string; saved: boolean }>('/knowledge/upload', data),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['knowledge'] }),
  });
}

// === Playground ===

export function usePlaygroundProfiles() {
  return useQuery<Record<string, { role: string; tools: string[]; planA: string; planE: string }>>({
    queryKey: ['playground-profiles'],
    queryFn: () => api.get('/playground/profiles'),
  });
}

export function usePlaygroundPipeline(empId: string) {
  return useQuery<any>({
    queryKey: ['playground-pipeline', empId],
    queryFn: () => api.get(`/playground/pipeline/${empId}`),
    enabled: !!empId,
  });
}

export function usePlaygroundEvents(tenantId: string, seconds: number = 60) {
  return useQuery<{ events: any[]; count: number }>({
    queryKey: ['playground-events', tenantId, seconds],
    queryFn: () => api.get(`/playground/events?tenant_id=${tenantId}&seconds=${seconds}`),
    enabled: !!tenantId,
    refetchInterval: 5_000,
  });
}

export function usePlaygroundSend() {
  return useMutation({
    mutationFn: (data: { tenant_id: string; message: string; mode?: string }) =>
      api.post<{ response: string; tenant_id: string; profile: Record<string, unknown>; plan_a: string; plan_e: string; source?: string }>('/playground/send', data),
  });
}


// === Routing Rules ===

export interface RoutingRule {
  id: string; priority: number; name: string;
  condition: Record<string, string>; action: string;
  agentId?: string; description: string;
}

export function useRoutingRules() {
  return useQuery<RoutingRule[]>({
    queryKey: ['routing-rules'],
    queryFn: () => api.get('/routing/rules'),
  });
}

// === IM User Mappings ===

export interface UserMapping {
  channel: string;
  channelUserId: string;
  employeeId: string;
  ssmPath?: string;
}

export function useUserMappings() {
  return useQuery<UserMapping[]>({
    queryKey: ['user-mappings'],
    queryFn: () => api.get('/bindings/user-mappings'),
  });
}

export function useCreateUserMapping() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (data: { channel: string; channelUserId: string; employeeId: string }) =>
      api.post<{ saved: boolean }>('/bindings/user-mappings', data),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['user-mappings'] }),
  });
}

export function useDeleteUserMapping() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (data: { channel: string; channelUserId: string }) =>
      api.del<{ deleted: boolean }>(`/bindings/user-mappings?channel=${data.channel}&channelUserId=${data.channelUserId}`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['user-mappings'] }),
  });
}

export function useApprovePairing() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (data: { channel: string; pairingCode: string; employeeId: string; channelUserId: string; pairingUserId?: string }) =>
      api.post<{ approved: boolean; output?: string; error?: string; mappingWritten?: boolean }>('/bindings/pairing-approve', data),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['user-mappings'] });
      qc.invalidateQueries({ queryKey: ['audit'] });
    },
  });
}

// =========================================================================
// Security Center
// =========================================================================

export function useGlobalSoul() {
  return useQuery<{ content: string; key: string }>({
    queryKey: ['security-global-soul'],
    queryFn: () => api.get('/security/global-soul'),
  });
}

export function useUpdateGlobalSoul() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (content: string) => api.put('/security/global-soul', { content }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['security-global-soul'] }),
  });
}

export function usePositionSoul(posId: string) {
  return useQuery<{ content: string; key: string }>({
    queryKey: ['security-position-soul', posId],
    queryFn: () => api.get(`/security/positions/${posId}/soul`),
    enabled: !!posId,
  });
}

export function useUpdatePositionSoul() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ posId, content }: { posId: string; content: string }) =>
      api.put(`/security/positions/${posId}/soul`, { content }),
    onSuccess: (_d, v) => qc.invalidateQueries({ queryKey: ['security-position-soul', v.posId] }),
  });
}

export function usePositionTools(posId: string) {
  return useQuery<{ profile: string; tools: string[] }>({
    queryKey: ['security-position-tools', posId],
    queryFn: () => api.get(`/security/positions/${posId}/tools`),
    enabled: !!posId,
  });
}

export function useUpdatePositionTools() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ posId, profile, tools }: { posId: string; profile: string; tools: string[] }) =>
      api.put(`/security/positions/${posId}/tools`, { profile, tools }),
    onSuccess: (_d, v) => qc.invalidateQueries({ queryKey: ['security-position-tools', v.posId] }),
  });
}

export interface SecurityRuntime {
  id: string; name: string; status: string;
  containerUri: string; roleArn: string; model: string;
  idleTimeoutSec: number; maxLifetimeSec: number;
  guardrailId?: string; guardrailVersion?: string;
  createdAt: string; version: string;
}

export interface Guardrail {
  id: string; name: string; status: string; version: string; updatedAt: string;
}

export interface GuardrailEvent {
  id: string; timestamp: string; actorName: string; actorId: string;
  guardrailId: string; guardrailVersion: string; guardrailSource: string;
  guardrailPolicy: string; detail: string; status: string;
}

export function useSecurityRuntimes() {
  return useQuery<{ runtimes: SecurityRuntime[]; error?: string }>({
    queryKey: ['security-runtimes'],
    queryFn: () => api.get('/security/runtimes'),
    staleTime: 30_000,
  });
}

export function useUpdateRuntimeLifecycle() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ runtimeId, idleTimeoutSec, maxLifetimeSec }: { runtimeId: string; idleTimeoutSec: number; maxLifetimeSec: number }) =>
      api.put(`/security/runtimes/${runtimeId}/lifecycle`, { idleTimeoutSec, maxLifetimeSec }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['security-runtimes'] }),
  });
}

export function useInfrastructure() {
  return useQuery<{ iamRoles: any[]; ecrImages: any[]; securityGroups: any[]; vpcs: any[]; subnets: any[] }>({
    queryKey: ['security-infrastructure'],
    queryFn: () => api.get('/security/infrastructure'),
    staleTime: 60_000,
  });
}

// =========================================================================
// Settings — Admin Account, Admin Assistant, System Stats
// =========================================================================

export function useChangeAdminPassword() {
  return useMutation({
    mutationFn: (newPassword: string) => api.put('/settings/admin-password', { newPassword }),
  });
}

export function useChangePassword() {
  return useMutation({
    mutationFn: (data: { currentPassword: string; newPassword: string }) =>
      api.post<{ token: string; changed: boolean }>('/auth/change-password', data),
  });
}

export function useAdminAssistant() {
  return useQuery<{ model: string; systemPrompt: string; maxHistoryTurns: number; maxTokens: number }>({
    queryKey: ['admin-assistant-config'],
    queryFn: () => api.get('/settings/admin-assistant'),
  });
}

export function useUpdateAdminAssistant() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (data: { model?: string; systemPrompt?: string; maxHistoryTurns?: number; maxTokens?: number }) =>
      api.put('/settings/admin-assistant', data),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['admin-assistant-config'] }),
  });
}

export function usePlatformAccess() {
  return useQuery<{ ssmCommand: string; instanceId: string; region: string; portForwardCommand: string }>({
    queryKey: ['platform-access'],
    queryFn: () => api.get('/settings/platform-access'),
  });
}

export function usePlatformLogs(service?: string, lines?: number) {
  const qs = new URLSearchParams();
  if (service) qs.set('service', service);
  if (lines) qs.set('lines', String(lines));
  return useQuery<{ logs: string; service: string; lines: number }>({
    queryKey: ['platform-logs', service, lines],
    queryFn: () => api.get(`/settings/platform-logs?${qs}`),
    enabled: false,
  });
}

export function useAdminHistory() {
  return useQuery<{ history: any[] }>({
    queryKey: ['admin-history'],
    queryFn: () => api.get('/settings/admin-assistant/history'),
  });
}

export function useClearAdminHistory() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => api.del('/settings/admin-assistant/history'),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['admin-history'] }),
  });
}

export function useRestartService() {
  return useMutation({
    mutationFn: (service: string) => api.post('/settings/restart-service', { service }),
  });
}

export function useIMChannelHealth() {
  return useQuery<{ lastActivity: Record<string, string>; messagesLast24h: Record<string, number> }>({
    queryKey: ['im-channel-health'],
    queryFn: () => api.get('/admin/im-channels/health'),
    refetchInterval: 60_000,
  });
}

export function useIMEnrollment() {
  return useQuery<{ totalWithAgent: number; bound: number; unbound: number; unboundEmployees: any[]; multiChannel: any[] }>({
    queryKey: ['im-enrollment'],
    queryFn: () => api.get('/admin/im-channels/enrollment'),
  });
}

export function useSystemStats() {
  return useQuery<{
    cpu: { pct: number };
    memory: { total: number; used: number; free: number; pct: number };
    disk: { total: number; used: number; free: number; pct: number };
    ports: { port: number; name: string; listening: boolean }[];
  }>({
    queryKey: ['system-stats'],
    queryFn: () => api.get('/settings/system-stats'),
    refetchInterval: 10_000,
  });
}

// ── Fine-grained security resource hooks ─────────────────────────────────────

export interface EcrImage {
  uri: string; repo: string; tag: string;
  digest: string; sizeBytes: number; pushedAt: string;
}

export function useEcrImages() {
  return useQuery<{ images: EcrImage[]; error?: string }>({
    queryKey: ['ecr-images'],
    queryFn: () => api.get('/security/ecr-images'),
    staleTime: 60_000,
  });
}

export interface IamRole {
  name: string; arn: string; relevant: boolean; created: string;
}

export function useIamRoles() {
  return useQuery<{ roles: IamRole[]; error?: string }>({
    queryKey: ['iam-roles'],
    queryFn: () => api.get('/security/iam-roles'),
    staleTime: 120_000,
  });
}

export interface VpcResource {
  vpcs: { id: string; name: string; cidr: string; isDefault: boolean }[];
  subnets: { id: string; name: string; vpcId: string; az: string; cidr: string; public: boolean }[];
  securityGroups: { id: string; name: string; description: string; vpcId: string; relevant: boolean }[];
}

export function useVpcResources() {
  return useQuery<VpcResource>({
    queryKey: ['vpc-resources'],
    queryFn: () => api.get('/security/vpc-resources'),
    staleTime: 120_000,
  });
}

export function useUpdateRuntimeConfig() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (data: {
      runtimeId: string; containerUri?: string; roleArn?: string;
      networkMode?: string; securityGroupIds?: string[]; subnetIds?: string[];
      modelId?: string; idleTimeoutSec?: number; maxLifetimeSec?: number;
      guardrailId?: string; guardrailVersion?: string;
    }) => api.put(`/security/runtimes/${data.runtimeId}/config`, data),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['security-runtimes'] }),
  });
}

export function useGuardrails() {
  return useQuery<{ guardrails: Guardrail[]; error?: string }>({
    queryKey: ['guardrails'],
    queryFn: () => api.get('/security/guardrails'),
    staleTime: 60_000,
  });
}

export function useGuardrailEvents(limit = 50) {
  return useQuery<{ events: GuardrailEvent[]; error?: string }>({
    queryKey: ['guardrail-events', limit],
    queryFn: () => api.get(`/audit/guardrail-events?limit=${limit}`),
    staleTime: 15_000,
    refetchInterval: 30_000,
  });
}

export function useCreateRuntime() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (data: {
      name: string; containerUri: string; roleArn: string;
      networkMode: string; securityGroupIds: string[]; subnetIds: string[];
      modelId: string; idleTimeoutSec: number; maxLifetimeSec: number;
    }) => api.post('/security/runtimes/create', data),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['security-runtimes'] }),
  });
}

// ── Position → Runtime mapping ────────────────────────────────────────────────

export function usePositionRuntimeMap() {
  return useQuery<{ map: Record<string, string> }>({
    queryKey: ['position-runtime-map'],
    queryFn: () => api.get('/security/position-runtime-map'),
    staleTime: 30_000,
  });
}

export function useSetPositionRuntime() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ posId, runtimeId }: { posId: string; runtimeId: string }) =>
      api.put(`/security/positions/${posId}/runtime`, { runtimeId }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['position-runtime-map'] });
      qc.invalidateQueries({ queryKey: ['security-runtimes'] });
    },
  });
}

export function useDeletePositionRuntime() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (posId: string) => api.del(`/security/positions/${posId}/runtime`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['position-runtime-map'] }),
  });
}

// ── Employee model override ────────────────────────────────────────────────────

export function useSetEmployeeModel() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ empId, modelId, modelName, inputRate, outputRate, reason }: {
      empId: string; modelId: string; modelName: string;
      inputRate: number; outputRate: number; reason?: string;
    }) => api.put(`/settings/model/employee/${empId}`, { modelId, modelName, inputRate, outputRate, reason }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['model-config'] }),
  });
}

export function useRemoveEmployeeModel() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (empId: string) => api.del(`/settings/model/employee/${empId}`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['model-config'] }),
  });
}

// ── Agent config (compaction, context, language) ───────────────────────────────

export function useAgentConfig() {
  return useQuery<{ positionConfig: Record<string, any>; employeeConfig: Record<string, any> }>({
    queryKey: ['agent-config'],
    queryFn: () => api.get('/settings/agent-config'),
    staleTime: 30_000,
  });
}

export function useSetPositionAgentConfig() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ posId, config }: { posId: string; config: Record<string, any> }) =>
      api.put(`/settings/agent-config/position/${posId}`, config),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['agent-config'] }),
  });
}

export function useSetEmployeeAgentConfig() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ empId, config }: { empId: string; config: Record<string, any> }) =>
      api.put(`/settings/agent-config/employee/${empId}`, config),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['agent-config'] }),
  });
}

// ── KB Assignments ─────────────────────────────────────────────────────────────

export function useKBAssignments() {
  return useQuery<{ positionKBs: Record<string, string[]>; employeeKBs: Record<string, string[]> }>({
    queryKey: ['kb-assignments'],
    queryFn: () => api.get('/settings/kb-assignments'),
    staleTime: 30_000,
  });
}

export function useSetPositionKBs() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ posId, kbIds }: { posId: string; kbIds: string[] }) =>
      api.put(`/settings/kb-assignments/position/${posId}`, { kbIds }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['kb-assignments'] }),
  });
}

export function useSetEmployeeKBs() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ empId, kbIds }: { empId: string; kbIds: string[] }) =>
      api.put(`/settings/kb-assignments/employee/${empId}`, { kbIds }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['kb-assignments'] }),
  });
}

// ── Always-On Agent Management ───────────────────────────────────────────

export function useAlwaysOnStatus(empId: string) {
  return useQuery({
    queryKey: ['always-on-status', empId],
    queryFn: () => api.get(`/agents/${empId}/always-on/status`),
    enabled: !!empId,
    refetchInterval: 30000,
  });
}

export function useAlwaysOnChannels(empId: string) {
  return useQuery({
    queryKey: ['always-on-channels', empId],
    queryFn: () => api.get(`/agents/${empId}/always-on/channels`),
    enabled: !!empId,
  });
}

export function useEnableAlwaysOn() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ empId, enable }: { empId: string; enable: boolean }) =>
      api.put(`/agents/${empId}/always-on`, { enable }),
    onSuccess: (_, { empId }) => {
      qc.invalidateQueries({ queryKey: ['always-on-status', empId] });
      qc.invalidateQueries({ queryKey: ['employees'] });
      qc.invalidateQueries({ queryKey: ['agents'] });
    },
  });
}

export function useDisconnectChannel() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ empId, channel }: { empId: string; channel: string }) =>
      api.del(`/agents/${empId}/always-on/channels/${channel}`),
    onSuccess: (_, { empId }) => {
      qc.invalidateQueries({ queryKey: ['always-on-channels', empId] });
    },
  });
}

export function useFargateOverview() {
  return useQuery({
    queryKey: ['fargate-overview'],
    queryFn: () => api.get('/security/fargate/overview'),
    refetchInterval: 30000,
  });
}

export function useWorkspaceFiles(empId: string, agentType: string = 'serverless') {
  return useQuery({
    queryKey: ['workspace-files', empId, agentType],
    queryFn: () => api.get(`/workspace/${empId}/files?agent_type=${agentType}`),
    enabled: !!empId,
  });
}

export function useSetIMPlatforms() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ posId, platforms }: { posId: string; platforms: string[] }) =>
      api.put(`/security/positions/${posId}/im-platforms`, { allowedIMPlatforms: platforms }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['positions'] }),
  });
}
