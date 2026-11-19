const BASE = '/api/v1';

async function getAuthHeaders(): Promise<Record<string, string>> {
  const headers: Record<string, string> = { 'Content-Type': 'application/json' };
  const getToken = (window as any).__openclaw_getToken;
  if (getToken) {
    const token = await getToken();
    if (token) headers['Authorization'] = `Bearer ${token}`;
  }
  return headers;
}

async function request<T>(path: string, options?: RequestInit): Promise<T> {
  const headers = await getAuthHeaders();
  const res = await fetch(`${BASE}${path}`, {
    headers,
    ...options,
  });
  if (res.status === 401) {
    // Token expired or invalid — redirect to login
    if (window.location.pathname !== '/login') {
      window.location.href = '/login';
    }
    throw new Error('Authentication required');
  }
  if (!res.ok) {
    let data: any = null;
    try { data = await res.json(); } catch { /* non-JSON body */ }
    // FastAPI wraps structured HTTPException payloads in `detail`.
    const payload = data?.detail && typeof data.detail === 'object' ? data.detail : data;
    const detailMessage = typeof data?.detail === 'string' ? data.detail : null;
    const err: any = new Error(payload?.message || detailMessage || `API ${res.status}: ${res.statusText}`);
    err.status = res.status;
    err.response = { status: res.status, data: payload };
    throw err;
  }
  return res.json();
}

export const api = {
  get: <T>(path: string) => request<T>(path),
  post: <T>(path: string, body?: unknown) =>
    request<T>(path, { method: 'POST', body: body != null ? JSON.stringify(body) : undefined }),
  put: <T>(path: string, body: unknown) =>
    request<T>(path, { method: 'PUT', body: JSON.stringify(body) }),
  del: <T>(path: string) => request<T>(path, { method: 'DELETE' }),
};
