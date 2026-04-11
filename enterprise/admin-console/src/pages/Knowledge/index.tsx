import { useState } from 'react';
import ReactMarkdown from 'react-markdown';
import { BookOpen, Search, FolderOpen, Globe, Building2, FileText, Plus, Eye, Link2, X, Code } from 'lucide-react';
import { Card, StatCard, Badge, Button, PageHeader, Table, Modal, Input, Select, Tabs, Textarea } from '../../components/ui';
import { useKnowledgeBases, useUploadKnowledgeDoc, usePositions, useEmployees, useKBAssignments, useSetPositionKBs, useSetEmployeeKBs } from '../../hooks/useApi';
import type { KnowledgeBaseItem } from '../../hooks/useApi';
import { api } from '../../api/client';

export default function KnowledgeBase_() {
  const { data: kbs = [], isLoading } = useKnowledgeBases();
  const { data: positions = [] } = usePositions();
  const { data: employees = [] } = useEmployees();
  const { data: kbAssignData } = useKBAssignments();
  const setPositionKBs = useSetPositionKBs();
  const setEmployeeKBs = useSetEmployeeKBs();
  const uploadMut = useUploadKnowledgeDoc();
  const [activeTab, setActiveTab] = useState('all');
  const [assignTarget, setAssignTarget] = useState<{ type: 'pos'|'emp'; id: string; name: string } | null>(null);
  const [assignDraft, setAssignDraft] = useState<string[]>([]);
  const kbAssign = kbAssignData || { positionKBs: {}, employeeKBs: {} };
  const [showUpload, setShowUpload] = useState(false);
  const [showSearch, setShowSearch] = useState(false);
  const [showFile, setShowFile] = useState<{ name: string; content: string } | null>(null);
  const [fileViewRaw, setFileViewRaw] = useState(false);
  const [selectedKb, setSelectedKb] = useState<KnowledgeBaseItem | null>(null);
  const [searchQuery, setSearchQuery] = useState('');
  const [searchResults, setSearchResults] = useState<any[]>([]);
  const [searched, setSearched] = useState(false);
  const [uploadKbId, setUploadKbId] = useState('');
  const [uploadFilename, setUploadFilename] = useState('');
  const [uploadContent, setUploadContent] = useState('');

  const globalKBs = kbs.filter(kb => kb.scope === 'global');
  const deptKBs = kbs.filter(kb => kb.scope === 'department');
  const totalDocs = kbs.reduce((s, kb) => s + kb.docCount, 0);
  const totalSize = kbs.reduce((s, kb) => s + (kb.sizeBytes || 0), 0);

  const tabData: Record<string, KnowledgeBaseItem[]> = { all: kbs, global: globalKBs, department: deptKBs };

  const handleSearch = async () => {
    setSearched(true);
    try {
      const resp = await api.get<any[]>(`/knowledge/search?query=${encodeURIComponent(searchQuery)}`);
      setSearchResults(resp);
    } catch { setSearchResults([]); }
  };

  const handleViewFile = async (key: string, name: string) => {
    try {
      const resp = await api.get<{ content: string }>(`/workspace/file?key=${encodeURIComponent(key)}`);
      setShowFile({ name, content: resp.content });
    } catch { setShowFile({ name, content: 'Failed to load file' }); }
  };

  const [uploadError, setUploadError] = useState('');
  const handleUpload = () => {
    if (uploadKbId && uploadFilename && uploadContent) {
      setUploadError('');
      uploadMut.mutate({ kbId: uploadKbId, filename: uploadFilename, content: uploadContent }, {
        onSuccess: () => { setShowUpload(false); setUploadKbId(''); setUploadFilename(''); setUploadContent(''); setUploadError(''); },
        onError: (err: any) => {
          const status = err?.response?.status || err?.status;
          if (status === 413) {
            setUploadError('Document too large (max 1MB). For larger documents, use Bedrock Knowledge Base skill with RAG.');
          } else {
            setUploadError(err?.response?.data?.detail || err?.message || 'Upload failed');
          }
        },
      });
    }
  };

  return (
    <div>
      <PageHeader
        title="Knowledge Base"
        description="Markdown documents in S3 — synced to agent workspace/knowledge/ at runtime"
        actions={
          <div className="flex gap-2">
            <Button variant="default" onClick={() => setShowSearch(true)}><Search size={16} /> Search</Button>
            <Button variant="primary" onClick={() => setShowUpload(true)}><Plus size={16} /> New Document</Button>
          </div>
        }
      />

      <div className="grid grid-cols-2 gap-4 sm:grid-cols-4 mb-6">
        <StatCard title="Knowledge Bases" value={kbs.length} icon={<BookOpen size={22} />} color="primary" />
        <StatCard title="Documents" value={totalDocs} icon={<FileText size={22} />} color="info" />
        <StatCard title="Total Size" value={totalSize > 0 ? `${(totalSize / 1024).toFixed(1)} KB` : '0'} icon={<FolderOpen size={22} />} color="success" />
        <StatCard title="Format" value="Markdown" icon={<FileText size={22} />} color="cyan" />
      </div>

      <Card className="mb-6">
        <Tabs
          tabs={[
            { id: 'all', label: 'All', count: kbs.length },
            { id: 'global', label: 'Organization', count: globalKBs.length },
            { id: 'department', label: 'Department', count: deptKBs.length },
            { id: 'assignments', label: 'Assignments' },
          ]}
          activeTab={activeTab}
          onChange={setActiveTab}
        />

        {/* KB Assignments tab */}
        {activeTab === 'assignments' && (
          <div className="mt-4 space-y-6">
            <div className="rounded-xl bg-info/5 border border-info/20 px-4 py-3 text-xs text-info">
              Assign knowledge bases to positions or individual employees. Agents download and read these documents at session start, enabling them to answer questions using your company's knowledge.
            </div>

            {/* Per-Position */}
            <div>
              <h3 className="text-sm font-semibold text-text-primary mb-3 flex items-center gap-2">
                <Globe size={15} className="text-primary" /> By Position
              </h3>
              <div className="space-y-2">
                {positions.map(pos => {
                  const assigned: string[] = (kbAssign.positionKBs as any)[pos.id] || [];
                  const assignedKBs = assigned.map(id => kbs.find(k => k.id === id)).filter(Boolean);
                  return (
                    <div key={pos.id} className="flex items-center gap-3 rounded-xl bg-surface-dim px-4 py-3">
                      <div className="flex-1">
                        <p className="text-sm font-medium text-text-primary">{pos.name}</p>
                        <p className="text-xs text-text-muted">{pos.departmentName}</p>
                        {assignedKBs.length > 0 && (
                          <div className="flex flex-wrap gap-1 mt-1.5">
                            {assignedKBs.map(kb => kb && <Badge key={kb.id} color="info">{kb.name}</Badge>)}
                          </div>
                        )}
                      </div>
                      <Button size="sm" variant={assigned.length > 0 ? 'default' : 'ghost'}
                        onClick={() => { setAssignTarget({ type: 'pos', id: pos.id, name: pos.name }); setAssignDraft(assigned); }}>
                        <Link2 size={12} /> {assigned.length > 0 ? `${assigned.length} KB` : 'Assign'}
                      </Button>
                    </div>
                  );
                })}
              </div>
            </div>

            {/* Per-Employee (show only if they have individual overrides) */}
            {Object.keys(kbAssign.employeeKBs || {}).length > 0 && (
              <div>
                <h3 className="text-sm font-semibold text-text-primary mb-3 flex items-center gap-2">
                  <FileText size={15} className="text-primary" /> Individual Employee Overrides
                </h3>
                <div className="space-y-2">
                  {Object.entries(kbAssign.employeeKBs || {}).map(([empId, kbIds]: [string, any]) => {
                    const emp = employees.find(e => e.id === empId);
                    if (!emp) return null;
                    return (
                      <div key={empId} className="flex items-center gap-3 rounded-xl bg-surface-dim px-4 py-3">
                        <div className="flex-1">
                          <p className="text-sm font-medium">{emp.name}</p>
                          <div className="flex gap-1 mt-1">{(kbIds as string[]).map(id => { const kb = kbs.find(k => k.id === id); return kb ? <Badge key={id} color="success">{kb.name}</Badge> : null; })}</div>
                        </div>
                        <Button size="sm" variant="ghost"
                          onClick={() => setPositionKBs.mutate({ posId: empId, kbIds: [] })}>
                          <X size={12} /> Clear
                        </Button>
                      </div>
                    );
                  })}
                </div>
              </div>
            )}

            {/* Add individual employee override */}
            <div className="text-center pt-2">
              <p className="text-xs text-text-muted">To add an individual employee override, select an employee below:</p>
              <div className="flex gap-2 justify-center mt-2">
                <select className="rounded-xl border border-dark-border/60 bg-surface-dim px-3 py-2 text-sm text-text-primary focus:outline-none"
                  onChange={e => e.target.value && setAssignTarget({ type: 'emp', id: e.target.value, name: employees.find(emp => emp.id === e.target.value)?.name || e.target.value })}>
                  <option value="">Select employee...</option>
                  {employees.map(e => <option key={e.id} value={e.id}>{e.name} — {e.positionName}</option>)}
                </select>
              </div>
            </div>
          </div>
        )}

        {/* KB list tabs */}
        {activeTab !== 'assignments' && (
        <div className="mt-4">
          <Table
            columns={[
              { key: 'name', label: 'Knowledge Base', render: (kb: KnowledgeBaseItem) => (
                <button onClick={() => setSelectedKb(kb)} className="flex items-center gap-2 text-primary-light hover:underline">
                  {kb.scope === 'global' ? <Globe size={14} /> : <Building2 size={14} />}
                  <div><p className="font-medium text-left">{kb.name}</p><p className="text-xs text-text-muted">{kb.scopeName}</p></div>
                </button>
              )},
              { key: 'docs', label: 'Documents', render: (kb: KnowledgeBaseItem) => kb.docCount },
              { key: 'size', label: 'Size', render: (kb: KnowledgeBaseItem) => kb.sizeBytes > 0 ? `${(kb.sizeBytes / 1024).toFixed(1)} KB` : '—' },
              { key: 'status', label: 'Status', render: (kb: KnowledgeBaseItem) => (
                <Badge color={kb.status === 'indexed' ? 'success' : 'warning'} dot>{kb.status}</Badge>
              )},
              { key: 'access', label: 'Access', render: (kb: KnowledgeBaseItem) => <span className="text-xs text-text-muted">{kb.accessibleBy}</span> },
            ]}
            data={tabData[activeTab] || []}
          />
        </div>
        )}
      </Card>

      {/* KB Assignment Modal */}
      {assignTarget && (
        <Modal open={true} onClose={() => setAssignTarget(null)}
          title={`Assign Knowledge Bases — ${assignTarget.name}`}
          footer={
            <div className="flex justify-end gap-3">
              <Button variant="default" onClick={() => setAssignTarget(null)}>Cancel</Button>
              <Button variant="primary" onClick={() => {
                if (assignTarget.type === 'pos') setPositionKBs.mutate({ posId: assignTarget.id, kbIds: assignDraft });
                else setEmployeeKBs.mutate({ empId: assignTarget.id, kbIds: assignDraft });
                setAssignTarget(null);
              }}>Save</Button>
            </div>
          }>
          <p className="text-xs text-text-muted mb-4">
            Agents in this {assignTarget.type === 'pos' ? 'position' : 'account'} will download these documents into their workspace at session start.
          </p>
          <div className="space-y-2">
            {kbs.length === 0 ? (
              <p className="text-sm text-text-muted text-center py-6">No knowledge bases yet. Create one first.</p>
            ) : kbs.map(kb => {
              const checked = assignDraft.includes(kb.id);
              return (
                <label key={kb.id} className={`flex items-start gap-3 rounded-xl px-4 py-3 cursor-pointer transition-colors ${checked ? 'bg-primary/10 border border-primary/30' : 'bg-surface-dim hover:bg-dark-hover'}`}>
                  <input type="checkbox" checked={checked} onChange={() => setAssignDraft(d => checked ? d.filter(x => x !== kb.id) : [...d, kb.id])} className="accent-primary mt-0.5" />
                  <div>
                    <p className="text-sm font-medium text-text-primary">{kb.name}</p>
                    <p className="text-xs text-text-muted">{kb.accessibleBy} · {kb.docCount} docs</p>
                  </div>
                </label>
              );
            })}
          </div>
        </Modal>
      )}

      {/* KB Detail Drawer */}
      {selectedKb && (
        <Card className="mb-6">
          <div className="flex items-center justify-between mb-4">
            <div>
              <h3 className="text-lg font-semibold text-text-primary">{selectedKb.name}</h3>
              <p className="text-sm text-text-muted">{selectedKb.scopeName} · {selectedKb.docCount} documents · S3: {selectedKb.s3Prefix}</p>
            </div>
            <Button variant="default" size="sm" onClick={() => setSelectedKb(null)}>Close</Button>
          </div>
          <div className="space-y-2">
            {(selectedKb.files || []).map(f => (
              <div key={f.key} className="flex items-center justify-between rounded-lg bg-dark-bg px-4 py-2.5">
                <div className="flex items-center gap-2">
                  <FileText size={14} className="text-text-muted" />
                  <span className="text-sm font-medium">{f.name}</span>
                  <span className="text-xs text-text-muted">{(f.size / 1024).toFixed(1)} KB</span>
                </div>
                <Button variant="ghost" size="sm" onClick={() => handleViewFile(f.key, f.name)}><Eye size={14} /> View</Button>
              </div>
            ))}
            {(!selectedKb.files || selectedKb.files.length === 0) && (
              <p className="text-sm text-text-muted text-center py-4">No documents yet</p>
            )}
          </div>
        </Card>
      )}

      {/* File Viewer — rendered Markdown with raw toggle */}
      <Modal open={!!showFile} onClose={() => { setShowFile(null); setFileViewRaw(false); }}
        title={showFile?.name || ''} size="xl">
        {showFile && (
          <div>
            <div className="flex items-center justify-between mb-3">
              <span className="text-xs text-text-muted">{showFile.content.length.toLocaleString()} chars</span>
              <button
                onClick={() => setFileViewRaw(r => !r)}
                className="flex items-center gap-1.5 text-xs text-text-muted hover:text-primary transition-colors border border-dark-border rounded px-2 py-1"
              >
                <Code size={12} />
                {fileViewRaw ? 'Rendered' : 'Raw'}
              </button>
            </div>
            {fileViewRaw ? (
              <pre className="rounded-lg bg-dark-bg border border-dark-border p-4 text-sm text-text-secondary whitespace-pre-wrap font-mono max-h-[60vh] overflow-y-auto">
                {showFile.content}
              </pre>
            ) : (
              <div className="rounded-lg bg-dark-bg border border-dark-border p-5 max-h-[60vh] overflow-y-auto prose prose-invert prose-sm max-w-none
                prose-headings:text-text-primary prose-headings:font-semibold
                prose-p:text-text-secondary prose-p:leading-relaxed
                prose-strong:text-text-primary
                prose-code:bg-dark-card prose-code:px-1 prose-code:rounded prose-code:text-xs prose-code:text-primary
                prose-pre:bg-dark-card prose-pre:border prose-pre:border-dark-border
                prose-table:text-sm prose-th:text-text-primary prose-td:text-text-secondary
                prose-a:text-primary prose-blockquote:border-primary/40 prose-blockquote:text-text-muted
                prose-ul:text-text-secondary prose-ol:text-text-secondary prose-li:marker:text-text-muted">
                <ReactMarkdown>{showFile.content}</ReactMarkdown>
              </div>
            )}
          </div>
        )}
      </Modal>

      {/* Upload Modal */}
      <Modal open={showUpload} onClose={() => setShowUpload(false)} title="New Knowledge Document" size="md"
        footer={<div className="flex justify-end gap-3">
          <Button variant="default" onClick={() => setShowUpload(false)}>Cancel</Button>
          <Button variant="primary" onClick={handleUpload} disabled={!uploadKbId || !uploadFilename || !uploadContent || uploadMut.isPending}>
            {uploadMut.isPending ? 'Uploading...' : 'Upload'}
          </Button>
        </div>}
      >
        <div className="space-y-4">
          <Select label="Knowledge Base" value={uploadKbId} onChange={setUploadKbId}
            options={kbs.map(kb => ({ label: `${kb.name} (${kb.scopeName})`, value: kb.id }))}
            placeholder="Select target knowledge base" />
          <Input label="Filename" value={uploadFilename} onChange={setUploadFilename}
            placeholder="e.g. api-guidelines.md" description="Must end with .md" />
          <Textarea label="Content (Markdown)" value={uploadContent} onChange={setUploadContent}
            rows={12} placeholder="# Document Title&#10;&#10;Write your knowledge document in Markdown format..." />
          {uploadError && (
            <div className="rounded-lg bg-danger/10 border border-danger/30 px-3 py-2.5 text-sm text-danger">
              {uploadError}
            </div>
          )}
        </div>
      </Modal>

      {/* Search Modal */}
      <Modal open={showSearch} onClose={() => { setShowSearch(false); setSearched(false); setSearchQuery(''); setSearchResults([]); }} title="Knowledge Search" size="lg">
        <div className="space-y-4">
          <p className="text-sm text-text-secondary">Full-text search across all knowledge documents in S3</p>
          <div className="flex gap-2">
            <input value={searchQuery} onChange={e => setSearchQuery(e.target.value)}
              onKeyDown={e => e.key === 'Enter' && searchQuery && handleSearch()}
              placeholder="Enter search query..."
              className="flex-1 rounded-lg border border-dark-border bg-dark-bg px-3 py-2 text-sm text-text-primary placeholder:text-text-muted focus:border-primary focus:outline-none" />
            <Button variant="primary" onClick={handleSearch} disabled={!searchQuery}><Search size={16} /> Search</Button>
          </div>
          {searched && (
            <div className="space-y-3">
              <p className="text-xs text-text-muted">{searchResults.length} results found</p>
              {searchResults.map((r, i) => (
                <div key={i} className="rounded-lg bg-dark-bg border border-dark-border p-3">
                  <div className="flex items-center justify-between mb-1">
                    <div className="flex items-center gap-2">
                      <span className="text-sm font-medium text-text-primary">{r.name || r.doc}</span>
                      <Badge>{r.type === 'kb' ? 'Knowledge Base' : (r.kbName || r.kb)}</Badge>
                    </div>
                    {r.score && <Badge color={r.score > 0.9 ? 'success' : r.score > 0.8 ? 'info' : 'warning'}>Score: {r.score}</Badge>}
                  </div>
                  {r.snippet && <p className="text-xs text-text-secondary mt-1">{r.snippet}</p>}
                </div>
              ))}
              {searchResults.length === 0 && <p className="text-sm text-text-muted text-center py-4">No matches found</p>}
            </div>
          )}
        </div>
      </Modal>
    </div>
  );
}
